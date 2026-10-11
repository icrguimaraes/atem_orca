import { Fragment, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, download, type Company, type CostCenter, type Department, type Package, type TraceEntries, type TraceLevel, type TraceRecord, type TraceRow, type TraceTree } from "../api";
import { useAuth } from "../auth";
import { ActiveFilters, FilterBar, type ActiveChip } from "../components/FilterBar";
import { Alert, Card, Empty, Loading, PageHeader, useLoad } from "../components/ui";
import { MONTHS, byDepartment, deptRank, fmtDateTime, fmtMoney, fmtShare } from "../labels";
import { usePersistentState } from "../persist";

/**
 * Rastro (rota /rastro, 09/10/2026): do total do Painel ao lançamento que o originou. Mesmos filtros do Painel
 * (empresa, área, CC, tipo, ano, mês, pacote); a trilha área → setor → centro de custo → pacote → conta fica na URL
 * (`parent_*`, os mesmos nomes do drill-down do Painel — o "Ver rastro" da tabela chega já posicionado) e, na conta,
 * a lista dos lançamentos: linha do OPEX, item do CAPEX, posição do quadro, contrato PJ ou partida importada com o
 * arquivo de origem. "Ver lançamentos" mostra os registros de qualquer nível.
 */
const LEVELS: TraceLevel[] = ["department", "area", "cost_center", "package", "account"];
const LEVEL_LABELS: Record<TraceLevel, string> = { department: "Área", area: "Setor", cost_center: "Centro de custo", package: "Pacote", account: "Conta" };
const PLURAL: Record<string, string> = { Área: "Áreas", Setor: "Setores", "Centro de custo": "Centros de custo", Pacote: "Pacotes", Conta: "Contas", Lançamento: "Lançamentos" };
// parâmetro da trilha de cada nível: [com id, linha "Sem …"]
const PARENT: Record<TraceLevel, [string, string]> = {
  department: ["parent_department_id", "parent_no_department"],
  area: ["parent_area_id", "parent_no_area"],
  cost_center: ["parent_cost_center_id", ""],
  package: ["parent_package_id", "parent_no_package"],
  account: ["parent_account_id", ""],
};
const TRAIL_KEYS = LEVELS.flatMap((l) => PARENT[l].filter(Boolean));
const FILTER_KEYS = ["company_id", "cost_center_id", "package_id", "account_id", "department_id", "years", "months", "modules", "series"];
const BUDGET_TYPES = [
  { key: "OPEX", label: "OPEX" },
  { key: "CAPEX", label: "CAPEX" },
  { key: "PERSONNEL", label: "Pessoal" },
];
const PAGE = 50;
const SOURCE_LABELS: Record<string, string> = {
  version_id: "Versão (id)", version: "Versão nº", scope: "Escopo da versão", batch_id: "Lote de importação", layout: "Layout do arquivo",
  line_id: "Linha nº", submission_id: "Submissão nº", project_id: "Projeto nº", item_id: "Item nº", contract_id: "Contrato nº",
};

const nums = (csv: string) => csv.split(",").filter(Boolean).map(Number).filter((n) => Number.isFinite(n));

export default function Trace() {
  const { user, can } = useAuth();
  const isController = can("CONTROLLER");
  const [params, setParams] = useSearchParams();
  // a URL (link "Ver rastro" do Painel) vale sobre os filtros guardados
  const fromUrl = FILTER_KEYS.some((k) => params.has(k));
  const [filters, setFilters] = usePersistentState(
    "rastro.filters",
    { company_id: params.get("company_id") ?? "", cost_center_id: params.get("cost_center_id") ?? "", package_id: params.get("package_id") ?? "" },
    fromUrl,
  );
  const [department, setDepartment] = usePersistentState("rastro.department", params.get("department_id") ?? "", fromUrl);
  const [years, setYears] = usePersistentState<number[]>("rastro.years", nums(params.get("years") ?? ""), fromUrl);
  const [months, setMonths] = usePersistentState<number[]>("rastro.months", nums(params.get("months") ?? ""), fromUrl);
  const [modules, setModules] = usePersistentState<string[]>("rastro.modules", (params.get("modules") ?? "").split(",").filter(Boolean), fromUrl);
  const [series, setSeries] = usePersistentState("rastro.series", params.get("series") ?? "", fromUrl);
  const [accountId] = useState(params.get("account_id") ?? ""); // filtro por conta só chega pela URL (clique no Painel)
  const [showEntries, setShowEntries] = useState(params.get("lancamentos") === "1");
  const [search, setSearch] = useState("");
  const [needle, setNeedle] = useState("");
  const [offset, setOffset] = useState(0);
  useEffect(() => {
    const t = window.setTimeout(() => setNeedle(search.trim()), 300);
    return () => window.clearTimeout(t);
  }, [search]);

  // trilha: só na URL (voltar do navegador recua um nível)
  const trailQs = TRAIL_KEYS.filter((k) => params.has(k)).map((k) => `${k}=${encodeURIComponent(params.get(k) ?? "")}`).join("&");
  const chosen = LEVELS.filter((l) => PARENT[l].some((k) => k && params.has(k))); // níveis já fixados na URL
  const level = LEVELS.find((l) => !chosen.includes(l)) ?? null;
  const leaf = level === null;

  const query = new URLSearchParams(
    Object.entries({
      ...filters,
      account_id: accountId,
      department_id: department,
      years: years.join(","),
      months: months.join(","),
      modules: modules.join(","),
      series,
    }).filter(([, v]) => v),
  ).toString();
  const qs = [trailQs, query].filter(Boolean).join("&");

  const base = useLoad(async () => {
    const [companies, ccs, packages, departments] = await Promise.all([
      api<Company[]>("/companies"),
      api<CostCenter[]>("/cost-centers"),
      api<Package[]>("/packages"),
      api<Department[]>("/departments"),
    ]);
    return { companies, ccs, packages, departments };
  });
  const tree = useLoad(
    () => (level ? api<TraceTree>(`/trace/tree?level=${level}${qs ? `&${qs}` : ""}`) : Promise.resolve(null)),
    [level, qs],
  );
  const wantEntries = leaf || showEntries;
  useEffect(() => setOffset(0), [qs, needle]);
  const entries = useLoad(
    () =>
      wantEntries
        ? api<TraceEntries>(`/trace/entries?offset=${offset}&limit=${PAGE}${needle ? `&search=${encodeURIComponent(needle)}` : ""}${qs ? `&${qs}` : ""}`)
        : Promise.resolve(null),
    [wantEntries, qs, needle, offset],
  );

  function setTrail(next: Record<string, string | null>) {
    const p = new URLSearchParams(params);
    for (const [k, v] of Object.entries(next)) {
      if (v === null) p.delete(k);
      else p.set(k, v);
    }
    setParams(p);
  }
  function open(row: TraceRow) {
    if (!level) return;
    const [byId, none] = PARENT[level];
    if (row.id === null) {
      if (!none) return;
      setTrail({ [none]: "true" });
    } else setTrail({ [byId]: String(row.id) });
  }
  function backTo(idx: number) {
    // mantém os níveis até idx (exclusive), apaga os de baixo
    const next: Record<string, string | null> = {};
    chosen.slice(idx).forEach((l) => PARENT[l].forEach((k) => k && (next[k] = null)));
    setTrail(next);
  }
  function clearAll() {
    setFilters({ company_id: "", cost_center_id: "", package_id: "" });
    setDepartment("");
    setYears([]);
    setMonths([]);
    setModules([]);
    setSeries("");
    backTo(0);
  }
  function toggleYear(y: number, current: number[]) {
    const b = years.length ? years : current;
    const next = b.includes(y) ? b.filter((x) => x !== y) : [...b, y];
    if (next.length) setYears(next.sort((a, c) => a - c));
  }
  function toggleMonth(m: number) {
    setMonths((cur) => {
      const next = cur.includes(m) ? cur.filter((x) => x !== m) : [...cur, m].sort((a, b) => a - b);
      return next.length === 12 ? [] : next;
    });
  }
  function toggleModule(m: string) {
    setModules((cur) => {
      const next = cur.includes(m) ? cur.filter((x) => x !== m) : [...cur, m];
      return next.length === BUDGET_TYPES.length ? [] : BUDGET_TYPES.map((b) => b.key).filter((k) => next.includes(k));
    });
  }

  if (!base.data) return <Loading />;
  const { companies, ccs, packages, departments } = base.data;
  const info = tree.data ?? entries.data ?? null;
  const period = info?.period;
  const crumbs = info?.trail ?? [];
  const activeFilters =
    [filters.company_id, filters.cost_center_id, filters.package_id, department, accountId].filter(Boolean).length +
    (years.length ? 1 : 0) + (months.length ? 1 : 0) + (modules.length ? 1 : 0) + (series ? 1 : 0);
  const shownYears = years.length ? years : (period?.years ?? []);
  const bothSeries = Boolean(period?.actual_label && period?.budget_label);
  const t = tree.data && tree.data.level === level ? tree.data : null; // nunca mostrar o nível anterior enquanto carrega
  const rows = t ? [...t.rows].sort((a, b) => (t.level === "department" ? deptRank(a.name) - deptRank(b.name) : 0) || Number(b.value) - Number(a.value)) : [];

  // filtros ativos como chips removíveis (mesmo padrão do Painel); a conta vinda do Painel tem chip próprio abaixo
  const names = <T extends { id: number }>(list: T[], ids: string, get: (x: T) => string) =>
    ids.split(",").map((i) => { const x = list.find((o) => String(o.id) === i); return x ? get(x) : i; }).join(" + ");
  const chips: ActiveChip[] = [
    ...(filters.company_id ? [{ label: `Empresa: ${names(companies, filters.company_id, (c) => c.short_name ?? c.name)}`, onRemove: () => setFilters({ ...filters, company_id: "", cost_center_id: "" }) }] : []),
    ...(department ? [{ label: `Área: ${names(departments, department, (d) => d.name)}`, onRemove: () => setDepartment("") }] : []),
    ...(filters.cost_center_id ? [{ label: `Centro de custo: ${names(ccs, filters.cost_center_id, (c) => c.name)}`, onRemove: () => setFilters({ ...filters, cost_center_id: "" }) }] : []),
    ...(modules.length ? [{ label: `Tipo: ${modules.map((m) => BUDGET_TYPES.find((b) => b.key === m)?.label ?? m).join(" + ")}`, onRemove: () => setModules([]) }] : []),
    ...(years.length ? [{ label: `Ano: ${years.join(" + ")}`, onRemove: () => setYears([]) }] : []),
    ...(months.length ? [{ label: `Mês: ${months.map((m) => MONTHS[m - 1]).join(", ")}`, onRemove: () => setMonths([]) }] : []),
    ...(filters.package_id ? [{ label: `Pacote: ${names(packages, filters.package_id, (p) => p.name)}`, onRemove: () => setFilters({ ...filters, package_id: "" }) }] : []),
    ...(series ? [{ label: `Série: ${series === "budget" ? period?.budget_label ?? "orçado" : period?.actual_label ?? "realizado"}`, onRemove: () => setSeries("") }] : []),
    ...(crumbs.length ? [{ label: `Trilha: ${crumbs[crumbs.length - 1].name}`, onRemove: () => backTo(0) }] : []),
  ];

  return (
    <>
      <PageHeader
        title="Rastro"
        subtitle="Do total do Painel ao lançamento: área → setor → centro de custo → pacote → conta → registro de origem · clique na linha para descer e na trilha para voltar."
      />
      <FilterBar
        onApply={(v) => setFilters({ ...filters, cost_center_id: v.cost_center_id })}
        onReset={clearAll}
        resetCount={activeFilters + crumbs.length}
        showActive={false}
        desktopReset={false}
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
                  {[...departments].sort((a, b) => byDepartment(a.name, b.name)).map((d) => {
                    const on = department.split(",").includes(String(d.id));
                    return (
                      <button
                        key={d.id}
                        type="button"
                        className={on ? "active" : ""}
                        aria-pressed={on}
                        onClick={() => setDepartment((cur) => { const picked = cur ? cur.split(",") : []; const next = on ? picked.filter((x) => x !== String(d.id)) : [...picked, String(d.id)]; return next.length === departments.length ? "" : next.join(","); })}
                      >
                        {d.name}
                      </button>
                    );
                  })}
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

      {info && (
        <div className="chip-groups" aria-busy={tree.loading || entries.loading}>
          <div className="chip-group">
            <span className="chip-label">Ano</span>
            <div className="year-tabs" role="group" aria-label="Anos exibidos (somados)">
              {[...info.available_years].sort((a, b) => a - b).map((y) => {
                const on = shownYears.includes(y);
                return (
                  <button key={y} type="button" className={on ? "active" : ""} aria-pressed={on} onClick={() => toggleYear(y, period?.years ?? [])}>
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
              {packages.map((p) => (
                <option key={p.id} value={String(p.id)}>{`${p.roman ? `${p.roman} · ` : ""}${p.name}`}</option>
              ))}
            </select>
          </div>
          {bothSeries && (
            <div className="chip-group">
              <span className="chip-label">Série</span>
              <div className="month-chips" role="group" aria-label="Série rastreada">
                <button type="button" className={info.series === "actual" ? "active" : ""} aria-pressed={info.series === "actual"} onClick={() => setSeries("actual")}>
                  {period?.actual_label}
                </button>
                <button type="button" className={info.series === "budget" ? "active" : ""} aria-pressed={info.series === "budget"} onClick={() => setSeries("budget")}>
                  {period?.budget_label}
                </button>
              </div>
            </div>
          )}
        </div>
      )}
      <ActiveFilters chips={chips} onReset={clearAll} resetCount={activeFilters + crumbs.length} />
      {accountId && (
        <div className="active-filters" aria-label="Filtro por conta (veio do Painel)">
          <span className="filter-chip">Conta filtrada no Painel · <Link to={`/rastro?${qs.replace(/(^|&)account_id=[^&]*/, "")}`}>remover</Link></span>
        </div>
      )}

      <nav className="breadcrumb" aria-label="Trilha do rastro">
        <button type="button" className="btn-link" onClick={() => backTo(0)} disabled={crumbs.length === 0}>
          Tudo
        </button>
        {crumbs.map((c, i) => (
          <span key={c.level}>
            <span className="muted"> › </span>
            <button type="button" className="btn-link" onClick={() => backTo(i + 1)} disabled={i === crumbs.length - 1 && leaf} title={`Voltar para ${LEVEL_LABELS[c.level]}`}>
              {c.label}: {c.code ? `${c.code} · ` : ""}{c.name}
            </button>
          </span>
        ))}
        {level && (
          <span className="muted small"> › {LEVEL_LABELS[level]}</span>
        )}
      </nav>

      {tree.error && <Alert>{tree.error}</Alert>}
      {level && (
        <Card title={`${LEVEL_LABELS[level]} · ${t?.series_label ?? ""}`} actions={
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setShowEntries((v) => !v)}>
            {showEntries ? "Ocultar lançamentos" : "Ver lançamentos deste nível"}
          </button>
        }>
          {!t ? <Loading /> : !rows.length ? <Empty>Sem valores neste recorte.</Empty> : (
            <>
              <p className="muted small">
                Total do nível: <strong>{fmtMoney(t.total)}</strong> · {t.count} {(t.count === 1 ? LEVEL_LABELS[level] : PLURAL[LEVEL_LABELS[level]]).toLowerCase()} · clique na linha para descer até {t.next_label.toLowerCase()}.
              </p>
              <div className="table-wrap">
                <table className="table drill-table">
                  <thead>
                    <tr>
                      <th>{LEVEL_LABELS[level]}</th>
                      <th className="right">{t.series_label}</th>
                      <th className="right">AV %</th>
                      <th className="right">{PLURAL[t.next_label] ?? t.next_label}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => {
                      const clickable = r.id !== null || Boolean(PARENT[level][1]);
                      return (
                        <tr key={r.id ?? "none"}>
                          <td>
                            {clickable ? (
                              <button type="button" className="row-filter" title={`Detalhar ${r.name}`} onClick={() => open(r)}>
                                {r.name}
                              </button>
                            ) : r.name}
                            {r.code && <span className="muted small mono"> {r.code}</span>}
                          </td>
                          <td className="right nowrap">{fmtMoney(r.value)}</td>
                          <td className="right nowrap muted">{fmtShare(r.share)}</td>
                          <td className="right nowrap muted">{r.children}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                  <tfoot>
                    <tr>
                      <td>Total do nível</td>
                      <td className="right nowrap">{fmtMoney(t.total)}</td>
                      <td className="right nowrap muted">100%</td>
                      <td />
                    </tr>
                  </tfoot>
                </table>
              </div>
            </>
          )}
        </Card>
      )}

      {wantEntries && (
        <Card title={leaf ? "Lançamentos da conta" : "Lançamentos do recorte"} actions={
          <button type="button" className="btn btn-ghost btn-sm" disabled={!entries.data?.count} onClick={() => download(`/trace/entries.xlsx?${[needle ? `search=${encodeURIComponent(needle)}` : "", qs].filter(Boolean).join("&")}`, "rastro.xlsx")}>
            Baixar Excel
          </button>
        }>
          <div className="section-tools">
            <span className="muted small">
              {entries.data && (
                <>
                  Total do nível: <strong>{fmtMoney(entries.data.level_total)}</strong> · {entries.data.count} registro{entries.data.count === 1 ? "" : "s"} somando {fmtMoney(entries.data.total)}
                  {Number(entries.data.difference) !== 0 && !needle && (
                    <> · diferença de {fmtMoney(entries.data.difference)} (arredondamento do rateio de pessoal ou lançamento fora do escopo)</>
                  )}
                  {needle && <> (busca ativa: a soma é só dos registros encontrados)</>}
                </>
              )}
            </span>
            <input
              type="search"
              className="drill-search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Buscar descrição, fornecedor, documento, conta ou CC…"
              aria-label="Buscar lançamentos"
            />
          </div>
          {entries.error && <Alert>{entries.error}</Alert>}
          {!entries.data ? <Loading /> : !entries.data.items.length ? <Empty>Nenhum lançamento neste recorte.</Empty> : (
            <>
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Tipo</th>
                      <th>Centro de custo</th>
                      <th>Conta</th>
                      <th>Lançamento</th>
                      <th>Mês</th>
                      <th className="right">Valor</th>
                      <th>Origem</th>
                      <th>Abrir</th>
                    </tr>
                  </thead>
                  <tbody>
                    {entries.data.items.map((r) => <RecordRow key={r.id} r={r} isController={isController} pj={(user?.pj_access ?? "NONE") !== "NONE"} />)}
                  </tbody>
                </table>
              </div>
              {entries.data.count > PAGE && (
                <div className="pager">
                  <span className="muted small">{offset + 1}–{Math.min(offset + PAGE, entries.data.count)} de {entries.data.count}</span>
                  <button type="button" className="btn btn-ghost btn-sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Anteriores</button>
                  <button type="button" className="btn btn-ghost btn-sm" disabled={offset + PAGE >= entries.data.count} onClick={() => setOffset(offset + PAGE)}>Próximos</button>
                </div>
              )}
            </>
          )}
        </Card>
      )}
    </>
  );
}

function monthsOf(r: TraceRecord): string {
  if (r.month) return MONTHS[r.month - 1];
  const on = r.values.map((v, i) => (Number(v) ? i : -1)).filter((i) => i >= 0);
  if (!on.length) return Number(r.unscheduled) ? "sem mês" : "—";
  const contiguous = on.every((m, i) => i === 0 || m === on[i - 1] + 1);
  const label = on.length === 12 ? "Jan–Dez" : contiguous && on.length > 1 ? `${MONTHS[on[0]]}–${MONTHS[on[on.length - 1]]}` : on.map((i) => MONTHS[i]).join(", ");
  return Number(r.unscheduled) ? `${label} + sem mês` : label;
}

function RecordRow({ r, isController, pj }: { r: TraceRecord; isController: boolean; pj: boolean }) {
  const [open, setOpen] = useState(false);
  const src = r.source ?? {};
  const origin = src.file_name
    ? `${src.file_name}${src.loaded_at ? ` · ${fmtDateTime(String(src.loaded_at))}` : ""}`
    : String(src.label ?? "—");
  const canOpen = r.link && (!r.link.startsWith("/importacoes") || isController) && (r.link !== "/pj" || pj);
  return (
    <>
      <tr>
        <td className="nowrap">{r.kind_label}</td>
        <td>{r.cost_center ? <><span className="mono">{r.cost_center.code}</span> {r.cost_center.name}</> : "—"}</td>
        <td>{r.account ? <><span className="mono">{r.account.code}</span> {r.account.name}</> : "—"}</td>
        <td>
          <button type="button" className="row-filter" onClick={() => setOpen((v) => !v)} aria-expanded={open} title="Ver todos os campos do lançamento">
            {r.title}
          </button>
          {r.detail && <div className="muted small">{r.detail}</div>}
        </td>
        <td className="nowrap">{monthsOf(r)}</td>
        <td className="right nowrap">{fmtMoney(r.total)}</td>
        <td className="small">{origin}{r.package ? <div className="muted">{r.package}</div> : null}</td>
        <td className="nowrap">{canOpen ? <Link to={r.link as string}>Abrir</Link> : <span className="muted">—</span>}</td>
      </tr>
      {open && (
        <tr>
          <td colSpan={8} className="small">
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    {MONTHS.map((m) => <th key={m} className="right">{m}</th>)}
                    {Number(r.unscheduled) !== 0 && <th className="right">Sem mês</th>}
                    <th className="right">Total</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    {r.values.map((v, i) => <td key={i} className="right nowrap">{Number(v) ? fmtMoney(v) : <span className="muted">—</span>}</td>)}
                    {Number(r.unscheduled) !== 0 && <td className="right nowrap">{fmtMoney(r.unscheduled)}</td>}
                    <td className="right nowrap"><strong>{fmtMoney(r.total)}</strong></td>
                  </tr>
                </tbody>
              </table>
            </div>
            <dl className="kv">
              {r.fields.map(([k, v]) => (
                <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>
              ))}
              {r.justification && <><dt>Justificativa</dt><dd>{r.justification}</dd></>}
              {Object.entries(src).filter(([k, v]) => v !== null && v !== "" && !["label", "file_name", "loaded_at"].includes(k)).map(([k, v]) => (
                <Fragment key={k}><dt>Origem · {SOURCE_LABELS[k] ?? k}</dt><dd>{String(v)}</dd></Fragment>
              ))}
            </dl>
          </td>
        </tr>
      )}
    </>
  );
}
