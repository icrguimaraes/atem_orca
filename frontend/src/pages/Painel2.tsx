import { useCallback, useState, type ReactNode } from "react";
import { usePersistentState } from "../persist";
import { Link, useSearchParams } from "react-router-dom";
import { api, type Breakdown, type BreakdownRow, type Company, type CostCenter, type Cycle, type Department, type Overview, type Package } from "../api";
import { useAuth } from "../auth";
import { BudgetProgressCard } from "../components/BudgetProgressCard";
import { ManagerTasks, PackageReviews, PainelBase, QuestionsInbox } from "../components/PainelBlocks";
import { FilterBar } from "../components/FilterBar";
import { DrillTable } from "../components/DrillTable";
import { PlotlyChart, type Figure } from "../components/PlotlyChart";
import { Alert, Card, Empty, Loading, PageHeader, Stat, lines, useLoad } from "../components/ui";
import { MONTHS, fmtCompact, fmtMoney, fmtPct, fmtSignedMoney } from "../labels";

/**
 * Painel (rota /): filtros, números, blocos e tabela, com os gráficos em Plotly montados no backend
 * (`services/painel_figures.py`, a partir de `/dashboard/overview?figures=true`). Existe para comparação com o
 * Painel em SVG; depois da escolha, uma das duas versões sai. Clique nos gráficos e na tabela filtra; "Limpar filtros" volta ao início.
 */
type OverviewWithFigures = Overview & {
  figures: Record<string, Figure>;
  top_cost_centers_total?: { main: string; base: string | null; var_pct: string | null };
};

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
  { key: "top", label: "Maiores centros de custo" },
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

/** Saudação pelo horário do navegador. */
function greeting(): string {
  const h = new Date().getHours();
  return h < 12 ? "Bom dia" : h < 18 ? "Boa tarde" : "Boa noite";
}

export default function Painel2() {
  const { user, can } = useAuth();
  const isController = can("CONTROLLER");
  const isPlanner = can("CONTROLLER", "HR");
  const [searchParams] = useSearchParams();
  const [filters, setFilters] = usePersistentState("painel.filters", { company_id: "", cost_center_id: "", package_id: "" });
  const [years, setYears] = usePersistentState<number[]>("painel.years", []);
  function toggleYear(y: number, current: number[]) {
    const base = years.length ? years : current;
    const next = base.includes(y) ? base.filter((x) => x !== y) : [...base, y];
    if (next.length) setYears(next.sort((a, b) => a - b));
  }
  const [compare, setCompare] = usePersistentState("painel.compare", false); // desmarcado por padrão (pedido de 08/10/2026)
  const [samePeriod, setSamePeriod] = usePersistentState("painel.samePeriod", true);
  const tipo = (searchParams.get("tipo") ?? "").toUpperCase();
  const tipoFromUrl = BUDGET_TYPES.some((b) => b.key === tipo);
  // ?tipo= (links de outras páginas) vale sobre o filtro guardado
  const [modules, setModules] = usePersistentState<string[]>("painel.modules", tipoFromUrl ? [tipo] : [], tipoFromUrl);
  function toggleModule(m: string) {
    setModules((cur) => {
      const next = cur.includes(m) ? cur.filter((x) => x !== m) : [...cur, m];
      return next.length === BUDGET_TYPES.length ? [] : BUDGET_TYPES.map((b) => b.key).filter((k) => next.includes(k));
    });
  }
  const [months, setMonths] = usePersistentState<number[]>("painel.months", []);
  const [department, setDepartment] = usePersistentState("painel.department", ""); // área (Controladoria, Tributos…); vazio = todas
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

  // filtro por conta: só existe por clique (nos rankings ou na tabela); aparece como etiqueta removível
  const [account, setAccount] = usePersistentState<{ id: number; label: string } | null>("painel.account", null);

  // cliques nos visuais filtram o painel, como no Power BI
  const onMonth = useCallback((cd: unknown) => {
    const m = pick(cd, 1);
    if (m && m >= 1 && m <= 12) toggleMonth(m);
  }, [toggleMonth]);
  // clicar de novo no item escolhido desmarca (como no Power BI)
  const toggleCostCenter = useCallback((id: number) => {
    setFilters((f) => ({ ...f, cost_center_id: f.cost_center_id === String(id) ? "" : String(id) }));
  }, []);
  const onCostCenter = useCallback((cd: unknown) => {
    const id = pick(cd, 3);
    if (id) toggleCostCenter(id);
  }, [toggleCostCenter]);
  function onTableRow(level: Breakdown["group_by"], row: BreakdownRow) {
    if (row.id === null) return;
    const id = row.id;
    if (level === "package") setFilters((f) => ({ ...f, package_id: f.package_id === String(id) ? "" : String(id) }));
    else if (level === "account") setAccount((a) => (a?.id === id ? null : { id, label: row.code ? `${row.name} · ${row.code}` : row.name }));
    else if (level === "cost_center") toggleCostCenter(id);
  }
  function clearAll() {
    setFilters({ company_id: "", cost_center_id: "", package_id: "" });
    setAccount(null);
    setYears([]);
    setMonths([]);
    setModules([]);
    setDepartment("");
    setCompare(false);
    setSamePeriod(true);
  }
  const activeFilters =
    [filters.company_id, filters.cost_center_id, filters.package_id].filter(Boolean).length +
    (account ? 1 : 0) + (years.length ? 1 : 0) + (months.length ? 1 : 0) + (modules.length ? 1 : 0) + (department ? 1 : 0);

  const base = useLoad(async () => {
    const [cycles, companies, ccs, packages, departments] = await Promise.all([
      api<Cycle[]>("/cycles"),
      api<Company[]>("/companies"),
      api<CostCenter[]>("/cost-centers"),
      api<Package[]>("/packages"),
      api<Department[]>("/departments"),
    ]);
    return { cycle: cycles[0] ?? null, companies, ccs, packages, departments };
  });
  const query = new URLSearchParams(
    Object.entries({
      ...filters,
      account_id: account ? String(account.id) : "",
      department_id: department,
      years: years.join(","),
      months: months.join(","),
      modules: modules.join(","),
      compare: compare ? "" : "false",
      same_period: samePeriod ? "" : "false",
    }).filter(([, v]) => v),
  ).toString();
  const overview = useLoad(() => api<OverviewWithFigures>(`/dashboard/overview?figures=true${query ? `&${query}` : ""}`), [query]);

  if (!base.data) return <Loading />;
  const { companies, ccs, packages, departments } = base.data;
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
  const filtered = Boolean(filters.company_id || filters.cost_center_id || filters.package_id || account || modules.length);
  const unpick = (label: string, clear: () => void) => (
    <button type="button" className="btn btn-ghost btn-sm" onClick={clear}>{label}</button>
  );

  return (
    <>
      <PageHeader
        title="Painel"
        subtitle={<p className="greeting">{greeting()}, {user?.name.split(" ")[0]}!</p>}
        actions={
          <>
            {isPlanner && <Link to="/pessoal/simulacao" className="btn btn-ghost">Simular cenário de pessoal</Link>}
          </>
        }
      />

      {!isController && <ManagerTasks warnWhenEmpty={Boolean(user?.roles.includes("MANAGER"))} />}
      <PackageReviews />
      <QuestionsInbox />

      <FilterBar
        onApply={(v) => setFilters({ ...filters, cost_center_id: v.cost_center_id })}
        onReset={clearAll}
        resetCount={activeFilters + (compare ? 1 : 0) + (samePeriod ? 0 : 1)}
        lead={
          <>
            <div className="chip-group">
              <span className="chip-label">Empresa</span>
              <div className="month-chips" role="group" aria-label="Empresas">
                <button type="button" className={!filters.company_id ? "active" : ""} aria-pressed={!filters.company_id} onClick={() => setFilters({ ...filters, company_id: "", cost_center_id: "" })}>
                  Todas
                </button>
                {companies.map((c) => {
                  const picked = filters.company_id ? filters.company_id.split(",") : [];
                  const on = picked.includes(String(c.id));
                  return (
                    <button
                      key={c.id}
                      type="button"
                      className={on ? "active" : ""}
                      aria-pressed={on}
                      title={`${c.code} · ${c.name}`}
                      onClick={() => {
                        const next = on ? picked.filter((x) => x !== String(c.id)) : [...picked, String(c.id)];
                        setFilters({ ...filters, company_id: next.length === companies.length ? "" : next.join(","), cost_center_id: "" });
                      }}
                    >
                      {c.short_name ?? c.name}
                    </button>
                  );
                })}
              </div>
            </div>
            {departments.length > 0 && (
              <div className="chip-group">
                <span className="chip-label">Área</span>
                <div className="month-chips" role="group" aria-label="Áreas">
                  <button type="button" className={!department ? "active" : ""} aria-pressed={!department} onClick={() => setDepartment("")}>
                    Todas
                  </button>
                  {[...departments].sort((a, b) => a.name.localeCompare(b.name)).map((d) => (
                    <button
                      key={d.id}
                      type="button"
                      className={department.split(",").includes(String(d.id)) ? "active" : ""}
                      aria-pressed={department.split(",").includes(String(d.id))}
                      onClick={() => setDepartment((cur) => { const picked = cur ? cur.split(",") : []; const next = picked.includes(String(d.id)) ? picked.filter((x) => x !== String(d.id)) : [...picked, String(d.id)]; return next.length === departments.length ? "" : next.join(","); })}
                    >
                      {d.name}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </>
        }
        fields={[
          {
            key: "cost_center_id", label: "Centro de custo", value: filters.cost_center_id, wide: true,
            onChange: (v) => setFilters({ ...filters, cost_center_id: v }),
            options: [
              { value: "", label: isController ? "Todos os centros de custo" : "Meus centros de custo" },
              ...ccs.filter((c) => (!filters.company_id || filters.company_id.split(",").includes(String(c.company_id))) && (!department || department.split(",").includes(String(c.department_id)))).map((c) => ({ value: String(c.id), label: `${c.code} · ${c.name}` })),
            ],
          },
        ]}
        extra={
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
        }
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
              <span className="chip-label">Pacote</span>
              <select value={filters.package_id} aria-label="Pacote GMD" onChange={(e) => setFilters({ ...filters, package_id: e.target.value })}>
                <option value="">Todos os pacotes</option>
                {packages.filter((p) => p.nature !== "CAPEX").map((p) => (
                  <option key={p.id} value={String(p.id)}>{`${p.roman ? `${p.roman} · ` : ""}${p.name}`}</option>
                ))}
              </select>
            </div>
          </div>
          {activeFilters > 0 && (
            <div className="active-filters" aria-label="Filtros ativos">
              <span className="muted small">Filtros ativos (guardados da sua última visita):</span>
              {filters.company_id && <span className="filter-chip">Empresa: {filters.company_id.split(",").map((i) => companies.find((c) => String(c.id) === i)?.short_name ?? i).join(" + ")}</span>}
              {department && <span className="filter-chip">Área: {department.split(",").map((i) => departments.find((d) => String(d.id) === i)?.name ?? i).join(" + ")}</span>}
              {filters.cost_center_id && <span className="filter-chip">Centro de custo: {ccs.find((c) => String(c.id) === filters.cost_center_id)?.code ?? filters.cost_center_id}</span>}
              {modules.length > 0 && <span className="filter-chip">Tipo: {modules.map((m) => BUDGET_TYPES.find((b) => b.key === m)?.label ?? m).join(" + ")}</span>}
              {months.length > 0 && <span className="filter-chip">Mês: {months.map((m) => MONTHS[m - 1]).join(", ")}</span>}
              {filters.package_id && <span className="filter-chip">Pacote: {packages.find((p) => String(p.id) === filters.package_id)?.name ?? filters.package_id}</span>}
              <button type="button" className="btn btn-ghost btn-sm" onClick={clearAll}>Limpar tudo</button>
            </div>
          )}
          {account && (
            <div className="active-filters" aria-label="Filtros aplicados por clique">
              <span className="filter-chip">
                Conta: {account.label}
                <button type="button" aria-label="Remover o filtro de conta" title="Remover o filtro de conta" onClick={() => setAccount(null)}>×</button>
              </span>
            </div>
          )}
          <div className="section-tools">
            <div className="toggle-options" role="group" aria-label="Opções de comparação">
              <label className={period?.compare_available ? "" : "off"} title={period?.compare_available ? "" : "Disponível com um único ano selecionado e o ano anterior carregado"}>
                <input type="checkbox" checked={compare} disabled={!period?.compare_available} onChange={(e) => setCompare(e.target.checked)} />
                Comparar com o ano anterior
              </label>
              <label title={period?.same_period_available ? "Limita a base de comparação aos meses já fechados do realizado" : "Vale quando há comparação (ano anterior ou orçado) e o realizado do ano ainda está em andamento, sem filtro de meses"}>
                <input type="checkbox" checked={samePeriod} onChange={(e) => setSamePeriod(e.target.checked)} />
                Mesmo período{period?.closed_month && (period.closed ?? 12) < 12 ? ` (até ${period.closed_month})` : ""}
              </label>
            </div>
            <div className="inline-controls">
              <span className="selection-note">
                Selecionado: <strong>{mainLabel}{typesTxt}</strong>
                {hasBase && <> · comparado com <strong>{baseLabel}</strong></>}
                <span className="selection-hint"> · clique nos gráficos e na tabela para filtrar</span>
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
            {hasBase && <Stat label={baseLabel} value={fmtCompact(o.kpis.prev_ytd)} hint={lines(fmtMoney(o.kpis.prev_ytd), "base de comparação")} />}
            <Stat
              label={mainLabel}
              value={fmtCompact(o.kpis.ref_ytd)}
              hint={lines(
                fmtMoney(o.kpis.ref_ytd),
                isBudgetMain && Number(o.kpis.budget_unscheduled) > 0 && `inclui ${fmtMoney(o.kpis.budget_unscheduled)} de CAPEX sem cronograma mensal`,
              )}
            />
            {hasBase && (
              <div className="stat stat-inline-delta">
                <span className="stat-label">Variação</span>
                <span className="stat-value"><Delta pct={o.kpis.ytd_var_pct} /></span>
                <span className="stat-hint">
                  <span className="stat-line"><strong>{fmtSignedMoney(Number(o.kpis.ref_ytd) - Number(o.kpis.prev_ytd))}</strong></span>
                  <span className="stat-line">vs {baseLabel}</span>
                </span>
              </div>
            )}
            {o.has_actual && o.selected_years.length === 1 && o.last_closed_period && !months.length && (
              <Stat
                label="Média mensal"
                value={fmtCompact(Number(o.kpis.actual_total) / o.last_closed_period)}
                hint={lines(fmtMoney(Number(o.kpis.actual_total) / o.last_closed_period), `${o.last_closed_period} mês(es) com realizado`)}
              />
            )}
            {Number(o.kpis.ref_annualized) > 0 && (
              <Stat
                label={`${o.reference_year} anualizado`}
                value={fmtCompact(o.kpis.ref_annualized)}
                hint={lines(
                  fmtMoney(o.kpis.ref_annualized),
                  o.has_prev && o.kpis.annualized_vs_prev_pct !== null
                    ? `${fmtPct(o.kpis.annualized_vs_prev_pct)} (${fmtSignedMoney(Number(o.kpis.ref_annualized) - Number(o.kpis.prev_total))}) vs ${o.previous_year} cheio`
                    : "projeção linear",
                )}
              />
            )}
            {o.has_budget && !isBudgetMain && (period?.base_kind !== "budget" || o.kpis.budget_total !== o.kpis.prev_ytd) && (
              <Stat
                label={budgetLabel}
                value={fmtCompact(o.kpis.budget_total)}
                hint={lines(
                  fmtMoney(o.kpis.budget_total),
                  o.kpis.budget_consumption_pct !== null && `realizado ${fmtPct(o.kpis.budget_consumption_pct)} do orçado no período`,
                  Number(o.kpis.budget_unscheduled) > 0 && `inclui ${fmtMoney(o.kpis.budget_unscheduled)} de CAPEX sem cronograma mensal`,
                )}
              />
            )}
          </div>
          {o.by_module.length > 0 && (
            <div className="stats stats-modules">
              {o.by_module.map((m) => (
                <Stat
                  key={m.module}
                  label={`${m.label} · ${mainLabel}`}
                  value={fmtCompact(m.main)}
                  hint={lines(
                    fmtMoney(m.main),
                    hasBase && m.var_pct !== null && Number(m.base) > 0 &&
                      `${fmtPct(m.var_pct)} (${fmtSignedMoney(Number(m.main) - Number(m.base))}) vs ${baseLabel}`,
                    Number(m.unscheduled) > 0 && `inclui ${fmtMoney(m.unscheduled)} sem cronograma mensal`,
                  )}
                />
              ))}
            </div>
          )}

          <div className="section-stack">
            {shell("monthly",
              <Card title="Comparativo mensal" actions={months.length ? unpick("Desmarcar meses", () => setMonths([])) : undefined}>
                <div className="monthly-split">
                  <PlotlyChart figure={f.monthly} height={360} onClick={onMonth} ariaLabel="Comparativo mensal" />
                  {f.monthly_total && (
                    <div className="monthly-total">
                      <PlotlyChart figure={f.monthly_total} height={360} ariaLabel="Total do período" />
                    </div>
                  )}
                </div>
              </Card>,
            )}

            {shell("table",
              <Card
                title={`Por área, setor, pacote GMD e conta · ${mainLabel}${typesTxt}`}
                actions={filters.package_id || account || filters.cost_center_id
                  ? unpick("Desmarcar", () => { setFilters((cur) => ({ ...cur, package_id: "", cost_center_id: "" })); setAccount(null); })
                  : undefined}
              >
                <DrillTable query={query} refLabel={mainLabel} onSelect={onTableRow} why />
              </Card>,
            )}

            {shell("cumulative",
              <Card title={`Total acumulado · ${mainLabel}`} actions={months.length ? unpick("Desmarcar meses", () => setMonths([])) : undefined}>
                <PlotlyChart figure={f.cumulative} height={340} onClick={onMonth} ariaLabel="Total acumulado" />
              </Card>,
            )}

            {shell("top",
              <Card
                title={`Maiores centros de custo · ${mainLabel}`}
                actions={filters.cost_center_id ? unpick("Desmarcar", () => setFilters((cur) => ({ ...cur, cost_center_id: "" }))) : undefined}
              >
                {o.top_cost_centers_total && (
                  <div className="stats rank-stats">
                    <Stat label={`Total · ${mainLabel}`} value={fmtCompact(o.top_cost_centers_total.main)} hint={lines(fmtMoney(o.top_cost_centers_total.main), "todos os centros de custo")} />
                    {o.top_cost_centers_total.base !== null && (
                      <>
                        <Stat label={`Total · ${baseLabel}`} value={fmtCompact(o.top_cost_centers_total.base)} hint={fmtMoney(o.top_cost_centers_total.base)} />
                        <div className="stat stat-inline-delta">
                          <span className="stat-label">Variação</span>
                          <span className="stat-value"><Delta pct={o.top_cost_centers_total.var_pct} /></span>
                          <span className="stat-hint">
                            <span className="stat-line"><strong>{fmtSignedMoney(Number(o.top_cost_centers_total.main) - Number(o.top_cost_centers_total.base))}</strong></span>
                            <span className="stat-line">vs {baseLabel}</span>
                          </span>
                        </div>
                      </>
                    )}
                  </div>
                )}
                <p className="muted small">Clique num centro de custo para filtrar; de novo para desmarcar.</p>
                {o.top_cost_centers.length ? (
                  <div className="monthly-split">
                    <PlotlyChart figure={f.top_cost_centers} onClick={onCostCenter} ariaLabel="Maiores centros de custo" />
                    {f.top_cost_centers_total && (
                      <div className="monthly-total">
                        <PlotlyChart figure={f.top_cost_centers_total} height={360} ariaLabel="Total de todos os centros de custo" />
                      </div>
                    )}
                  </div>
                ) : (
                  <Empty>Sem dados.</Empty>
                )}
              </Card>,
            )}

          </div>
        </div>
      )}
      {o?.budget_progress && o.budget_progress.total_cost_centers > 0 && <BudgetProgressCard progress={o.budget_progress} figures={f ? { rank: f.budget_progress, total: f.budget_progress_total } : undefined} />}
      <PainelBase />
    </>
  );
}
