import { useState } from "react";
import { api, type AuditLog, type Page, type User } from "../api";
import { Alert, Card, Empty, Loading, PageHeader, useLoad } from "../components/ui";
import { fmtDateTime, fmtInt } from "../labels";

const ACTIONS: Record<string, string> = {
  CREATE: "Criação",
  UPDATE: "Alteração",
  LOGIN: "Login",
  LOGIN_FAILED: "Login recusado",
  UPLOAD: "Upload",
  CONFIRM: "Confirmação",
  REJECT: "Descarte",
  LOAD: "Carga",
  SET_ROLES: "Perfis alterados",
  SET_SCOPES: "Escopo alterado",
  ADD_SCOPE: "Acesso concedido",
  REMOVE_SCOPE: "Acesso removido",
  SET_PARAMETER: "Parâmetro alterado",
  CYCLE_OPEN: "Ciclo aberto",
  CYCLE_CLOSE: "Ciclo fechado",
};

const ENTITIES = ["", "import_batch", "cost_center", "account", "branch", "budget_cycle", "cycle_parameter", "user", "employee"];

function changes(log: AuditLog): string {
  const after = log.after ?? {};
  const before = log.before ?? {};
  const keys = Object.keys(after).filter((k) => !["created_at", "updated_at"].includes(k));
  if (log.action === "UPDATE")
    return keys.map((k) => `${k}: ${before[k] ?? "∅"} → ${after[k] ?? "∅"}`).join(" · ");
  return keys.slice(0, 4).map((k) => `${k}: ${typeof after[k] === "object" ? JSON.stringify(after[k]) : after[k]}`).join(" · ");
}

export default function Audit() {
  const [entity, setEntity] = useState("");
  const [offset, setOffset] = useState(0);
  const { data, error } = useLoad(async () => {
    const [logs, users] = await Promise.all([
      api<Page<AuditLog>>(`/audit-logs?limit=50&offset=${offset}${entity ? `&entity_type=${entity}` : ""}`),
      api<User[]>("/users"),
    ]);
    return { logs, users: new Map(users.map((u) => [u.id, u.name])) };
  }, [entity, offset]);

  return (
    <>
      <PageHeader title="Auditoria" subtitle="Quem fez o quê, quando e qual era o valor anterior." />
      <Card
        actions={
          <select value={entity} onChange={(e) => (setEntity(e.target.value), setOffset(0))}>
            {ENTITIES.map((e) => (
              <option key={e} value={e}>
                {e || "Todas as entidades"}
              </option>
            ))}
          </select>
        }
      >
        {error && <Alert>{error}</Alert>}
        {!data ? (
          <Loading />
        ) : data.logs.items.length === 0 ? (
          <Empty>Nenhum registro.</Empty>
        ) : (
          <>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Quando</th>
                    <th>Usuário</th>
                    <th>Ação</th>
                    <th>Entidade</th>
                    <th>Detalhe</th>
                  </tr>
                </thead>
                <tbody>
                  {data.logs.items.map((l) => (
                    <tr key={l.id}>
                      <td className="nowrap">{fmtDateTime(l.occurred_at)}</td>
                      <td>{l.user_id ? data.users.get(l.user_id) ?? `#${l.user_id}` : "—"}</td>
                      <td>{ACTIONS[l.action] ?? l.action}</td>
                      <td className="mono small">
                        {l.entity_type} {l.entity_id ?? ""}
                      </td>
                      <td className="small">
                        {changes(l)}
                        {l.reason && <div className="muted">{l.reason}</div>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="pager">
              <span className="muted small">
                {fmtInt(offset + 1)}–{fmtInt(offset + data.logs.items.length)} de {fmtInt(data.logs.total)}
              </span>
              <button className="btn btn-ghost btn-sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>
                Anterior
              </button>
              <button className="btn btn-ghost btn-sm" disabled={offset + 50 >= data.logs.total} onClick={() => setOffset(offset + 50)}>
                Próxima
              </button>
            </div>
          </>
        )}
      </Card>
    </>
  );
}
