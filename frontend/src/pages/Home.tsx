import { useState } from "react";
import { Link } from "react-router-dom";
import {
  api,
  type Company,
  type CostCenter,
  type Cycle,
  type DatasetVersion,
  type ImportBatch,
  type Inventory,
  type Overview,
  type Package,
  type Page,
  type QualityCheck,
} from "../api";
import { useAuth } from "../auth";
import { BudgetProgressCard } from "../components/BudgetProgressCard";
import { FilterBar } from "../components/FilterBar";
import { DivergingBars, Heatmap, Legend, MonthlyChart, PairedBars, SERIES, TopBars, Waterfall } from "../components/charts";
import { Alert, Badge, Card, Empty, Loading, PageHeader, Stat, useLoad } from "../components/ui";
import {
  CYCLE_STATUS,
  DATASET_LABELS,
  IMPORT_STATUS,
  MONTHS,
  fmtCompact,
  fmtDate,
  fmtDateTime,
  fmtInt,
  fmtMoney,
  fmtPct,
  SUBMISSION_STATUS,
} from "../labels";

const SEVERITY = {
  ERROR: { tone: "bad", label: "Erro" },
  WARNING: { tone: "warn", label: "Atenção" },
  INFO: { tone: "info", label: "Info" },
  OK: { tone: "good", label: "OK" },
} as const;

function daysUntil(iso: string | null): string | undefined {
  if (!iso) return undefined;
  const diff = Math.ceil((new Date(`${iso}T23:59:59`).getTime() - Date.now()) / 86_400_000);
  return diff >= 0 ? `faltam ${diff} dia(s)` : `encerrado há ${-diff} dia(s)`;
}

function Delta({ pct, invert = false }: { pct: string | null; invert?: boolean }) {
  if (pct === null) return null;
  const n = Number(pct);
  // em despesa, crescer é "pior": seta para cima em tom de alerta
  const cls = n === 0 ? "" : (n > 0) !== invert ? "up" : "down";
  return <span className={`delta ${cls}`}>{n > 0 ? "▲" : n < 0 ? "▼" : "•"} {fmtPct(pct)}</span>;
}

function RankChart({ rows, refLabel, prevLabel, empty, showPrev }: { rows: Overview["top_accounts"]; refLabel: string; prevLabel: string; empty: string; showPrev: boolean }) {
  if (!rows.length) return <Empty>{empty}</Empty>;
  return (
    <TopBars
      label={refLabel}
      prevLabel={showPrev ? prevLabel : null}
      rows={rows.map((r) => ({
        label: r.name ?? r.code ?? "—",
        sub: r.code,
        value: Number(r.ref_ytd),
        prev: showPrev ? Number(r.prev_ytd) : undefined,
        note: showPrev && r.ytd_var_pct !== null ? fmtPct(r.ytd_var_pct) : undefined,
      }))}
    />
  );
}

export default function Home() {
  const { user, can } = useAuth();
  const isController = can("CONTROLLER");
  const [filters, setFilters] = useState({ company_id: "", cost_center_id: "", package_id: "" });
  const [showTable, setShowTable] = useState(false);
  // anos exibidos: vazio = padrão do servidor (mais recente com realizado + anterior); um ano = só ele; dois = comparação
  const [years, setYears] = useState<number[]>([]);
  function toggleYear(y: number, current: number[]) {
    const base = years.length ? years : current;
    const next = base.includes(y) ? base.filter((x) => x !== y) : [...base, y];
    if (next.length) setYears(next.sort());
  }

  const base = useLoad(async () => {
    const [cycles, companies, ccs, packages] = await Promise.all([
      api<Cycle[]>("/cycles"),
      api<Company[]>("/companies"),
      api<CostCenter[]>("/cost-centers"),
      api<Package[]>("/packages"),
    ]);
    return { cycle: cycles[0] ?? null, companies, ccs, packages };
  });

  const query = new URLSearchParams(
    Object.entries({ ...filters, years: years.join(",") }).filter(([, v]) => v),
  ).toString();
  const inventory = useLoad(() => api<Inventory>("/dashboard/inventory"));
  const overview = useLoad(() => api<Overview>(`/dashboard/overview${query ? `?${query}` : ""}`), [query]);

  const admin = useLoad(async () => {
    if (!isController) return null;
    const [quality, imports, versions] = await Promise.all([
      api<{ checks: QualityCheck[]; issues: number }>("/dashboard/data-quality"),
      api<Page<ImportBatch>>("/imports?limit=5"),
      api<DatasetVersion[]>("/dataset-versions?current_only=true"),
    ]);
    return { quality, imports, versions };
  }, [isController]);

  if (!base.data) return <Loading />;
  const { cycle, companies, ccs, packages } = base.data;
  const o = overview.data;
  const monthName = o?.last_closed_period ? MONTHS[o.last_closed_period - 1] : null;
  const prevLabel = o?.previous_year ? `${o.previous_year} até ${monthName ?? "—"}` : "";
  const refLabel = o ? `${o.reference_year} até ${monthName ?? "—"}` : "";
  const hasData = Boolean(o && (o.has_actual || o.has_budget));
  const shownYears = years.length ? years : (o?.selected_years ?? []);
  const inv = inventory.data;

  return (
    <>
      <PageHeader
        title="Painel"
        subtitle={
          o?.last_closed_period
            ? `Olá, ${user?.name.split(" ")[0]}. Realizado ${o.reference_year} até ${monthName}${o.has_prev ? ` comparado ao mesmo período de ${o.previous_year}` : ""}.`
            : `Olá, ${user?.name.split(" ")[0]}. ${cycle ? cycle.name : ""}`
        }
      />

      {!isController && <ManagerTasks />}

      <FilterBar
        onApply={(v) => setFilters({ company_id: v.company_id, cost_center_id: v.cost_center_id, package_id: v.package_id })}
        fields={[
          {
            key: "company_id", label: "Empresa", value: filters.company_id,
            onChange: (v) => setFilters({ company_id: v, cost_center_id: "", package_id: filters.package_id }),
            options: [{ value: "", label: "Todas as empresas" }, ...companies.map((c) => ({ value: String(c.id), label: `${c.code} · ${c.short_name ?? c.name}` }))],
          },
          {
            key: "package_id", label: "Pacote GMD", value: filters.package_id,
            onChange: (v) => setFilters({ ...filters, package_id: v }),
            options: [{ value: "", label: "Todos os pacotes" }, ...packages.filter((p) => p.nature !== "CAPEX").map((p) => ({ value: String(p.id), label: `${p.roman ? `${p.roman} · ` : ""}${p.name}` }))],
          },
          {
            key: "cost_center_id", label: "Centro de custo", value: filters.cost_center_id, wide: true,
            onChange: (v) => setFilters({ ...filters, cost_center_id: v }),
            options: (d) => [
              { value: "", label: isController ? "Todos os centros de custo" : "Meus centros de custo" },
              ...ccs.filter((c) => !d.company_id || String(c.company_id) === d.company_id).map((c) => ({ value: String(c.id), label: `${c.code} · ${c.name}` })),
            ],
          },
        ]}
        extra={o && o.available_years.length > 1 && (
          <div className="year-tabs" role="group" aria-label="Anos exibidos (um ano mostra só ele; dois comparam)" aria-busy={overview.loading}>
            {[...o.available_years].sort().map((y) => {
              const on = shownYears.includes(y); // estado local: o chip responde ao clique antes da resposta do servidor
              return (
                <button key={y} type="button" className={on ? "active" : ""} aria-pressed={on} onClick={() => toggleYear(y, o.selected_years)}>
                  {y}
                </button>
              );
            })}
          </div>
        )}
      />

      {overview.error && <Alert>{overview.error}</Alert>}
      {!o ? (
        <Loading />
      ) : !hasData ? (
        <Alert tone="warn">
          Sem realizado ou orçamento de referência carregado{filters.company_id || filters.cost_center_id || filters.package_id ? " para estes filtros" : ""}.
          {isController && <> <Link className="link" to="/importacoes">Importar</Link> no layout da aba “Realizado”.</>} Abaixo, o que já existe na base.
        </Alert>
      ) : (
        <div className={overview.loading ? "is-loading" : undefined} aria-busy={overview.loading}>
          <div className="stats">
            {o.has_prev && (
              <>
                <Stat label={`Realizado ${o.previous_year} (ano)`} value={fmtCompact(o.kpis.prev_total)} hint={fmtMoney(o.kpis.prev_total)} />
                <Stat label={`Realizado ${prevLabel}`} value={fmtCompact(o.kpis.prev_ytd)} hint="base de comparação" />
              </>
            )}
            {o.has_actual && (
              <Stat label={`Realizado ${refLabel}`} value={fmtCompact(o.kpis.ref_ytd)} tone="warn" hint={fmtMoney(o.kpis.ref_ytd)} />
            )}
            {o.has_prev && o.has_actual && (
              <div className="stat stat-inline-delta">
                <span className="stat-label">Variação no período</span>
                <span className="stat-value"><Delta pct={o.kpis.ytd_var_pct} /></span>
                <span className="stat-hint">{o.reference_year} vs {o.previous_year}, mesmos meses</span>
              </div>
            )}
            {o.has_actual && o.last_closed_period && (
              <Stat
                label="Média mensal"
                value={fmtCompact(Number(o.kpis.ref_ytd) / o.last_closed_period)}
                hint={`${o.last_closed_period} mês(es) com realizado`}
              />
            )}
            {o.has_actual && o.last_closed_period !== 12 && (
              <Stat
                label={`${o.reference_year} anualizado`}
                value={fmtCompact(o.kpis.ref_annualized)}
                hint={o.has_prev && o.kpis.annualized_vs_prev_pct !== null ? `${fmtPct(o.kpis.annualized_vs_prev_pct)} vs ${o.previous_year} cheio` : "projeção linear"}
              />
            )}
            {o.has_budget && (
              <Stat
                label={`Orçado ${o.reference_year}`}
                value={fmtCompact(o.kpis.budget_total)}
                hint={o.kpis.budget_consumption_pct !== null ? `realizado ${fmtPct(o.kpis.budget_consumption_pct)} vs orçado no período` : undefined}
              />
            )}
          </div>

          <Card
            title="Evolução mensal do realizado"
            actions={
              <button className="btn btn-ghost btn-sm" onClick={() => setShowTable(!showTable)}>
                {showTable ? "Ver gráfico" : "Ver tabela"}
              </button>
            }
          >
            <Legend
              items={[
                ...(o.has_prev ? [{ label: String(o.previous_year), color: SERIES.prev }] : []),
                ...(o.has_actual ? [{ label: String(o.reference_year), color: SERIES.ref }] : []),
                ...(o.has_budget ? [{ label: `Orçado ${o.reference_year}`, color: SERIES.budget, line: true }] : []),
              ]}
            />
            {showTable ? (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Mês</th>
                      {o.has_prev && <th className="right">{o.previous_year}</th>}
                      <th className="right">{o.reference_year}</th>
                      {o.has_budget && <th className="right">Orçado {o.reference_year}</th>}
                      {o.has_prev && <th className="right">Var.</th>}
                    </tr>
                  </thead>
                  <tbody>
                    {o.monthly.map((m) => (
                      <tr key={m.month}>
                        <td>{MONTHS[m.month - 1]}</td>
                        {o.has_prev && <td className="right">{fmtMoney(m.prev)}</td>}
                        <td className="right">{Number(m.ref) ? fmtMoney(m.ref) : "—"}</td>
                        {o.has_budget && <td className="right">{fmtMoney(m.budget)}</td>}
                        {o.has_prev && (
                          <td className="right">
                            {Number(m.prev) && Number(m.ref) ? <Delta pct={String((Number(m.ref) - Number(m.prev)) / Number(m.prev))} /> : "—"}
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <MonthlyChart rows={o.monthly} prevYear={o.has_prev ? o.previous_year : null} refYear={o.reference_year} showBudget={o.has_budget} />
            )}
          </Card>

          {o.has_prev && o.has_actual && o.by_package.length > 0 && (
            <div className="grid-2">
              <Card title={`Ponte ${o.previous_year} → ${o.reference_year} por pacote · até ${monthName ?? "—"}`}>
                <p className="muted small">De onde vem a variação: cada barra é o aumento (▲) ou redução (▼) do pacote no mesmo período.</p>
                <Waterfall
                  start={{ label: prevLabel, value: Number(o.kpis.prev_ytd) }}
                  end={{ label: refLabel, value: Number(o.kpis.ref_ytd) }}
                  steps={o.by_package
                    .map((p) => ({ label: p.package, delta: Number(p.ref_ytd) - Number(p.prev_ytd) }))
                    .filter((p) => Math.abs(p.delta) > 0.5)
                    .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))}
                />
              </Card>
              <Card title={`Maiores aumentos e reduções por conta · até ${monthName ?? "—"}`}>
                {o.account_deltas.length ? (
                  <DivergingBars
                    rows={o.account_deltas.map((d) => ({ label: d.name, sub: d.code, delta: Number(d.delta), from: Number(d.prev_ytd), to: Number(d.ref_ytd) }))}
                  />
                ) : (
                  <Empty>Sem variações.</Empty>
                )}
              </Card>
            </div>
          )}

          {o.heatmap.rows.length > 0 && (
            <Card title={`Mapa de calor · maiores centros de custo × mês (${o.heatmap.year})`}>
              <Heatmap rows={o.heatmap.rows.map((r) => ({ label: r.name, sub: r.code, values: r.values.map(Number), total: Number(r.total) }))} />
            </Card>
          )}

          <div className="grid-2">
            <Card title={`Por pacote GMD · acumulado até ${monthName ?? "—"}`}>
              <Legend
                items={[
                  ...(o.has_prev ? [{ label: String(o.previous_year), color: SERIES.prev }] : []),
                  { label: String(o.reference_year), color: SERIES.ref },
                ]}
              />
              {o.by_package.length ? (
                <PairedBars
                  prevLabel={prevLabel}
                  refLabel={refLabel}
                  rows={o.by_package.map((p) => ({
                    label: p.package,
                    prev: Number(p.prev_ytd),
                    ref: Number(p.ref_ytd),
                    note: p.ytd_var_pct !== null ? fmtPct(p.ytd_var_pct) : undefined,
                  }))}
                />
              ) : (
                <Empty>Sem dados.</Empty>
              )}
            </Card>
            {isController && admin.data ? (
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
            ) : (
              <Card title={`Prazos · ${cycle?.name ?? ""}`}>
                <dl className="kv">
                  <dt>Status</dt>
                  <dd>{cycle ? <Badge tone={CYCLE_STATUS[cycle.status]?.tone ?? "neutral"}>{CYCLE_STATUS[cycle.status]?.label}</Badge> : "—"}</dd>
                  <dt>OPEX</dt>
                  <dd>{fmtDate(cycle?.opex_deadline ?? null)} <span className="muted small">{daysUntil(cycle?.opex_deadline ?? null)}</span></dd>
                  <dt>CAPEX</dt>
                  <dd>{fmtDate(cycle?.capex_deadline ?? null)} <span className="muted small">{daysUntil(cycle?.capex_deadline ?? null)}</span></dd>
                </dl>
              </Card>
            )}
          </div>

          <div className="grid-2">
            <Card title={`Maiores centros de custo · ${refLabel}`}>
              {o.has_prev && <p className="muted small">Barra fina: {prevLabel}. Percentual: variação entre os períodos.</p>}
              <RankChart rows={o.top_cost_centers} prevLabel={prevLabel} refLabel={refLabel} empty="Sem dados." showPrev={o.has_prev} />
            </Card>
            <Card title={`Maiores contas · ${refLabel}`}>
              {o.has_prev && <p className="muted small">Barra fina: {prevLabel}. Percentual: variação entre os períodos.</p>}
              <RankChart rows={o.top_accounts} prevLabel={prevLabel} refLabel={refLabel} empty="Sem dados." showPrev={o.has_prev} />
            </Card>
          </div>
        </div>
      )}

      {o?.budget_progress && o.budget_progress.total_cost_centers > 0 && <BudgetProgressCard progress={o.budget_progress} />}

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

      {!hasData && isController && admin.data && admin.data.quality.issues > 0 && (
        <Card title="Qualidade da base">
          <ul className="checks-list">
            {admin.data.quality.checks.filter((c) => c.severity !== "OK").map((c) => (
              <li key={c.code}>
                <Badge tone={SEVERITY[c.severity].tone}>{SEVERITY[c.severity].label}</Badge>
                <div>
                  <div>{c.title}{c.count > 0 && <strong> · {fmtInt(c.count)}</strong>}</div>
                  {c.detail && <div className="muted small">{c.detail}</div>}
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
function ManagerTasks() {
  const opex = useLoad(() => api<TaskSummary>("/opex/summary"));
  const capex = useLoad(() => api<TaskSummary>("/capex/summary"));
  const people = useLoad(() => api<TaskSummary>("/personnel/summary"));
  if (!opex.data || !capex.data || !people.data) return null;
  const ccs = new Map<number, TaskRow>();
  for (const r of [...opex.data.rows, ...capex.data.rows, ...people.data.rows]) if (!ccs.has(r.cost_center_id)) ccs.set(r.cost_center_id, r);
  if (ccs.size === 0) return <Alert tone="warn">Nenhum centro de custo está vinculado ao seu usuário. Peça à Controladoria para associar o seu CC.</Alert>;
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
