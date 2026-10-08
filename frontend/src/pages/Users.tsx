import { useState } from "react";
import { api, type AccessItem, type UserListItem } from "../api";
import { useAuth } from "../auth";
import { Alert, Badge, Card, Loading, PageHeader, SearchBox, useLoad } from "../components/ui";
import { UserAccessModal } from "../components/UserAccess";
import { UserEditor } from "../components/UserEditor";
import { ROLE_LABELS, fmtDateTime, fmtInt, PJ_ACCESS_LABELS } from "../labels";

/* Usuários agrupados por área (08/10/2026): cada área lista quem acessa os CCs dela, com o setor e o CC explícitos;
   Administrador/Controladoria ficam num grupo à parte (veem tudo) e quem ainda não tem CC aparece em destaque. */

const GLOBAL_GROUP = "Acesso total · Administrador e Controladoria";
const NONE_GROUP = "Sem centro de custo (não enxerga nada)";

function fold(s: string) {
  return s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

function Sectors({ items }: { items: AccessItem[] }) {
  const bySector = new Map<string, AccessItem[]>();
  for (const i of items) {
    const k = i.sector ?? "Sem setor";
    bySector.set(k, [...(bySector.get(k) ?? []), i]);
  }
  return (
    <div className="user-sectors">
      {[...bySector.entries()].map(([sector, list]) => (
        <div key={sector}>
          <strong>{sector}</strong>
          <span className="muted small">
            {" "}· {list.map((c) => `${c.code}${c.manager ? " (gestor)" : ""}`).join(", ")}
          </span>
        </div>
      ))}
    </div>
  );
}

export default function Users() {
  const { can } = useAuth();
  const { data, error, reload } = useLoad(() => api<UserListItem[]>("/users"));
  const [editing, setEditing] = useState<UserListItem | null>(null);
  const [form, setForm] = useState<{ user: UserListItem | null } | null>(null); // user null = novo
  const [ok, setOk] = useState<string | null>(null);
  const [q, setQ] = useState("");

  const needle = fold(q.trim());
  const users = (data ?? []).filter((u) => !needle || fold(`${u.name} ${u.email} ${u.access.items.map((i) => `${i.code} ${i.sector ?? ""} ${i.department ?? ""}`).join(" ")}`).includes(needle));
  // grupo por área: o usuário aparece em cada área em que tem CC, só com os setores daquela área
  const groups = new Map<string, { user: UserListItem; items: AccessItem[] }[]>();
  const push = (g: string, user: UserListItem, items: AccessItem[]) => groups.set(g, [...(groups.get(g) ?? []), { user, items }]);
  for (const u of users) {
    if (u.access.is_global) push(GLOBAL_GROUP, u, u.access.items);
    else if (!u.access.items.length) push(NONE_GROUP, u, []);
    else {
      const byDept = new Map<string, AccessItem[]>();
      for (const i of u.access.items) byDept.set(i.department ?? "Sem área", [...(byDept.get(i.department ?? "Sem área") ?? []), i]);
      byDept.forEach((items, dept) => push(dept, u, items));
    }
  }
  const order = (g: string) => (g === NONE_GROUP ? "0" : g === GLOBAL_GROUP ? "2" : `1${g}`);
  const sorted = [...groups.entries()].sort((a, b) => order(a[0]).localeCompare(order(b[0])));

  return (
    <>
      <PageHeader
        title="Usuários"
        subtitle="Agrupados por área: cada usuário enxerga os centros de custo em que é gestor (cadastro do CC) e os marcados no cadastro dele; Administrador e Controladoria veem todos."
        actions={can() && <button className="btn btn-primary" onClick={() => { setOk(null); setForm({ user: null }); }}>Novo usuário</button>}
      />
      {ok && <Alert tone="good">{ok}</Alert>}
      {error && <Alert>{error}</Alert>}
      <div className="filters">
        <SearchBox value={q} onChange={setQ} placeholder="Buscar nome, e-mail, setor ou CC" />
      </div>
      {!data ? (
        <Loading />
      ) : (
        <div className="section-stack">
          {sorted.map(([group, rows]) => (
            <Card
              key={group}
              title={`${group} · ${fmtInt(rows.length)} usuário${rows.length === 1 ? "" : "s"}`}
              actions={group === NONE_GROUP ? <Badge tone="warn">marque os CCs em Editar</Badge> : undefined}
            >
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Nome</th>
                      <th>Perfis</th>
                      <th>{group === GLOBAL_GROUP ? "Acesso" : "Setor e centros de custo"}</th>
                      <th>Situação</th>
                      <th>Último acesso</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map(({ user: u, items }) => (
                      <tr key={u.id}>
                        <td>
                          {u.name}
                          <div className="muted small">{u.email}</div>
                        </td>
                        <td className="small">
                          {u.roles.map((r) => ROLE_LABELS[r] ?? r).join(", ")}
                          {u.pj_access && u.pj_access !== "NONE" && (
                            <div><Badge tone="warn">Contratos PJ: {PJ_ACCESS_LABELS[u.pj_access]}</Badge></div>
                          )}
                        </td>
                        <td>
                          {group === GLOBAL_GROUP ? (
                            <>
                              <Badge tone="info">Todos os CCs</Badge>
                              {items.some((i) => i.manager) && (
                                <div className="muted small">gestor de {items.filter((i) => i.manager).map((i) => `${i.code} (${i.sector ?? "sem setor"})`).join(", ")}</div>
                              )}
                            </>
                          ) : group === NONE_GROUP ? (
                            <span className="muted">Nenhum</span>
                          ) : (
                            <>
                              {u.access.departments.includes(group) && <Badge tone="info">área inteira</Badge>}
                              <Sectors items={items} />
                            </>
                          )}
                        </td>
                        <td>{u.is_active ? <Badge tone="good">Ativo</Badge> : <Badge tone="neutral">Inativo</Badge>}</td>
                        <td className="small">{fmtDateTime(u.last_login_at)}</td>
                        <td className="row-actions">
                          {can() && <button className="btn btn-sm" onClick={() => { setOk(null); setForm({ user: u }); }}>Editar</button>}
                          <button className="btn btn-sm btn-ghost" onClick={() => setEditing(u)} title="Ver acessos e liberar uma empresa inteira">Acessos</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          ))}
          {sorted.length === 0 && <div className="muted">Nenhum usuário encontrado.</div>}
        </div>
      )}
      {editing && <UserAccessModal user={editing} onClose={() => setEditing(null)} onChanged={reload} />}
      {form && <UserEditor user={form.user} onClose={() => setForm(null)} onSaved={(msg) => { setOk(msg); reload(); }} />}
    </>
  );
}
