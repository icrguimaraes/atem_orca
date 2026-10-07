import { Link, useNavigate } from "react-router-dom";
import {
  api,
  type DatasetVersion,
  type ImportBatch,
  type Inventory,
  type Page,
  type QualityCheck,
  type ReviewQueueItem,
} from "../api";
import { useAuth } from "../auth";
import { Alert, Badge, Card, Empty, Stat, useLoad } from "./ui";
import {
  DATASET_LABELS,
  IMPORT_STATUS,
  fmtCompact,
  fmtDate,
  fmtDateTime,
  fmtInt,
  fmtMoney,
  REVIEW_STATUS,
  SUBMISSION_STATUS,
} from "../labels";

/* Blocos do Painel que não são gráficos (vieram do Painel antigo em SVG): tarefas do gestor, validações GMD e, no fim,
   a base importada (quadro, premissas, cadastros) e a manutenção da Controladoria (qualidade, importações, versões). */

const SEVERITY = {
  ERROR: { tone: "bad", label: "Erro" },
  WARNING: { tone: "warn", label: "Atenção" },
  INFO: { tone: "info", label: "Info" },
  OK: { tone: "good", label: "OK" },
} as const;

/** Base importada e manutenção (Controladoria): depois dos números do Painel. */
export function PainelBase() {
  const { can } = useAuth();
  const isController = can("CONTROLLER");
  const inventory = useLoad(() => api<Inventory>("/dashboard/inventory"));
  const admin = useLoad(async () => {
    if (!isController) return null;
    const [quality, imports, versions] = await Promise.all([
      api<{ checks: QualityCheck[]; issues: number }>("/dashboard/data-quality"),
      api<Page<ImportBatch>>("/imports?limit=5"),
      api<DatasetVersion[]>("/dataset-versions?current_only=true"),
    ]);
    return { quality, imports, versions };
  }, [isController]);
  const inv = inventory.data;

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
          <div className="grid-2">
            <Card title="Por tipo de contrato">
              <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr><th>Contrato</th><th className="right">Pessoas</th><th className="right">Folha mensal</th><th className="right">Multiplicador</th></tr>
                </thead>
                <tbody>
                  {inv.personnel.by_contract.map((c) => (
                    <tr key={c.contract}>
                      <td>{c.contract}</td>
                      <td className="right">{fmtInt(c.headcount)}</td>
                      <td className="right">{fmtMoney(c.payroll)}</td>
                      <td className="right">{Number(c.multiplier).toLocaleString("pt-BR")}×</td>
                    </tr>
                  ))}
                </tbody>
              </table></div>
            </Card>
            <Card title="Maiores centros de custo em pessoas">
              <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr><th>Centro de custo</th><th className="right">Pessoas</th><th className="right">Folha mensal</th></tr>
                </thead>
                <tbody>
                  {inv.personnel.by_cost_center.map((c) => (
                    <tr key={`${c.code}-${c.name}`}>
                      <td>{c.name}<div className="muted small mono">{c.code ?? "—"}</div></td>
                      <td className="right">{fmtInt(c.headcount)}</td>
                      <td className="right">{fmtMoney(c.payroll)}</td>
                    </tr>
                  ))}
                </tbody>
              </table></div>
            </Card>
          </div>
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

      {inv && (
        <Card title="Cadastros">
          <div className="stats">
            <Stat label="Centros de custo ativos" value={fmtInt(inv.master.cost_centers)} hint={inv.master.cost_centers_without_user ? `${fmtInt(inv.master.cost_centers_without_user)} sem usuário gestor` : "todos com gestor"} />
            {Object.entries(inv.master.accounts_by_nature).map(([n, c]) => (
              <Stat key={n} label={`Contas ${n}`} value={fmtInt(c)} />
            ))}
            <Stat label="Pacotes GMD" value={fmtInt(inv.master.packages.length)} hint={`${inv.master.packages.filter((p) => p.package_type === 1).length} com validação obrigatória`} />
          </div>
        </Card>
      )}

      {/* manutenção da base (Controladoria): depois dos números, junto das importações e versões */}
      {isController && admin.data && (
        <Card
          title="Qualidade da base"
          actions={admin.data.quality.issues ? <Badge tone="warn">{admin.data.quality.issues} ponto(s)</Badge> : <Badge tone="good">Tudo certo</Badge>}
        >
          <ul className="checks-list">
            {admin.data.quality.checks.map((c) => (
              <li key={c.code}>
                <Badge tone={SEVERITY[c.severity].tone}>{SEVERITY[c.severity].label}</Badge>
                <div>
                  <div>
                    {c.title}
                    {c.count > 1 && c.severity !== "OK" && <strong> · {fmtInt(c.count)}</strong>}
                  </div>
                  {c.severity !== "OK" && c.detail && <div className="muted small">{c.detail}</div>}
                  {c.severity !== "OK" && c.samples.length > 0 && (
                    <div className="muted small mono">{c.samples.slice(0, 6).join(" · ")}{c.samples.length > 6 ? " …" : ""}</div>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {isController && admin.data && (
        <div className="grid-2">
          <Card title="Últimas importações" actions={<Link to="/importacoes" className="link">Ver todas</Link>}>
            {admin.data.imports.items.length ? (
              <div className="table-wrap">
              <table className="table">
                <tbody>
                  {admin.data.imports.items.map((b) => (
                    <tr key={b.id}>
                      <td>
                        <Link to={`/importacoes/${b.id}`} className="link">{b.file_name}</Link>
                        <div className="muted small">{DATASET_LABELS[b.dataset_type ?? ""] ?? "Detectando…"}</div>
                      </td>
                      <td className="right">
                        <Badge tone={IMPORT_STATUS[b.status]?.tone ?? "neutral"}>{IMPORT_STATUS[b.status]?.label ?? b.status}</Badge>
                        <div className="muted small">{fmtDateTime(b.created_at)}</div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table></div>
            ) : (
              <Empty>Nenhum arquivo importado. <Link to="/importacoes" className="link">Importar agora</Link></Empty>
            )}
          </Card>
          <Card title="Bases vigentes">
            {admin.data.versions.length ? (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Base</th>
                      <th>Escopo</th>
                      <th className="right">Versão</th>
                      <th className="right">Registros</th>
                    </tr>
                  </thead>
                  <tbody>
                    {admin.data.versions.map((v) => (
                      <tr key={v.id}>
                        <td>{DATASET_LABELS[v.dataset_type] ?? v.dataset_type}</td>
                        <td className="mono small">{v.scope_key}</td>
                        <td className="right">v{v.version_number}</td>
                        <td className="right">{fmtInt(v.row_count)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty>Nenhuma base carregada ainda.</Empty>
            )}
          </Card>
        </div>
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

