import { useCallback, useEffect, useMemo, useState } from "react";
import { usePersistentState } from "../persist";
import { api, type Company, type Package } from "../api";
import { useAuth } from "../auth";
import { FilterBar } from "../components/FilterBar";
import { PlotlyChart, type Figure } from "../components/PlotlyChart";
import { Alert, Badge, Card, Empty, Loading, PageHeader, Stat, useLoad } from "../components/ui";
import { fmtInt } from "../labels";

interface Kpi { label: string; value: string | null; compact: string; pct?: string | null; pct_label?: string; share?: string; hint?: string }
interface Dashboard {
  years: { prev: number; ref: number; target: number };
  series?: { prev: string; ref: string; labels: Record<string, string> };
  version: { id: number; label: string; status: string };
  kpis: Record<string, Kpi>;
  status: { total: number; filled: number; pending: number; submitted: number; returned: number; approved: number };
  has: { prev: boolean; ref: boolean; target: boolean };
  dimension: string;
  dimensions: Record<string, string>;
  drill_order: string[];
  figures: Record<string, Figure>;
  cells: number;
}
interface Options {
  cost_centers: { id: number; code: string; name: string; company_id: number; department_id: number | null }[];
  departments: { id: number; name: string }[];
  accounts: { code: string; name: string; nature: string; package_id: number | null }[];
  versions: { id: number; label: string; status: string; current: boolean }[];
  series: { key: string; label: string }[];
  default_series: { prev: string; ref: string };
  years: { year: number; kind: "target" | "actual"; closed: number | null }[];
  target_year: number;
}
type Filters = { company_id: string; department_id: string; cost_center_id: string; account: string; module: string; package_id: string; version_id: string; year: string; months: string; compare: string; same_period: string };
// cabeçalho igual ao do Painel (08/10/2026): ano principal (vazio = ano do ciclo), meses, comparar com o ano anterior (desligado por padrão) e mesmo período
const EMPTY: Filters = { company_id: "", department_id: "", cost_center_id: "", account: "", module: "", package_id: "", version_id: "", year: "", months: "", compare: "false", same_period: "" };
const MONTHS = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"];
const MODULE_LABEL: Record<string, string> = { OPEX: "OPEX", CAPEX: "CAPEX", PERSONNEL: "Pessoal" };
// drill-down: Empresa → Diretoria → Centro de custo → Conta → Mês
const NEXT: Record<string, string> = { company: "department", department: "cost_center", cost_center: "account", account: "month" };
const FILTER_OF: Record<string, keyof Filters> = { company: "company_id", department: "department_id", cost_center: "cost_center_id", account: "account", module: "module" };

export default function Analytics() {
  const { can } = useAuth();
  const isController = can("CONTROLLER");
  const [filters, setFilters] = usePersistentState<Filters>("analise.filters", EMPTY);
  const [dimension, setDimension] = usePersistentState("analise.dimension", "cost_center");
  const [variationBy, setVariationBy] = usePersistentState<"account" | "cost_center">("analise.variationBy", "account");
  const [variationMode, setVariationMode] = usePersistentState<"abs" | "pct">("analise.variationMode", "abs");
  const [top, setTop] = usePersistentState("analise.top", 10);
  const [trail, setTrail] = usePersistentState<{ level: string; label: string; filters: Filters; dimension: string }[]>("analise.trail", []);
  const companies = useLoad(() => api<Company[]>("/companies"));
  const packages = useLoad(() => api<Package[]>("/packages"));
  const opts = useLoad(() => api<Options>("/analytics/options"));

  const qs = useMemo(() => {
    const p = new URLSearchParams();
    Object.entries(filters).forEach(([k, v]) => v && p.set(k, v));
    if (!filters.year && opts.data?.target_year) p.set("year", String(opts.data.target_year));
    p.set("dimension", dimension);
    p.set("variation_by", variationBy);
    p.set("variation_mode", variationMode);
    p.set("top", String(top));
    return p.toString();
  }, [filters, dimension, variationBy, variationMode, top, opts.data?.target_year]);
  const { data, error } = useLoad(() => api<Dashboard>(`/analytics/dashboard?${qs}`), [qs]);
  const [shown, setShown] = useState<Dashboard | null>(null);
  useEffect(() => { if (data) setShown(data); }, [data]);

  const set = (patch: Partial<Filters>) => setFilters((f) => ({ ...f, ...patch }));

  /** Clique numa barra do comparativo: filtra pela dimensão clicada e desce um nível. */
  const drill = useCallback((customdata: unknown, label: string) => {
    const id = Array.isArray(customdata) ? String(customdata[1]) : null;
    const key = FILTER_OF[dimension];
    if (!id || !key) return;
    setTrail((t) => [...t, { level: dimension, label, filters, dimension }]);
    set({ [key]: id } as Partial<Filters>);
    if (NEXT[dimension]) setDimension(NEXT[dimension]);
  }, [dimension, filters]);
  const drillCc = useCallback((customdata: unknown, label: string) => {
    const id = Array.isArray(customdata) ? String(customdata[2]) : null;
    if (!id) return;
    setTrail((t) => [...t, { level: "cost_center", label, filters, dimension }]);
    set({ cost_center_id: id });
    setDimension("account");
  }, [filters, dimension]);
  const back = (i: number) => {
    const step = trail[i];
    setFilters(step.filters);
    setDimension(step.dimension);
    setTrail(trail.slice(0, i));
  };
  const clear = () => { setFilters(EMPTY); setTrail([]); setDimension("cost_center"); };

  if (error && !shown) return <Alert>{error}</Alert>;
  if (!shown) return <Loading />;
  const d = shown;
  const y = d.years;
  const k = d.kpis;
  const active = Object.entries(filters).filter(([key, v]) => v && !(["version_id", "compare", "same_period"].includes(key))).length + (filters.compare === "true" ? 1 : 0) + (filters.same_period === "false" ? 1 : 0);

  return (
    <>
      <PageHeader
        title="Análise orçamentária"
        subtitle={`Realizado ${y.prev} × Orçado ${y.ref} × Orçamento ${y.target} (versão ${d.version.label}). Os números são os mesmos do processo: OPEX, CAPEX e Pessoal consolidados por centro de custo, conta e mês.`}
      />
      <FilterBar
        onApply={(v) => { set({ ...v } as Partial<Filters>); setTrail([]); }}  // botões (empresa, área, tipo) aplicam na hora
        onReset={clear}
        resetCount={active}
        lead={
          <>
            <div className="chip-group">
              <span className="chip-label">Empresa</span>
              <div className="month-chips" role="group" aria-label="Empresas">
                <button type="button" className={!filters.company_id ? "active" : ""} aria-pressed={!filters.company_id} onClick={() => { set({ company_id: "", cost_center_id: "" }); setTrail([]); }}>Todas</button>
                {(companies.data ?? []).map((c) => {
                  const on = filters.company_id === String(c.id);
                  return (
                    <button key={c.id} type="button" className={on ? "active" : ""} aria-pressed={on} title={`${c.code} · ${c.name}`} onClick={() => { set({ company_id: on ? "" : String(c.id), cost_center_id: "" }); setTrail([]); }}>
                      {c.short_name ?? c.name}
                    </button>
                  );
                })}
              </div>
            </div>
            {(opts.data?.departments.length ?? 0) > 0 && (
              <div className="chip-group">
                <span className="chip-label">Área</span>
                <div className="month-chips" role="group" aria-label="Áreas">
                  <button type="button" className={!filters.department_id ? "active" : ""} aria-pressed={!filters.department_id} onClick={() => { set({ department_id: "", cost_center_id: "" }); setTrail([]); }}>Todas</button>
                  {[...(opts.data?.departments ?? [])].sort((a, b) => a.name.localeCompare(b.name)).map((dp) => {
                    const on = filters.department_id === String(dp.id);
                    return (
                      <button key={dp.id} type="button" className={on ? "active" : ""} aria-pressed={on} onClick={() => { set({ department_id: on ? "" : String(dp.id), cost_center_id: "" }); setTrail([]); }}>
                        {dp.name}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
          </>
        }
        extra={
          <div className="chip-group">
            <span className="chip-label">Tipo</span>
            <div className="month-chips" role="group" aria-label="Tipos de orçamento">
              <button type="button" className={!filters.module ? "active" : ""} aria-pressed={!filters.module} onClick={() => set({ module: "", package_id: "" })}>Todos</button>
              {Object.entries(MODULE_LABEL).map(([v, l]) => (
                <button key={v} type="button" className={filters.module === v ? "active" : ""} aria-pressed={filters.module === v} onClick={() => set({ module: filters.module === v ? "" : v, package_id: "" })}>{l}</button>
              ))}
            </div>
          </div>
        }
        fields={[
          {
            key: "cost_center_id", label: "Centro de custo", value: filters.cost_center_id, wide: true,
            onChange: (v) => { set({ cost_center_id: v }); setTrail([]); },
            options: () => [
              { value: "", label: isController ? "Todos os centros de custo" : "Meus centros de custo" },
              ...(opts.data?.cost_centers ?? [])
                .filter((c) => (!filters.company_id || String(c.company_id) === filters.company_id) && (!filters.department_id || String(c.department_id) === filters.department_id))
                .map((c) => ({ value: String(c.id), label: `${c.code} · ${c.name}` })),
            ],
          },
          {
            key: "account", label: "Conta contábil", value: filters.account, wide: true,
            onChange: (v) => { set({ account: v }); setTrail([]); },
            options: (d) => [
              { value: "", label: "Todas as contas" },
              ...(opts.data?.accounts ?? []).filter((a) => !d.module || MODULE_OF[a.nature] === d.module).map((a) => ({ value: a.code, label: `${a.code} · ${a.name}` })),
            ],
          },
          ...((opts.data?.versions.length ?? 0) > 1
            ? [{
                key: "version_id", label: "Versão", value: filters.version_id,
                onChange: (v: string) => set({ version_id: v }),
                options: opts.data!.versions.map((v) => ({ value: v.current ? "" : String(v.id), label: `Versão ${v.label}${v.status === "FROZEN" ? " · congelada" : ""}${v.current ? " (atual)" : ""}` })),
              }]
            : []),
        ]}
      />
      {opts.data && (
        <>
          <div className="chip-groups">
            <div className="chip-group">
              <span className="chip-label">Ano</span>
              <div className="year-tabs" role="group" aria-label="Ano principal">
                {opts.data.years.map((yy) => {
                  const cur = Number(filters.year || opts.data!.target_year) === yy.year;
                  return <button key={yy.year} type="button" className={cur ? "active" : ""} aria-pressed={cur} onClick={() => { set({ year: yy.year === opts.data!.target_year ? "" : String(yy.year) }); setTrail([]); }}>{yy.year}</button>;
                })}
              </div>
            </div>
            <div className="chip-group">
              <span className="chip-label">Mês</span>
              <div className="month-chips" role="group" aria-label="Meses">
                <button type="button" className={!filters.months ? "active" : ""} aria-pressed={!filters.months} onClick={() => set({ months: "" })}>Todos</button>
                {MONTHS.map((m, i) => {
                  const sel = filters.months ? filters.months.split(",").map(Number) : [];
                  const on = sel.includes(i + 1);
                  return <button key={m} type="button" className={on ? "active" : ""} aria-pressed={on} onClick={() => { const next = on ? sel.filter((x) => x !== i + 1) : [...sel, i + 1].sort((a, b) => a - b); set({ months: next.length === 12 ? "" : next.join(",") }); }}>{m}</button>;
                })}
              </div>
            </div>
            <div className="chip-group">
              <span className="chip-label">Pacote</span>
              <select value={filters.package_id} aria-label="Pacote GMD" onChange={(e) => set({ package_id: e.target.value })}>
                <option value="">Todos os pacotes</option>
                {(filters.module && filters.module !== "OPEX" ? [] : (packages.data ?? []).filter((p) => p.nature !== "CAPEX")).map((p) => (
                  <option key={p.id} value={String(p.id)}>{`${p.roman ? `${p.roman} · ` : ""}${p.name}`}</option>
                ))}
              </select>
            </div>
          </div>
          <div className="section-tools">
            <div className="toggle-options" role="group" aria-label="Opções de comparação">
              <label>
                <input type="checkbox" checked={filters.compare === "true"} onChange={(e) => set({ compare: e.target.checked ? "true" : "false" })} />
                Comparar com o ano anterior
              </label>
              <label title="Limita a base de comparação aos meses já fechados do realizado">
                <input type="checkbox" checked={filters.same_period !== "false"} onChange={(e) => set({ same_period: e.target.checked ? "" : "false" })} />
                Mesmo período
              </label>
            </div>
            <span className="selection-note">
              Selecionado: <strong>{d.series?.labels?.target ?? k.target.label}</strong>
              {d.series?.labels?.ref && <> · referência <strong>{d.series.labels.ref}</strong></>}
              {d.series?.labels?.prev && <> · comparado com <strong>{d.series.labels.prev}</strong></>}
            </span>
          </div>
        </>
      )}
      {trail.length > 0 && (
        <nav className="breadcrumb" aria-label="Drill-down">
          <button className="btn-link link" onClick={clear}>Visão geral</button>
          {trail.map((t, i) => (
            <span key={i}> › <button className="btn-link link" onClick={() => back(i + 1 < trail.length ? i + 1 : trail.length)} disabled={i === trail.length - 1}>{d.dimensions[t.level]}: {t.label}</button></span>
          ))}
          <span className="muted small"> · nível atual: {d.dimensions[dimension]}</span>
        </nav>
      )}
      {!d.has.target && !d.has.prev && !d.has.ref && <Alert tone="warn">Nenhum valor para os filtros escolhidos.</Alert>}
      {error && <Alert>{error}</Alert>}

      <div className="stats">
        <Stat label={k.prev_actual.label} value={k.prev_actual.compact} hint={!d.has.prev ? "sem realizado carregado" : undefined} />
        <Stat label={k.ref_budget.label} value={k.ref_budget.compact} hint={!d.has.ref ? "sem orçado de referência carregado" : undefined} />
        <Stat label={k.target.label} value={k.target.compact} tone="warn" />
        <Stat label={k.var_ref.label} value={k.var_ref.pct_label} tone={k.var_ref.pct ? (Number(k.var_ref.pct) > 0 ? "bad" : "good") : undefined} hint={k.var_ref.pct ? k.var_ref.compact : `sem orçado ${y.ref}`} />
        <Stat label={k.var_prev.label} value={k.var_prev.pct_label} tone={k.var_prev.pct ? (Number(k.var_prev.pct) > 0 ? "bad" : "good") : undefined} hint={k.var_prev.pct ? k.var_prev.compact : `sem realizado ${y.prev}`} />
      </div>
      <div className="stats">
        {(["opex", "capex", "personnel"] as const).map((m) => <Stat key={m} label={`${k[m].label} ${y.target}`} value={k[m].compact} hint={k[m].share !== "—" ? `${k[m].share} do orçamento` : undefined} />)}
        <Stat label={k.filled_pct.label} value={k.filled_pct.compact} hint={k.filled_pct.hint} tone={k.filled_pct.value && Number(k.filled_pct.value) >= 1 ? "good" : undefined} />
        <Stat label={k.approved_pct.label} value={k.approved_pct.compact} hint={k.approved_pct.hint} tone={k.approved_pct.value && Number(k.approved_pct.value) >= 1 ? "good" : undefined} />
      </div>

      <Card title={`Evolução mensal · Realizado ${y.prev} × Orçado ${y.ref} × Orçamento ${y.target}`}>
        {d.figures.monthly.data.length ? <PlotlyChart figure={d.figures.monthly} height={340} ariaLabel="Evolução mensal" /> : <Empty>Sem valores mensais.</Empty>}
      </Card>

      <Card
        title="Comparativo anual"
        actions={
          <div className="inline-controls">
            <span className="muted small">Agrupar por</span>
            <select value={dimension} onChange={(e) => setDimension(e.target.value)} aria-label="Dimensão">
              {Object.entries(d.dimensions).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </div>
        }
      >
        {d.figures.annual.data.length ? (
          <>
            <PlotlyChart figure={d.figures.annual} height={360} onClick={FILTER_OF[dimension] ? drill : undefined} ariaLabel="Comparativo anual" />
            {FILTER_OF[dimension] && dimension !== "module" && <p className="muted small">Clique numa barra para detalhar ({d.dimensions[dimension]} → {d.dimensions[NEXT[dimension]]}).</p>}
          </>
        ) : <Empty>Sem valores.</Empty>}
      </Card>

      <div className="grid-2">
        <Card
          title={`Variação orçamentária · Orçamento ${y.target} × ${d.figures.variation.meta?.base_label ?? `Orçado ${y.ref}`}`}
          actions={
            <div className="inline-controls">
              <select value={variationBy} onChange={(e) => setVariationBy(e.target.value as "account" | "cost_center")} aria-label="Variação por">
                <option value="account">Por conta</option>
                <option value="cost_center">Por centro de custo</option>
              </select>
              <div className="year-tabs" role="tablist" aria-label="Modo">
                <button className={variationMode === "abs" ? "active" : ""} onClick={() => setVariationMode("abs")}>R$</button>
                <button className={variationMode === "pct" ? "active" : ""} onClick={() => setVariationMode("pct")}>%</button>
              </div>
            </div>
          }
        >
          {d.figures.variation.data[0] && (d.figures.variation.data[0] as { x?: unknown[] }).x?.length ? (
            <PlotlyChart figure={d.figures.variation} ariaLabel="Variação orçamentária" />
          ) : <Empty>Sem variações relevantes (abaixo do valor mínimo de alerta do ciclo).</Empty>}
        </Card>
        <Card
          title={`Maiores despesas · contas por orçamento ${y.target}`}
          actions={
            <div className="year-tabs" role="tablist" aria-label="Quantidade">
              {[10, 20, 0].map((n) => <button key={n} className={top === n ? "active" : ""} onClick={() => setTop(n)}>{n ? `Top ${n}` : "Todas"}</button>)}
            </div>
          }
        >
          {(d.figures.ranking.data[0] as { x?: unknown[] } | undefined)?.x?.length ? <PlotlyChart figure={d.figures.ranking} ariaLabel="Ranking de contas" /> : <Empty>Sem orçamento lançado.</Empty>}
        </Card>
      </div>

      <div className="grid-2">
        <Card title={`Composição do orçamento ${y.target}`} actions={<span className="muted small">Total <strong>{d.figures.composition.meta?.total}</strong></span>}>
          {d.has.target ? <PlotlyChart figure={d.figures.composition} ariaLabel="Composição por módulo" /> : <Empty>Sem orçamento lançado.</Empty>}
        </Card>
        <Card title="Andamento do preenchimento" actions={<span className="muted small">{fmtInt(d.status.total)} centro(s) de custo no escopo</span>}>
          <div className="stats stats-compact">
            <Stat label="Preenchidos" value={fmtInt(d.status.filled)} tone="good" />
            <Stat label="Pendentes" value={fmtInt(d.status.pending)} tone={d.status.pending ? "warn" : undefined} />
            <Stat label="Em validação" value={fmtInt(d.status.submitted)} />
            <Stat label="Devolvidos" value={fmtInt(d.status.returned)} tone={d.status.returned ? "bad" : undefined} />
            <Stat label="Aprovados" value={fmtInt(d.status.approved)} tone="good" />
          </div>
          {d.figures.status.data.length ? <PlotlyChart figure={d.figures.status} ariaLabel="Situação por módulo" /> : <Empty>Sem centros de custo no escopo.</Empty>}
          <p className="muted small">Preenchidos = CCs com algum valor em {y.target}. Em validação, devolvidos e aprovados referem-se ao fluxo do orçamento OPEX; o gráfico mostra os três módulos.</p>
        </Card>
      </div>

      <Card title={`Centros de custo · ${d.figures.cost_centers.meta?.base_label ?? `Orçado ${y.ref}`} × Orçamento ${y.target}`} actions={<span className="muted small">Estável = variação dentro de ±5%</span>}>
        {(d.figures.cost_centers.data[1] as { x?: unknown[] } | undefined)?.x?.length ? (
          <>
            <PlotlyChart figure={d.figures.cost_centers} onClick={filters.cost_center_id ? undefined : drillCc} ariaLabel="Centros de custo" />
            {!filters.cost_center_id && <p className="muted small">Clique num centro de custo para ver suas contas.</p>}
          </>
        ) : <Empty>Sem centros de custo com valores.</Empty>}
      </Card>
      <p className="muted small">
        Versão {d.version.label}{d.version.status === "FROZEN" ? " (congelada: fotografia do congelamento)" : ""} · {fmtInt(d.cells)} combinações CC × conta × mês no escopo.{" "}
        <span className="desktop-only"><Badge tone="neutral">Plotly</Badge> arraste para ampliar, clique na legenda para ocultar séries, duplo clique para voltar.</span>
        <span className="mobile-only">Toque numa barra para detalhar; toque na legenda para ocultar séries.</span>
      </p>
    </>
  );
}

const MODULE_OF: Record<string, string> = { OPEX: "OPEX", FINANCEIRO: "OPEX", CAPEX: "CAPEX", PESSOAL: "PERSONNEL" };
