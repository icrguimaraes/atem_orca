import { useState, type ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
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
  type ReviewQueueItem,
} from "../api";
import { useAuth } from "../auth";
import { BudgetProgressCard } from "../components/BudgetProgressCard";
import { FilterBar } from "../components/FilterBar";
import { CumulativeChart, DivergingBars, Heatmap, Legend, MonthlyChart, SERIES, TopBars } from "../components/charts";
import { DrillTable } from "../components/DrillTable";
import { Alert, Badge, Card, Empty, Loading, PageHeader, Stat, useLoad } from "../components/ui";
import {
  DATASET_LABELS,
  IMPORT_STATUS,
  MONTHS,
  fmtCompact,
  fmtDate,
  fmtDateTime,
  fmtInt,
  fmtMoney,
  fmtPct,
  REVIEW_STATUS,
  SUBMISSION_STATUS,
} from "../labels";

const SEVERITY = {
  ERROR: { tone: "bad", label: "Erro" },
  WARNING: { tone: "warn", label: "Atenção" },
  INFO: { tone: "info", label: "Info" },
  OK: { tone: "good", label: "OK" },
} as const;

function Delta({ pct, invert = false }: { pct: string | null; invert?: boolean }) {
  if (pct === null) return null;
  const n = Number(pct);
  // em despesa, crescer é "pior": seta para cima em tom de alerta
  const cls = n === 0 ? "" : (n > 0) !== invert ? "up" : "down";
  return <span className={`delta ${cls}`}>{n > 0 ? "▲" : n < 0 ? "▼" : "•"} {fmtPct(pct)}</span>;
}

function RankChart({ rows, label, baseLabel, color, baseColor }: {
  rows: Overview["top_accounts"]; label: string; baseLabel: string | null; color: string; baseColor: string;
}) {
  if (!rows.length) return <Empty>Sem dados.</Empty>;
  return (
    <TopBars
      label={label}
      color={color}
      prevColor={baseColor}
      prevLabel={baseLabel}
      rows={rows.map((r) => ({
        label: r.name ?? r.code ?? "—",
        sub: r.code,
        value: Number(r.ref_ytd),
        prev: baseLabel ? Number(r.prev_ytd) : undefined,
        note: baseLabel && r.ytd_var_pct !== null ? fmtPct(r.ytd_var_pct) : undefined,
      }))}
    />
  );
}

// tipos de orçamento (módulos) do filtro "Tipo"
const BUDGET_TYPES = [
  { key: "OPEX", label: "OPEX" },
  { key: "CAPEX", label: "CAPEX" },
  { key: "PERSONNEL", label: "Pessoal" },
];

const ORDER_KEY = "atem.painel.order";
const MONTH_FULL = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"];
// blocos do painel que o usuário pode reordenar (os KPIs ficam sempre no topo)
const SECTIONS = [
  { key: "monthly", label: "Comparativo mensal" },
  { key: "table", label: "Tabela por pacote, conta e centro de custo" },
  { key: "cumulative", label: "Total acumulado" },
  { key: "top", label: "Maiores centros de custo e contas" },
  { key: "packages", label: "Pacotes GMD e variação por pacote" },
  { key: "bridge", label: "Maiores aumentos e reduções por conta" },
  { key: "heatmap", label: "Mapa de calor" },
];

/** Envolve um bloco do painel: posição visual pela ordem escolhida (CSS `order`) e, no modo organizar,
 * uma faixa com o nome do bloco e setas para subir/descer. */
function SectionShell({ id, index, count, organizing, onMove, children }: {
  id: string; index: number; count: number; organizing: boolean; onMove: (key: string, dir: -1 | 1) => void; children: ReactNode;
}) {
  const label = SECTIONS.find((s) => s.key === id)?.label ?? id;
  return (
    <div className="section-shell" style={{ order: index }}>
      {organizing && (
        <div className="section-organize">
          <span><strong>{index + 1}.</strong> {label}</span>
          <div className="inline-controls">
            <button type="button" className="btn btn-ghost btn-sm" disabled={index === 0} onClick={() => onMove(id, -1)} aria-label={`Subir ${label}`}>↑ Subir</button>
            <button type="button" className="btn btn-ghost btn-sm" disabled={index === count - 1} onClick={() => onMove(id, 1)} aria-label={`Descer ${label}`}>↓ Descer</button>
          </div>
        </div>
      )}
      {children}
    </div>
  );
}

/** "janeiro a setembro", "janeiro, março e maio" ou "" (todos os meses). */
function monthsLabel(months: number[]): string {
  if (!months.length) return "";
  const contiguous = months.every((m, i) => i === 0 || m === months[i - 1] + 1);
  if (contiguous && months.length > 1) return `${MONTH_FULL[months[0] - 1]} a ${MONTH_FULL[months[months.length - 1] - 1]}`;
  const names = months.map((m) => MONTH_FULL[m - 1]);
  return names.length > 1 ? `${names.slice(0, -1).join(", ")} e ${names[names.length - 1]}` : names[0];
}

export default function Home() {
  const { user, can } = useAuth();
  const isController = can("CONTROLLER");
  const isPlanner = can("CONTROLLER", "HR");
  const [searchParams] = useSearchParams();
  const [filters, setFilters] = useState({ company_id: "", cost_center_id: "", package_id: "" });
  const [showTable, setShowTable] = useState(false);
  // anos exibidos (somados): vazio = padrão do servidor (ano mais recente com realizado)
  const [years, setYears] = useState<number[]>([]);
  function toggleYear(y: number, current: number[]) {
    const base = years.length ? years : current;
    const next = base.includes(y) ? base.filter((x) => x !== y) : [...base, y];
    if (next.length) setYears(next.sort((a, b) => a - b));
  }
  // opções de comparação: com o ano anterior (só com um ano selecionado) e limitada ao mesmo período (até o mês fechado)
  const [compare, setCompare] = useState(true);
  const [samePeriod, setSamePeriod] = useState(true);
  // tipos de orçamento (vazio = todos); "?tipo=OPEX" vem dos endereços antigos /orcamento, /capex e /pessoal
  const [modules, setModules] = useState<string[]>(() => {
    const t = (searchParams.get("tipo") ?? "").toUpperCase();
    return BUDGET_TYPES.some((b) => b.key === t) ? [t] : [];
  });
  function toggleModule(m: string) {
    setModules((cur) => {
      const next = cur.includes(m) ? cur.filter((x) => x !== m) : [...cur, m];
      return next.length === BUDGET_TYPES.length ? [] : BUDGET_TYPES.map((b) => b.key).filter((k) => next.includes(k));
    });
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

  // meses exibidos (vazio = todos); afeta KPIs, gráficos e tabela
  const [months, setMonths] = useState<number[]>([]);
  function toggleMonth(m: number) {
    setMonths((cur) => {
      const next = cur.includes(m) ? cur.filter((x) => x !== m) : [...cur, m].sort((a, b) => a - b);
      return next.length === 12 ? [] : next;
    });
  }
  // ordem dos blocos do painel escolhida pelo usuário (guardada no navegador)
  const [order, setOrder] = useState<string[]>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(ORDER_KEY) ?? "[]") as string[];
      return [...saved.filter((k) => SECTIONS.some((s) => s.key === k)), ...SECTIONS.map((s) => s.key).filter((k) => !saved.includes(k))];
    } catch {
      return SECTIONS.map((s) => s.key);
    }
  });
  const [organizing, setOrganizing] = useState(false);
  function move(key: string, dir: -1 | 1) {
    setOrder((cur) => {
      const i = cur.indexOf(key);
      const j = i + dir;
      if (i < 0 || j < 0 || j >= cur.length) return cur;
      const next = [...cur];
      [next[i], next[j]] = [next[j], next[i]];
      try {
        localStorage.setItem(ORDER_KEY, JSON.stringify(next));
      } catch {
        /* sem armazenamento: a ordem vale só nesta visita */
      }
      return next;
    });
  }
  const shell = (key: string, children: ReactNode) => (
    <SectionShell key={key} id={key} index={order.indexOf(key)} count={order.length} organizing={organizing} onMove={move}>
      {children}
    </SectionShell>
  );

  const query = new URLSearchParams(
    Object.entries({
      ...filters,
      years: years.join(","),
      months: months.join(","),
      modules: modules.join(","),
      compare: compare ? "" : "false",
      same_period: samePeriod ? "" : "false",
    }).filter(([, v]) => v),
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
  const period = o?.period;
  // rótulos prontos do servidor: "Realizado 2026 até SET", "Orçamento 2027", "Realizado 2025 até SET"…
  const monthsTxt = months.length ? ` · ${monthsLabel(months)}` : "";
  const typesTxt = modules.length ? ` · ${modules.map((m) => BUDGET_TYPES.find((b) => b.key === m)?.label ?? m).join(" + ")}` : "";
  const mainLabel = period ? `${period.main_label}${monthsTxt}` : "";
  const baseLabel = period?.base_label ?? "";
  const actualLabel = period?.actual_label ?? "Realizado";
  const budgetLabel = period?.budget_label ?? "Orçado";
  // na série mensal o ano anterior aparece como é (sem anualizar)
  const prevSeriesLabel = period?.annualized_base && o?.previous_year ? `Realizado ${o.previous_year}` : baseLabel;
  const isBudgetMain = period?.main === "budget";
  const mainColor = isBudgetMain ? SERIES.budget : SERIES.ref; // azul = realizado; verde = orçamento, sempre
  const baseColor = period?.base_kind === "budget" ? SERIES.budget : SERIES.past;
  const hasBase = Boolean(period?.base_kind);
  const hasData = Boolean(o && (o.has_actual || o.has_budget));
  const shownYears = years.length ? years : (o?.selected_years ?? []);
  const filtered = Boolean(filters.company_id || filters.cost_center_id || filters.package_id || modules.length);
  const inv = inventory.data;

  return (
    <>
      <PageHeader
        title="Painel"
        subtitle={
          o && hasData
            ? `Olá, ${user?.name.split(" ")[0]}. ${mainLabel}${typesTxt}${hasBase ? ` comparado com ${baseLabel}` : ""}.`
            : `Olá, ${user?.name.split(" ")[0]}. ${cycle ? cycle.name : ""}`
        }
        actions={isPlanner && <Link to="/pessoal/simulacao" className="btn btn-ghost">Simular cenário de pessoal</Link>}
      />

      {!isController && <ManagerTasks warnWhenEmpty={Boolean(user?.roles.includes("MANAGER"))} />}
      <PackageReviews />

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
      />

      {o && (
        <>
          <div className="chip-groups" aria-busy={overview.loading}>
            <div className="chip-group">
              <span className="chip-label">Ano</span>
              <div className="year-tabs" role="group" aria-label="Anos exibidos (somados)">
                <button
                  type="button"
                  className={shownYears.length === o.available_years.length ? "active" : ""}
                  aria-pressed={shownYears.length === o.available_years.length}
                  onClick={() => setYears([...o.available_years].sort((a, b) => a - b))}
                >
                  Todos
                </button>
                {[...o.available_years].sort((a, b) => a - b).map((y) => {
                  const on = shownYears.includes(y); // estado local: o chip responde ao clique antes da resposta do servidor
                  return (
                    <button key={y} type="button" className={on ? "active" : ""} aria-pressed={on} onClick={() => toggleYear(y, o.selected_years)}>
                      {y}
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="chip-group">
              <span className="chip-label">Mês</span>
              <div className="month-chips" role="group" aria-label="Meses exibidos">
                <button type="button" className={months.length === 0 ? "active" : ""} aria-pressed={months.length === 0} onClick={() => setMonths([])}>
                  Todos
                </button>
                {MONTHS.map((m, i) => (
                  <button key={m} type="button" className={months.includes(i + 1) ? "active" : ""} aria-pressed={months.includes(i + 1)} onClick={() => toggleMonth(i + 1)}>
                    {m.charAt(0) + m.slice(1).toLowerCase()}
                  </button>
                ))}
              </div>
            </div>
            <div className="chip-group">
              <span className="chip-label">Tipo</span>
              <div className="month-chips" role="group" aria-label="Tipos de orçamento">
                <button type="button" className={modules.length === 0 ? "active" : ""} aria-pressed={modules.length === 0} onClick={() => setModules([])}>
                  Todos
                </button>
                {BUDGET_TYPES.map((b) => (
                  <button key={b.key} type="button" className={modules.includes(b.key) ? "active" : ""} aria-pressed={modules.includes(b.key)} onClick={() => toggleModule(b.key)}>
                    {b.label}
                  </button>
                ))}
              </div>
            </div>
          </div>
          <div className="section-tools">
            <div className="toggle-options" role="group" aria-label="Opções de comparação">
              <label className={period?.compare_available ? "" : "off"} title={period?.compare_available ? "" : "Disponível com um único ano selecionado e o ano anterior carregado"}>
                <input type="checkbox" checked={compare} disabled={!period?.compare_available} onChange={(e) => setCompare(e.target.checked)} />
                Comparar com o ano anterior
              </label>
              <label className={period?.same_period_available ? "" : "off"} title={period?.same_period_available ? "" : "Disponível quando o realizado do ano ainda está em andamento, sem filtro de meses"}>
                <input type="checkbox" checked={samePeriod} disabled={!period?.same_period_available} onChange={(e) => setSamePeriod(e.target.checked)} />
                Mesmo período{period?.closed_month && (period.closed ?? 12) < 12 ? ` (até ${period.closed_month})` : ""}
              </label>
            </div>
            <div className="inline-controls">
              <span className="selection-note">
                Selecionado: {mainLabel}{typesTxt}
                {hasBase ? ` · base: ${baseLabel}` : ""}
              </span>
              <button type="button" className="btn btn-ghost btn-sm" aria-pressed={organizing} onClick={() => setOrganizing(!organizing)}>
                {organizing ? "Concluir" : "Organizar painel"}
              </button>
            </div>
          </div>
        </>
      )}

      {overview.error && <Alert>{overview.error}</Alert>}
      {!o ? (
        <Loading />
      ) : !hasData ? (
        <Alert tone="warn">
          {o.target_year !== null && o.selected_years.every((y) => y === o.target_year) ? (
            <>Ainda não há valores lançados no orçamento {o.target_year}{filtered ? " para estes filtros" : ""}. Os gestores preenchem pelo quadro “Suas tarefas”.</>
          ) : (
            <>
              Sem realizado ou orçamento de referência carregado{filtered ? " para estes filtros" : ""}.
              {isController && <> <Link className="link" to="/importacoes">Importar</Link> no layout da aba “Realizado”.</>} Abaixo, o que já existe na base.
            </>
          )}
        </Alert>
      ) : (
        <div className={`panel-body${overview.loading ? " is-loading" : ""}`} aria-busy={overview.loading}>
          <div className="stats">
            {period?.base_kind === "prev" && !period.annualized_base && o.kpis.prev_total !== o.kpis.prev_ytd && (
              <Stat label={`Realizado ${o.previous_year} (ano cheio)`} value={fmtCompact(o.kpis.prev_total)} hint={fmtMoney(o.kpis.prev_total)} />
            )}
            {hasBase && <Stat label={baseLabel} value={fmtCompact(o.kpis.prev_ytd)} hint="base de comparação" />}
            <Stat label={mainLabel} value={fmtCompact(o.kpis.ref_ytd)} hint={fmtMoney(o.kpis.ref_ytd)} />
            {hasBase && (
              <div className="stat stat-inline-delta">
                <span className="stat-label">Variação</span>
                <span className="stat-value"><Delta pct={o.kpis.ytd_var_pct} /></span>
                <span className="stat-hint">vs {baseLabel}</span>
              </div>
            )}
            {o.has_actual && o.selected_years.length === 1 && o.last_closed_period && !months.length && (
              <Stat
                label="Média mensal"
                value={fmtCompact(Number(o.kpis.actual_total) / o.last_closed_period)}
                hint={`${o.last_closed_period} mês(es) com realizado`}
              />
            )}
            {Number(o.kpis.ref_annualized) > 0 && (
              <Stat
                label={`${o.reference_year} anualizado`}
                value={fmtCompact(o.kpis.ref_annualized)}
                hint={o.has_prev && o.kpis.annualized_vs_prev_pct !== null ? `${fmtPct(o.kpis.annualized_vs_prev_pct)} vs ${o.previous_year} cheio` : "projeção linear"}
              />
            )}
            {o.has_budget && !isBudgetMain && (period?.base_kind !== "budget" || o.kpis.budget_total !== o.kpis.prev_ytd) && (
              <Stat
                label={budgetLabel}
                value={fmtCompact(o.kpis.budget_total)}
                hint={o.kpis.budget_consumption_pct !== null ? `realizado ${fmtPct(o.kpis.budget_consumption_pct)} do orçado no período` : undefined}
              />
            )}
          </div>

          <div className="section-stack">
          {shell("monthly",
          <Card
            title="Comparativo mensal"
            actions={
              <button className="btn btn-ghost btn-sm" onClick={() => setShowTable(!showTable)}>
                {showTable ? "Ver gráfico" : "Ver tabela"}
              </button>
            }
          >
            <Legend
              items={[
                ...(o.has_prev ? [{ label: prevSeriesLabel, color: SERIES.past }] : []),
                ...(o.has_actual ? [{ label: actualLabel, color: SERIES.ref }] : []),
                ...(o.has_budget ? [{ label: budgetLabel, color: SERIES.budget }] : []),
              ]}
            />
            {showTable ? (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Mês</th>
                      {o.has_prev && <th className="right">{prevSeriesLabel}</th>}
                      {o.has_actual && <th className="right">{actualLabel}</th>}
                      {o.has_budget && <th className="right">{budgetLabel}</th>}
                      {o.has_prev && o.has_actual && <th className="right">Var.</th>}
                    </tr>
                  </thead>
                  <tbody>
                    {o.monthly.map((m) => (
                      <tr key={m.month}>
                        <td>{MONTHS[m.month - 1]}</td>
                        {o.has_prev && <td className="right">{fmtMoney(m.prev)}</td>}
                        {o.has_actual && <td className="right">{Number(m.ref) ? fmtMoney(m.ref) : "—"}</td>}
                        {o.has_budget && <td className="right">{fmtMoney(m.budget)}</td>}
                        {o.has_prev && o.has_actual && (
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
              <MonthlyChart
                rows={o.monthly}
                prevYear={o.has_prev ? o.previous_year : null}
                refYear={o.reference_year}
                showRef={o.has_actual}
                showBudget={o.has_budget}
                prevColor={SERIES.past}
                refLabel={actualLabel}
                prevLabel={prevSeriesLabel}
                budgetLabel={budgetLabel}
              />
            )}
          </Card>,
          )}

          {shell("table",
          <Card title={`Por pacote GMD, conta e centro de custo · ${mainLabel}${typesTxt}`}>
            <DrillTable query={query} refLabel={mainLabel} />
          </Card>,
          )}

          {shell("cumulative",
          <Card title={`Total acumulado · ${mainLabel}${o.has_actual && o.has_budget ? ` vs ${budgetLabel}` : o.has_actual && o.has_prev ? ` vs ${prevSeriesLabel}` : ""}`}>
            <Legend
              items={[
                ...(o.has_actual ? [{ label: actualLabel, color: SERIES.ref }] : []),
                ...(o.has_budget ? [{ label: budgetLabel, color: SERIES.budget }] : o.has_prev ? [{ label: prevSeriesLabel, color: SERIES.past }] : []),
              ]}
            />
            <CumulativeChart
              rows={o.monthly}
              prevYear={o.has_prev ? o.previous_year : null}
              refYear={o.reference_year}
              showBudget={o.has_budget}
              prevColor={SERIES.past}
              refLabel={actualLabel}
              prevLabel={prevSeriesLabel}
              budgetLabel={budgetLabel}
            />
          </Card>,
          )}

          {/* ordem padrão por relevância; o usuário pode reordenar em "Organizar painel" */}
          {shell("top",
          <div className="grid-2">
            <Card title={`Maiores centros de custo · ${mainLabel}`}>
              {hasBase && <p className="muted small">Barra fina: {baseLabel}. Percentual: variação em relação à base.</p>}
              <RankChart rows={o.top_cost_centers} label={mainLabel} baseLabel={hasBase ? baseLabel : null} color={mainColor} baseColor={baseColor} />
            </Card>
            <Card title={`Maiores contas · ${mainLabel}`}>
              {hasBase && <p className="muted small">Barra fina: {baseLabel}. Percentual: variação em relação à base.</p>}
              <RankChart rows={o.top_accounts} label={mainLabel} baseLabel={hasBase ? baseLabel : null} color={mainColor} baseColor={baseColor} />
            </Card>
          </div>,
          )}

          {shell("packages",
          <div className="grid-2">
            <Card title={`Por pacote GMD · ${mainLabel}`}>
              {hasBase && <p className="muted small">Barra fina: {baseLabel}. Percentual: variação em relação à base.</p>}
              {o.by_package.filter((p) => Number(p.ref_ytd) || Number(p.prev_ytd)).length ? (
                <TopBars
                  label={mainLabel}
                  color={mainColor}
                  prevColor={baseColor}
                  prevLabel={hasBase ? baseLabel : null}
                  rows={o.by_package
                    .filter((p) => Number(p.ref_ytd) || Number(p.prev_ytd))
                    .sort((a, b) => Number(b.ref_ytd) - Number(a.ref_ytd))
                    .map((p) => ({
                      label: p.package,
                      value: Number(p.ref_ytd),
                      prev: hasBase ? Number(p.prev_ytd) : undefined,
                      note: hasBase && p.ytd_var_pct !== null ? fmtPct(p.ytd_var_pct) : undefined,
                    }))}
                />
              ) : (
                <Empty>Sem dados.</Empty>
              )}
            </Card>
            {hasBase && o.by_package.length > 0 && (
              <Card title={`Variação por pacote · vs ${baseLabel}`}>
                <p className="muted small">
                  De {fmtMoney(o.kpis.prev_ytd)} para {fmtMoney(o.kpis.ref_ytd)}: cada barra é o aumento (vermelho) ou a redução (azul) do pacote.
                </p>
                <DivergingBars
                  fromLabel={baseLabel}
                  toLabel={mainLabel}
                  rows={o.by_package
                    .map((p) => ({ label: p.package, delta: Number(p.ref_ytd) - Number(p.prev_ytd), from: Number(p.prev_ytd), to: Number(p.ref_ytd) }))
                    .filter((p) => Math.abs(p.delta) > 0.5)
                    .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))}
                />
              </Card>
            )}
          </div>,
          )}

          {hasBase && o.account_deltas.length > 0 && shell("bridge",
            <Card title={`Maiores aumentos e reduções por conta · vs ${baseLabel}`}>
              <DivergingBars
                fromLabel={baseLabel}
                toLabel={mainLabel}
                rows={o.account_deltas.map((d) => ({ label: d.name, sub: d.code, delta: Number(d.delta), from: Number(d.prev_ytd), to: Number(d.ref_ytd) }))}
              />
            </Card>,
          )}

          {o.heatmap.rows.length > 0 && shell("heatmap",
            <Card title={`Mapa de calor · maiores centros de custo × mês (${mainLabel})`}>
              <Heatmap budget={isBudgetMain} rows={o.heatmap.rows.map((r) => ({ label: r.name, sub: r.code, values: r.values.map(Number), total: Number(r.total) }))} />
            </Card>,
          )}
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
function ManagerTasks({ warnWhenEmpty }: { warnWhenEmpty: boolean }) {
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
function PackageReviews() {
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
