import { useCallback, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type Company, type CostCenter, type Cycle, type Overview, type Package } from "../api";
import { useAuth } from "../auth";
import { FilterBar } from "../components/FilterBar";
import { Heatmap } from "../components/charts";
import { DrillTable } from "../components/DrillTable";
import { PlotlyChart, type Figure } from "../components/PlotlyChart";
import { Alert, Card, Empty, Loading, PageHeader, Stat, useLoad } from "../components/ui";
import { MONTHS, fmtCompact, fmtMoney, fmtPct } from "../labels";

/**
 * Painel 2: o mesmo Painel (filtros, números, blocos e tabela), com os gráficos em Plotly montados no backend
 * (`services/painel_figures.py`, a partir de `/dashboard/overview?figures=true`). Existe para comparação com o
 * Painel em SVG; depois da escolha, uma das duas versões sai. Clique em mês ou centro de custo filtra.
 */
type OverviewWithFigures = Overview & { figures: Record<string, Figure> };

const ORDER_KEY = "atem.painel2.order";
const MONTH_FULL = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"];
const BUDGET_TYPES = [
  { key: "OPEX", label: "OPEX" },
  { key: "CAPEX", label: "CAPEX" },
  { key: "PERSONNEL", label: "Pessoal" },
];
const SECTIONS = [
  { key: "monthly", label: "Comparativo mensal" },
  { key: "table", label: "Tabela por pacote, conta e centro de custo" },
  { key: "cumulative", label: "Total acumulado" },
  { key: "top", label: "Maiores centros de custo e contas" },
  { key: "heatmap", label: "Mapa de calor" },
];

function Delta({ pct }: { pct: string | null }) {
  if (pct === null) return null;
  const n = Number(pct);
  const cls = n === 0 ? "" : n > 0 ? "up" : "down";
  return <span className={`delta ${cls}`}>{n > 0 ? "▲" : n < 0 ? "▼" : "•"} {fmtPct(pct)}</span>;
}

function monthsLabel(months: number[]): string {
  if (!months.length) return "";
  const contiguous = months.every((m, i) => i === 0 || m === months[i - 1] + 1);
  if (contiguous && months.length > 1) return `${MONTH_FULL[months[0] - 1]} a ${MONTH_FULL[months[months.length - 1] - 1]}`;
  const names = months.map((m) => MONTH_FULL[m - 1]);
  return names.length > 1 ? `${names.slice(0, -1).join(", ")} e ${names[names.length - 1]}` : names[0];
}

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

/** id da linha clicada (customdata[3] das figuras de ranking) ou mês (customdata[1] do comparativo mensal). */
function pick(customdata: unknown, index: number): number | null {
  const v = Array.isArray(customdata) ? customdata[index] : null;
  return v === null || v === undefined || v === "" ? null : Number(v);
}

export default function Painel2() {
  const { can } = useAuth();
  const isController = can("CONTROLLER");
  const [searchParams] = useSearchParams();
  const [filters, setFilters] = useState({ company_id: "", cost_center_id: "", package_id: "" });
  const [years, setYears] = useState<number[]>([]);
  function toggleYear(y: number, current: number[]) {
    const base = years.length ? years : current;
    const next = base.includes(y) ? base.filter((x) => x !== y) : [...base, y];
    if (next.length) setYears(next.sort((a, b) => a - b));
  }
  const [compare, setCompare] = useState(true);
  const [samePeriod, setSamePeriod] = useState(true);
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
  const [months, setMonths] = useState<number[]>([]);
  const toggleMonth = useCallback((m: number) => {
    setMonths((cur) => {
      const next = cur.includes(m) ? cur.filter((x) => x !== m) : [...cur, m].sort((a, b) => a - b);
      return next.length === 12 ? [] : next;
    });
  }, []);
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

  // cliques nos gráficos filtram o painel, como no Power BI
  const onMonth = useCallback((cd: unknown) => {
    const m = pick(cd, 1);
    if (m && m >= 1 && m <= 12) toggleMonth(m);
  }, [toggleMonth]);
  const onCostCenter = useCallback((cd: unknown) => {
    const id = pick(cd, 3);
    if (id) setFilters((f) => ({ ...f, cost_center_id: String(id) }));
  }, []);

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
    Object.entries({
      ...filters,
      years: years.join(","),
      months: months.join(","),
      modules: modules.join(","),
      compare: compare ? "" : "false",
      same_period: samePeriod ? "" : "false",
    }).filter(([, v]) => v),
  ).toString();
  const overview = useLoad(() => api<OverviewWithFigures>(`/dashboard/overview?figures=true${query ? `&${query}` : ""}`), [query]);

  if (!base.data) return <Loading />;
  const { cycle, companies, ccs, packages } = base.data;
  const o = overview.data;
  const f = o?.figures;
  const period = o?.period;
  const monthsTxt = months.length ? ` · ${monthsLabel(months)}` : "";
  const typesTxt = modules.length ? ` · ${modules.map((m) => BUDGET_TYPES.find((b) => b.key === m)?.label ?? m).join(" + ")}` : "";
  const mainLabel = period ? `${period.main_label}${monthsTxt}` : "";
  const baseLabel = period?.base_label ?? "";
  const budgetLabel = period?.budget_label ?? "Orçado";
  const isBudgetMain = period?.main === "budget";
  const hasBase = Boolean(period?.base_kind);
  const hasData = Boolean(o && (o.has_actual || o.has_budget));
  const shownYears = years.length ? years : (o?.selected_years ?? []);
  const filtered = Boolean(filters.company_id || filters.cost_center_id || filters.package_id || modules.length);

  return (
    <>
      <PageHeader
        title="Painel 2"
        subtitle={
          o && hasData
            ? `Versão em Plotly, para comparar com o Painel. ${mainLabel}${typesTxt}${hasBase ? ` comparado com ${baseLabel}` : ""}.`
            : `Versão em Plotly, para comparar com o Painel. ${cycle ? cycle.name : ""}`
        }
        actions={<Link to="/" className="btn btn-ghost">Abrir o Painel</Link>}
      />

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
                  const on = shownYears.includes(y);
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
                {hasBase ? ` · base: ${baseLabel}` : ""} · clique num mês ou centro de custo para filtrar
              </span>
              <button type="button" className="btn btn-ghost btn-sm" aria-pressed={organizing} onClick={() => setOrganizing(!organizing)}>
                {organizing ? "Concluir" : "Organizar painel"}
              </button>
            </div>
          </div>
        </>
      )}

      {overview.error && <Alert>{overview.error}</Alert>}
      {!o || !f ? (
        <Loading />
      ) : !hasData ? (
        <Alert tone="warn">
          {o.target_year !== null && o.selected_years.every((y) => y === o.target_year)
            ? <>Ainda não há valores lançados no orçamento {o.target_year}{filtered ? " para estes filtros" : ""}.</>
            : <>Sem realizado ou orçamento de referência carregado{filtered ? " para estes filtros" : ""}.</>}
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
              <Stat label="Média mensal" value={fmtCompact(Number(o.kpis.actual_total) / o.last_closed_period)} hint={`${o.last_closed_period} mês(es) com realizado`} />
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
              <Card title="Comparativo mensal">
                <PlotlyChart figure={f.monthly} height={360} onClick={onMonth} ariaLabel="Comparativo mensal" />
              </Card>,
            )}

            {shell("table",
              <Card title={`Por pacote GMD, conta e centro de custo · ${mainLabel}${typesTxt}`}>
                <DrillTable query={query} refLabel={mainLabel} />
              </Card>,
            )}

            {shell("cumulative",
              <Card title={`Total acumulado · ${mainLabel}`}>
                <PlotlyChart figure={f.cumulative} height={340} ariaLabel="Total acumulado" />
              </Card>,
            )}

            {shell("top",
              <div className="grid-2">
                <Card title={`Maiores centros de custo · ${mainLabel}`}>
                  {hasBase && <p className="muted small">Barra fina: {baseLabel}. Clique num centro de custo para filtrar.</p>}
                  {o.top_cost_centers.length ? <PlotlyChart figure={f.top_cost_centers} onClick={onCostCenter} ariaLabel="Maiores centros de custo" /> : <Empty>Sem dados.</Empty>}
                </Card>
                <Card title={`Maiores contas · ${mainLabel}`}>
                  {hasBase && <p className="muted small">Barra fina: {baseLabel}.</p>}
                  {o.top_accounts.length ? <PlotlyChart figure={f.top_accounts} ariaLabel="Maiores contas" /> : <Empty>Sem dados.</Empty>}
                </Card>
              </div>,
            )}

            {o.heatmap.rows.length > 0 && shell("heatmap",
              <Card title={`Mapa de calor · maiores centros de custo × mês (${mainLabel})`}>
                <Heatmap budget={isBudgetMain} rows={o.heatmap.rows.map((r) => ({ label: r.name, sub: r.code, values: r.values.map(Number), total: Number(r.total) }))} />
              </Card>,
            )}
          </div>
        </div>
      )}
    </>
  );
}
