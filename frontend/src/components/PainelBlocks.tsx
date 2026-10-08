import { Link, useNavigate } from "react-router-dom";
import {
  api,
  type Inventory,
  type ReviewQueueItem,
} from "../api";
import { PlotlyChart, type Figure } from "./PlotlyChart";
import { Alert, Badge, Card, Stat, useLoad } from "./ui";
import {
  fmtCompact,
  fmtDate,
  fmtDateTime,
  fmtInt,
  fmtMoney,
  REVIEW_STATUS,
  SUBMISSION_STATUS,
} from "../labels";

/* Blocos do Painel que não são gráficos Plotly do overview: tarefas do gestor, validações GMD e, no fim, a base
   importada (quadro de pessoal com o gráfico por CC e premissas). */


/** Base importada no fim do Painel: quadro de pessoal (com o gráfico por CC) e premissas. A manutenção da base
 *  (cadastros, qualidade, importações, versões) fica na página de consulta "Situação da base" (`pages/BaseStatus`). */
export function PainelBase() {
  const inventory = useLoad(() => api<Inventory>("/dashboard/inventory"));
  const inv = inventory.data;
  const people = inv?.figures?.people_by_cost_center as Figure | null | undefined;

  return (
    <>
      {inv && inv.personnel.headcount > 0 && (
        <>
          <h2 className="section-title">Quadro de pessoal (base importada)</h2>
          <div className="stats">
            <Stat label="Colaboradores ativos" value={fmtInt(inv.personnel.headcount)} />
            <Stat label="Folha mensal (salário base)" value={fmtCompact(inv.personnel.monthly_payroll)} hint={fmtMoney(inv.personnel.monthly_payroll)} />
            <Stat label="Custo mensal estimado" value={fmtCompact(inv.personnel.monthly_estimated_cost)} hint="salário × multiplicador do contrato" />
            <Stat label="Custo anual estimado" value={fmtCompact(inv.personnel.annual_estimated_cost)} hint="12 × custo mensal, sem reajuste" />
          </div>
          {people && (
            <Card title="Maiores centros de custo em pessoas">
              <PlotlyChart figure={people} ariaLabel="Maiores centros de custo em pessoas" />
            </Card>
          )}
        </>
      )}

      {inv && inv.macro.rows.length > 0 && (
        <Card title="Premissas macroeconômicas e de negócio (versão vigente)">
          <div className="table-wrap scroll-y">
            <table className="table table-compact">
              <thead>
                <tr>
                  <th>Indicador</th>
                  <th>Fonte</th>
                  {inv.macro.years.map((y) => <th key={y} className="right">{y}</th>)}
                </tr>
              </thead>
              <tbody>
                {inv.macro.rows.map((r, i) => (
                  <tr key={i}>
                    <td className={r.segment ? "indent" : undefined}>{r.segment ?? r.indicator}</td>
                    <td className="muted small wrap">{r.source ?? ""}</td>
                    {inv.macro.years.map((y) => {
                      const v = r.values[String(y)];
                      const n = Number(v);
                      const pct = r.indicator.includes("%") || (!r.segment && ["IPCA", "PIB Brasil", "PIB Norte", "CDI"].includes(r.indicator));
                      return (
                        <td key={y} className="right">
                          {v === undefined ? "—" : pct ? `${(n * 100).toLocaleString("pt-BR", { maximumFractionDigits: 2 })}%` : n.toLocaleString("pt-BR", { maximumFractionDigits: 2 })}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

    </>
  );
}



interface TaskRow { cost_center_id: number; code: string; name: string; status: string; status_label: string }
interface TaskSummary { cycle: { deadline: string | null; status: string }; rows: TaskRow[] }

/** Primeira coisa que o gestor vê: seus centros de custo, a situação de cada módulo e os prazos. */
export function ManagerTasks({ warnWhenEmpty }: { warnWhenEmpty: boolean }) {
  const opex = useLoad(() => api<TaskSummary>("/opex/summary"));
  const capex = useLoad(() => api<TaskSummary>("/capex/summary"));
  const people = useLoad(() => api<TaskSummary>("/personnel/summary"));
  if (!opex.data || !capex.data || !people.data) return null;
  const ccs = new Map<number, TaskRow>();
  for (const r of [...opex.data.rows, ...capex.data.rows, ...people.data.rows]) if (!ccs.has(r.cost_center_id)) ccs.set(r.cost_center_id, r);
  if (ccs.size === 0) {
    return warnWhenEmpty ? <Alert tone="warn">Nenhum centro de custo está vinculado ao seu usuário. Peça à Controladoria para associar o seu CC.</Alert> : null;
  }
  const modules: { key: string; label: string; route: string; data: TaskSummary }[] = [
    { key: "opex", label: "OPEX", route: "/orcamento", data: opex.data },
    { key: "capex", label: "CAPEX", route: "/capex", data: capex.data },
    { key: "personnel", label: "Pessoal", route: "/pessoal", data: people.data },
  ];
  const days = (iso: string | null) => (iso ? Math.ceil((new Date(iso).getTime() - Date.now()) / 86_400_000) : null);
  const todo = (status: string) => ["DRAFT", "IN_PROGRESS", "ADJUSTMENT_REQUESTED"].includes(status);
  return (
    <Card title="Suas tarefas" actions={<span className="muted small">{opex.data.cycle.status === "OPEN" ? "ciclo aberto para preenchimento" : "ciclo fechado"}</span>}>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Centro de custo</th>
              {modules.map((m) => {
                const d = days(m.data.cycle.deadline);
                return (
                  <th key={m.key}>
                    {m.label}
                    <div className="muted small" style={{ fontWeight: 400, textTransform: "none" }}>
                      {m.data.cycle.deadline ? `prazo ${fmtDate(m.data.cycle.deadline)}${d !== null ? (d < 0 ? " · vencido" : ` · faltam ${d} dia(s)`) : ""}` : "sem prazo definido"}
                    </div>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {[...ccs.values()].map((cc) => (
              <tr key={cc.cost_center_id}>
                <td><strong>{cc.name}</strong><div className="muted small mono">{cc.code}</div></td>
                {modules.map((m) => {
                  const row = m.data.rows.find((r) => r.cost_center_id === cc.cost_center_id);
                  const st = row ? SUBMISSION_STATUS[row.status] : null;
                  return (
                    <td key={m.key}>
                      <Link to={`${m.route}/${cc.cost_center_id}`} className="nowrap" style={{ textDecoration: "none" }}>
                        <Badge tone={st?.tone ?? "neutral"}>{st?.label ?? row?.status_label ?? "Não iniciado"}</Badge>
                      </Link>
                      {row && todo(row.status) && <div className="muted small">{row.status === "ADJUSTMENT_REQUESTED" ? "veja o motivo e ajuste" : "preencher e enviar"}</div>}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

/** Pacotes GMD aguardando a validação do usuário (gestor de pacote ou Controladoria); some quando não há nada. */
export function PackageReviews() {
  const navigate = useNavigate();
  const queue = useLoad(() => api<ReviewQueueItem[]>("/opex/review-queue"));
  const items = queue.data ?? [];
  if (!items.length) return null;
  const pending = items.filter((r) => r.review_status === "PENDING").length;
  return (
    <Card title="Pacotes aguardando sua validação" actions={pending ? <Badge tone="warn">{fmtInt(pending)} pendente(s)</Badge> : undefined}>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th>Centro de custo</th><th>Pacote</th><th className="right">Valor do pacote</th><th>Enviado em</th><th>Situação</th></tr>
          </thead>
          <tbody>
            {items.map((r) => (
              <tr key={`${r.submission_id}-${r.package_id}`} className="clickable" onClick={() => navigate(`/orcamento/${r.cost_center_id}?pacote=${r.package_id}`)}>
                <td>{r.cost_center}</td>
                <td>{r.package}</td>
                <td className="right">{fmtMoney(r.package_total)}</td>
                <td>{fmtDateTime(r.submitted_at)}</td>
                <td><Badge tone={REVIEW_STATUS[r.review_status]?.tone ?? "neutral"}>{REVIEW_STATUS[r.review_status]?.label}</Badge></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

