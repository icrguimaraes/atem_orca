import { useEffect, useMemo, useState } from "react";
import { api, type Company, type CostCenter, type Department, type UserAccess, type UserListItem } from "../api";
import { ROLE_LABELS, fmtInt } from "../labels";
import { Alert, Loading, Modal } from "./ui";

/* Criar e editar usuário num formulário só (08/10/2026): dados, perfis e os centros de custo que ele acessa — por
   área, com busca — marcando também de quais CCs ele é o gestor (cadastro do CC). Empresa inteira continua em
   "Acessos". Tudo vai para a auditoria pelo backend. */

const ROLE_HINTS: Record<string, string> = {
  ADMIN: "tudo, inclusive usuários e cadastros",
  CONTROLLER: "vê e ajusta todos os CCs, importa e consolida",
  MANAGER: "preenche, justifica, envia e responde perguntas dos CCs marcados",
  PACKAGE_MANAGER: "valida os pacotes GMD atribuídos a ele no ciclo",
  HR: "vê o quadro de pessoal de todos os CCs",
  VIEWER: "só consulta os CCs marcados (pode questionar no Painel)",
};
const GLOBAL = ["ADMIN", "CONTROLLER"];

function fold(s: string) {
  return s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

export function UserEditor({ user, onClose, onSaved }: { user: UserListItem | null; onClose: () => void; onSaved: (msg: string) => void }) {
  const creating = user === null;
  const [form, setForm] = useState({
    name: user?.name ?? "",
    email: user?.email ?? "",
    password: "",
    is_active: user?.is_active ?? true,
    roles: user?.roles ?? ["MANAGER"],
  });
  const [ccs, setCcs] = useState<CostCenter[] | null>(null);
  const [depts, setDepts] = useState<Department[]>([]);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [access, setAccess] = useState<UserAccess | null>(null);
  const [people, setPeople] = useState<Map<number, UserListItem>>(new Map());
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [manager, setManager] = useState<Set<number>>(new Set());
  const [q, setQ] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    Promise.all([
      api<CostCenter[]>("/cost-centers"),
      api<Department[]>("/departments"),
      api<Company[]>("/companies"),
      user ? api<UserAccess>(`/users/${user.id}/access`) : Promise.resolve(null),
      api<UserListItem[]>("/users"),
    ])
      .then(([c, d, co, a, us]) => {
        setPeople(new Map(us.map((u) => [u.id, u])));
        setCcs(c.filter((x) => x.is_active || (a && (a.managed.some((m) => m.id === x.id) || a.scopes.some((s) => s.cost_center_id === x.id)))));
        setDepts(d);
        setCompanies(co);
        setAccess(a);
        if (a) {
          const mgr = new Set(a.managed.map((m) => m.id));
          setManager(mgr);
          setSelected(new Set([...mgr, ...a.scopes.filter((s) => s.kind === "COST_CENTER" && s.cost_center_id).map((s) => s.cost_center_id!)]));
        }
      })
      .catch((e: Error) => setError(e.message));
  }, [user]);

  const groups = useMemo(() => {
    const needle = fold(q.trim());
    const deptName = new Map(depts.map((d) => [d.id, d.name]));
    const company = new Map(companies.map((c) => [c.id, c.short_name || c.name]));
    const out = new Map<string, CostCenter[]>();
    for (const cc of ccs ?? []) {
      const area = deptName.get(cc.department_id ?? -1) ?? "Sem área";
      const label = `${cc.code} ${cc.name} ${area} ${company.get(cc.company_id) ?? ""} ${cc.manager_name ?? ""}`;
      if (needle && !fold(label).includes(needle)) continue;
      out.set(area, [...(out.get(area) ?? []), cc]);
    }
    return [...out.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [ccs, depts, companies, q]);
  const companyName = (id: number) => companies.find((c) => c.id === id)?.short_name ?? "";
  const companyScopes = access?.scopes.filter((s) => s.kind === "COMPANY") ?? [];
  const isGlobal = form.roles.some((r) => GLOBAL.includes(r));

  function toggleRole(role: string) {
    setForm((f) => ({ ...f, roles: f.roles.includes(role) ? f.roles.filter((r) => r !== role) : [...f.roles, role] }));
  }
  function toggleCc(id: number) {
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) {
        n.delete(id);
        setManager((m) => { const k = new Set(m); k.delete(id); return k; });
      } else n.add(id);
      return n;
    });
  }
  function toggleManager(id: number) {
    setManager((m) => {
      const n = new Set(m);
      if (n.has(id)) n.delete(id);
      else {
        n.add(id);
        setSelected((s) => new Set(s).add(id));
      }
      return n;
    });
  }
  function toggleGroup(list: CostCenter[]) {
    const all = list.every((c) => selected.has(c.id));
    setSelected((s) => {
      const n = new Set(s);
      list.forEach((c) => (all ? n.delete(c.id) : n.add(c.id)));
      return n;
    });
    if (all) setManager((m) => { const n = new Set(m); list.forEach((c) => n.delete(c.id)); return n; });
  }

  async function save() {
    setError(null);
    if (form.name.trim().length < 2) return setError("Informe o nome");
    if (!form.email.includes("@")) return setError("Informe um e-mail válido");
    if (creating && form.password.length < 8) return setError("A senha inicial precisa de pelo menos 8 caracteres");
    if (!creating && form.password && form.password.length < 8) return setError("A nova senha precisa de pelo menos 8 caracteres");
    if (!form.roles.length) return setError("Escolha ao menos um perfil");
    const cost_center_ids = [...selected].filter((id) => !manager.has(id));
    const manager_of = [...manager];
    // o CC está ligado a OUTRA conta de usuário (o nome no CC pode ser igual: ex. a mesma pessoa com dois logins)
    const stealing = (ccs ?? []).filter((c) => manager.has(c.id) && c.manager_user_id && c.manager_user_id !== user?.id);
    const owner = (c: CostCenter) => {
      const u = people.get(c.manager_user_id!);
      return u ? `${u.name} <${u.email}>` : `usuário #${c.manager_user_id}`;
    };
    if (
      stealing.length &&
      !window.confirm(
        [
          "Estes CCs estão ligados a outra conta de usuário, que deixará de ser a gestora:",
          "",
          ...stealing.map((c) => `${c.code} · ${c.name}: hoje ${owner(c)}`),
          "",
          `Passar para ${form.name.trim()} <${form.email.trim().toLowerCase()}>?`,
        ].join("\n"),
      )
    )
      return;
    setBusy(true);
    try {
      if (creating) {
        await api("/users", {
          method: "POST",
          body: JSON.stringify({ name: form.name.trim(), email: form.email.trim(), password: form.password, roles: form.roles, cost_center_ids, manager_of }),
        });
        onSaved(`Usuário ${form.email.trim().toLowerCase()} criado com ${fmtInt(selected.size)} centro(s) de custo. Envie a senha inicial por um canal seguro.`);
      } else {
        const patch: Record<string, unknown> = {};
        if (form.name.trim() !== user!.name) patch.name = form.name.trim();
        if (form.email.trim().toLowerCase() !== user!.email) patch.email = form.email.trim();
        if (form.is_active !== user!.is_active) patch.is_active = form.is_active;
        if (form.password) patch.password = form.password;
        if (Object.keys(patch).length) await api(`/users/${user!.id}`, { method: "PATCH", body: JSON.stringify(patch) });
        if ([...form.roles].sort().join() !== [...user!.roles].sort().join())
          await api(`/users/${user!.id}/roles`, { method: "PUT", body: JSON.stringify(form.roles) });
        await api(`/users/${user!.id}/cost-centers`, { method: "PUT", body: JSON.stringify({ cost_center_ids, manager_of }) });
        onSaved(`Usuário ${form.name.trim()} atualizado${form.password ? " (senha redefinida: envie por um canal seguro)" : ""}.`);
      }
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title={creating ? "Novo usuário" : `Editar · ${user!.name}`}
      onClose={onClose}
      wide
      footer={
        <>
          <span className="muted small" style={{ marginRight: "auto" }}>
            {fmtInt(selected.size)} centro(s) de custo · gestor de {fmtInt(manager.size)}
          </span>
          <button className="btn" onClick={onClose} disabled={busy}>Cancelar</button>
          <button className="btn btn-primary" onClick={save} disabled={busy}>{busy ? "Salvando…" : creating ? "Criar usuário" : "Salvar"}</button>
        </>
      }
    >
      <div className="stack-lg">
        {error && <Alert>{error}</Alert>}
        <section className="stack">
          <h3 className="section-title">Dados</h3>
          <div className="form-row">
            <label>Nome<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
            <label>E-mail<input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></label>
            <label>
              {creating ? "Senha inicial (mín. 8)" : "Nova senha (opcional)"}
              <input type="text" autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} placeholder={creating ? "" : "deixe em branco para manter"} />
            </label>
          </div>
          {!creating && (
            <label className="check">
              <input type="checkbox" checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} />
              Ativo (desmarque para bloquear o acesso sem apagar o histórico)
            </label>
          )}
        </section>

        <section className="stack">
          <h3 className="section-title">Perfis</h3>
          <div className="role-grid">
            {Object.entries(ROLE_LABELS).map(([code, label]) => (
              <label key={code} className={`role-option ${form.roles.includes(code) ? "on" : ""}`}>
                <input type="checkbox" checked={form.roles.includes(code)} onChange={() => toggleRole(code)} />
                <span><strong>{label}</strong><span className="muted small"> — {ROLE_HINTS[code]}</span></span>
              </label>
            ))}
          </div>
        </section>

        <section className="stack">
          <h3 className="section-title">Centros de custo</h3>
          {isGlobal && <Alert tone="info">Administrador e Controladoria enxergam todos os CCs; a seleção abaixo só vale se o perfil mudar.</Alert>}
          {companyScopes.length > 0 && (
            <Alert tone="info">Também acessa a empresa inteira: {companyScopes.map((s) => s.name).join(", ")} (gerencie em Acessos).</Alert>
          )}
          <div className="muted small">
            Marque os CCs que o usuário acessa. "Gestor" grava o usuário como gestor do CC no cadastro (aparece como responsável e
            responde as perguntas do Painel).
          </div>
          <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Buscar por código, nome, área, empresa ou gestor atual" aria-label="Buscar centro de custo" />
          {!ccs ? (
            <Loading />
          ) : groups.length === 0 ? (
            <div className="muted">Nenhum centro de custo encontrado.</div>
          ) : (
            <div className="cc-picker">
              {groups.map(([area, list]) => {
                const all = list.every((c) => selected.has(c.id));
                const some = list.some((c) => selected.has(c.id));
                return (
                  <div key={area} className="cc-group">
                    <label className="cc-group-head">
                      <input type="checkbox" checked={all} ref={(el) => { if (el) el.indeterminate = some && !all; }} onChange={() => toggleGroup(list)} />
                      <strong>{area}</strong>
                      <span className="muted small">{fmtInt(list.filter((c) => selected.has(c.id)).length)} de {fmtInt(list.length)}</span>
                    </label>
                    {list.map((cc) => (
                      <div key={cc.id} className={`cc-row ${selected.has(cc.id) ? "on" : ""}`}>
                        <label className="cc-main">
                          <input type="checkbox" checked={selected.has(cc.id)} onChange={() => toggleCc(cc.id)} />
                          <span>
                            <strong>{cc.code}</strong> · {cc.name}
                            <span className="muted small">
                              {" "}· {companyName(cc.company_id)}
                              {cc.manager_user_id
                                ? ` · gestor: ${people.get(cc.manager_user_id)?.email ?? cc.manager_name ?? "outro usuário"}${cc.manager_user_id === user?.id ? " (este usuário)" : ""}`
                                : cc.manager_name ? ` · gestor (sem login): ${cc.manager_name}` : " · sem gestor"}
                            </span>
                          </span>
                        </label>
                        <label className="cc-manager" title="Gravar como gestor do CC (cadastro)">
                          <input type="checkbox" checked={manager.has(cc.id)} onChange={() => toggleManager(cc.id)} />
                          Gestor
                        </label>
                      </div>
                    ))}
                  </div>
                );
              })}
            </div>
          )}
        </section>
      </div>
    </Modal>
  );
}
