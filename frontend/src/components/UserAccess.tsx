import { useEffect, useMemo, useState } from "react";
import { api, type AccessSummary, type Company, type CostCenter, type UserAccess, type UserListItem } from "../api";
import { ROLE_LABELS, fmtInt } from "../labels";
import { Alert, Badge, Loading, Modal } from "./ui";

const MAX_RESULTS = 30;
const READS_BY_SCOPE = ["MANAGER", "VIEWER"]; // perfis que só enxergam CCs pelo gestor/escopo

const plural = (n: number, one: string, many: string) => `${fmtInt(n)} ${n === 1 ? one : many}`;
const companyLabel = (c: Company) => `${c.code} · ${c.short_name || c.name}`;

/** Coluna "Centros de custo" da lista de usuários. */
export function AccessCell({ user }: { user: UserListItem }) {
  const a: AccessSummary = user.access;
  if (a.is_global) return <Badge tone="info">Todos</Badge>;
  if (!a.cost_centers)
    return user.roles.some((r) => READS_BY_SCOPE.includes(r)) ? <Badge tone="warn">Nenhum</Badge> : <span className="muted">—</span>;
  const parts = [a.managed && `${fmtInt(a.managed)} como gestor`, a.scopes && plural(a.scopes, "atribuído", "atribuídos")];
  return (
    <>
      <strong>{plural(a.cost_centers, "CC", "CCs")}</strong>
      <span className="muted small access-sub">{parts.filter(Boolean).join(" · ")}</span>
    </>
  );
}

/** Notas por perfil: o que o acesso por CC significa para quem tem perfis com regra própria. */
function roleNotes(user: UserListItem, access: UserAccess): string[] {
  const notes: string[] = [];
  if (access.is_global) {
    const roles = user.roles.filter((r) => r === "ADMIN" || r === "CONTROLLER").map((r) => ROLE_LABELS[r]).join(" e ");
    notes.push(`Perfil ${roles}: enxerga todos os centros de custo. Os acessos abaixo só valem se o perfil mudar.`);
  }
  if (user.roles.includes("HR")) notes.push("Perfil RH: no módulo Pessoal enxerga o quadro de todos os centros de custo.");
  if (user.roles.includes("PACKAGE_MANAGER"))
    notes.push("Gestor de pacote: vê as linhas dos pacotes atribuídos a ele no ciclo em todos os CCs, além dos CCs abaixo.");
  if (user.roles.includes("VIEWER") && !access.is_global) notes.push("Consulta: só leitura nos centros de custo abaixo.");
  return notes;
}

export function UserAccessModal({ user, onClose, onChanged }: { user: UserListItem; onClose: () => void; onChanged: () => void }) {
  const [access, setAccess] = useState<UserAccess | null>(null);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [q, setQ] = useState("");
  const [results, setResults] = useState<CostCenter[] | null>(null);
  const [companyId, setCompanyId] = useState("");

  useEffect(() => {
    Promise.all([api<UserAccess>(`/users/${user.id}/access`), api<Company[]>("/companies")])
      .then(([a, c]) => (setAccess(a), setCompanies(c)))
      .catch((e: Error) => setError(e.message));
  }, [user.id]);

  // há muitos CCs: busca no servidor (código começando pelo termo ou nome contendo) com pequeno atraso
  useEffect(() => {
    const term = q.trim();
    if (term.length < 2) {
      setResults(null);
      return;
    }
    let alive = true;
    const timer = setTimeout(() => {
      api<CostCenter[]>(`/cost-centers?q=${encodeURIComponent(term)}`)
        .then((r) => alive && setResults(r))
        .catch((e: Error) => alive && setError(e.message));
    }, 250);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [q]);

  const index = useMemo(() => {
    const managed = new Set(access?.managed.map((c) => c.id));
    const scoped = new Set(access?.scopes.filter((s) => s.kind === "COST_CENTER").map((s) => s.cost_center_id));
    const wholeCompanies = new Set(access?.scopes.filter((s) => s.kind === "COMPANY").map((s) => s.company_id));
    return { managed, scoped, wholeCompanies, company: new Map(companies.map((c) => [c.id, c])) };
  }, [access, companies]);

  async function change(path: string, init: RequestInit): Promise<boolean> {
    setBusy(true);
    setError(null);
    try {
      setAccess(await api<UserAccess>(path, init));
      onChanged();
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  }
  const add = (body: { cost_center_id?: number; company_id?: number }) =>
    change(`/users/${user.id}/scopes`, { method: "POST", body: JSON.stringify(body) });
  const remove = (scopeId: number) => change(`/users/${user.id}/scopes/${scopeId}`, { method: "DELETE" });

  function resultStatus(cc: CostCenter) {
    if (index.managed.has(cc.id)) return <Badge tone="neutral">Gestor</Badge>;
    if (index.scoped.has(cc.id)) return <Badge tone="good">Atribuído</Badge>;
    if (index.wholeCompanies.has(cc.company_id)) return <Badge tone="good">Pela empresa</Badge>;
    return (
      <button className="btn btn-sm" disabled={busy} onClick={() => add({ cost_center_id: cc.id })}>
        Adicionar
      </button>
    );
  }

  const freeCompanies = companies.filter((c) => c.is_active && !index.wholeCompanies.has(c.id));

  return (
    <Modal title={`Acessos · ${user.name}`} onClose={onClose} wide footer={<button className="btn" onClick={onClose}>Fechar</button>}>
      {!access ? (
        error ? <Alert>{error}</Alert> : <Loading />
      ) : (
        <div className="stack-lg">
          <div className="stack">
            <div>
              {access.is_global ? (
                <strong>Todos os centros de custo</strong>
              ) : (
                <>
                  Acessa <strong>{plural(access.cost_centers, "centro de custo", "centros de custo")}</strong>
                  <span className="muted"> · {user.roles.map((r) => ROLE_LABELS[r] ?? r).join(", ")}</span>
                </>
              )}
            </div>
            {roleNotes(user, access).map((n) => (
              <Alert key={n} tone="info">{n}</Alert>
            ))}
            {error && <Alert>{error}</Alert>}
          </div>

          <section className="stack">
            <h3 className="section-title">Como gestor</h3>
            {access.managed.length ? (
              <div className="table-wrap">
                <table className="table table-access">
                  <tbody>
                    {access.managed.map((cc) => (
                      <tr key={cc.id}>
                        <td>
                          <strong>{cc.code}</strong> · {cc.name} {!cc.is_active && <Badge tone="neutral">Inativo</Badge>}
                          <span className="muted small access-sub">{cc.company}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="muted">Não é gestor de nenhum centro de custo.</div>
            )}
            <div className="muted small">
              Vem do cadastro do CC (Cadastros → Centros de custo, campo Usuário gestor, editável pelo Administrador).
            </div>
          </section>

          <section className="stack">
            <h3 className="section-title">Atribuídos</h3>
            {access.scopes.length ? (
              <div className="table-wrap">
                <table className="table table-access">
                  <tbody>
                    {access.scopes.map((s) => (
                      <tr key={s.id}>
                        <td>
                          {s.kind === "COMPANY" && <><Badge tone="info">Empresa inteira</Badge> </>}
                          {s.kind === "DEPARTMENT" && <><Badge tone="info">Área inteira</Badge> </>}
                          {s.code && <><strong>{s.code}</strong> · </>}
                          {s.name} {!s.is_active && <Badge tone="neutral">Inativo</Badge>}
                          <span className="muted small access-sub">
                            {s.kind === "COMPANY" || s.kind === "DEPARTMENT" ? `${plural(s.cost_centers, "centro de custo", "centros de custo")}, inclusive os criados depois` : s.company}
                          </span>
                        </td>
                        <td className="row-actions">
                          <button className="btn btn-sm danger" disabled={busy} onClick={() => remove(s.id)}>
                            Remover
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="muted">Nenhum centro de custo atribuído.</div>
            )}
          </section>

          <section className="stack">
            <h3 className="section-title">Adicionar acesso</h3>
            <label>
              Centro de custo
              <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Código (início) ou parte do nome" />
            </label>
            {results &&
              (results.length ? (
                <>
                  <div className="table-wrap">
                    <table className="table table-access">
                      <tbody>
                        {results.slice(0, MAX_RESULTS).map((cc) => {
                          const company = index.company.get(cc.company_id);
                          return (
                            <tr key={cc.id}>
                              <td>
                                <strong>{cc.code}</strong> · {cc.name} {!cc.is_active && <Badge tone="neutral">Inativo</Badge>}
                                <span className="muted small access-sub">
                                  {company ? companyLabel(company) : ""}
                                  {cc.manager_name ? ` · gestor: ${cc.manager_name}` : ""}
                                </span>
                              </td>
                              <td className="row-actions">{resultStatus(cc)}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  {results.length > MAX_RESULTS && (
                    <div className="muted small">
                      Mostrando {MAX_RESULTS} de {fmtInt(results.length)}; refine a busca.
                    </div>
                  )}
                </>
              ) : (
                <div className="muted">Nenhum centro de custo encontrado.</div>
              ))}
            {freeCompanies.length > 0 && (
              <div className="access-company">
                <label>
                  Ou a empresa inteira
                  <select value={companyId} onChange={(e) => setCompanyId(e.target.value)}>
                    <option value="">Selecione…</option>
                    {freeCompanies.map((c) => (
                      <option key={c.id} value={c.id}>{companyLabel(c)}</option>
                    ))}
                  </select>
                </label>
                <button
                  className="btn"
                  disabled={busy || !companyId}
                  onClick={() => add({ company_id: Number(companyId) }).then((ok) => ok && setCompanyId(""))}
                >
                  Liberar empresa
                </button>
              </div>
            )}
          </section>
        </div>
      )}
    </Modal>
  );
}
