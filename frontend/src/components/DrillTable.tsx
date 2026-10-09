import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Breakdown, type BreakdownRow } from "../api";
import { fmtMoney, fmtPct, fmtShare, deptRank } from "../labels";
import { Empty, Loading } from "./ui";
import { WhyButton } from "./WhyPanel";

type Sort = "value_desc" | "value_asc" | "name" | "var";
type Level = Breakdown["group_by"];
// Área → Setor (cadastro do centro de custo) → Pacote GMD → Conta
const LEVELS: Level[] = ["department", "area", "package", "account"];
// filtro que cada nível passa aos filhos: [com id, linha "Sem …"]
const PARENT: Record<Level, [string, string]> = {
  department: ["parent_department_id", "parent_no_department"],
  area: ["parent_area_id", "parent_no_area"],
  package: ["parent_package_id", "parent_no_package"],
  account: ["parent_account_id", ""],
  cost_center: ["", ""],
};
// níveis em que clicar no nome filtra o painel inteiro
const FILTERABLE: Level[] = ["package", "account", "cost_center"];
const SORT_LABELS: Record<Sort, string> = {
  value_desc: "Maior valor",
  value_asc: "Menor valor",
  name: "Nome",
  var: "Maior variação",
};

function sortRows(rows: BreakdownRow[], sort: Sort): BreakdownRow[] {
  const by = {
    value_desc: (a: BreakdownRow, b: BreakdownRow) => Number(b.ref) - Number(a.ref),
    value_asc: (a: BreakdownRow, b: BreakdownRow) => Number(a.ref) - Number(b.ref),
    name: (a: BreakdownRow, b: BreakdownRow) => a.name.localeCompare(b.name, "pt-BR"),
    var: (a: BreakdownRow, b: BreakdownRow) => Math.abs(Number(b.var)) - Math.abs(Number(a.var)),
  }[sort];
  // área: Vice-Presidência sempre primeiro (ordem de importância), qualquer que seja a ordenação escolhida
  return [...rows].sort((a, b) => deptRank(a.name) - deptRank(b.name) || by(a, b));
}

/** Semáforo da variação, com os limiares de alerta do ciclo (os mesmos do orçamento OPEX):
 * vermelho acima do limite de crescimento (`alert.growth_pct`), amarelo abaixo do limite de redução
 * (`alert.reduction_pct`, queda forte que pede atenção) e verde dentro da faixa. */
function tone(pct: string | null, t: { growth: number; reduction: number }): "good" | "bad" | null {
  if (pct === null) return null;
  const n = Number(pct);
  // só verde e vermelho (09/10/2026): fora da faixa, para cima ou para baixo, é vermelho
  return n > t.growth || n < -t.reduction ? "bad" : "good";
}

function VarCell({ pct, t }: { pct: string | null; t: { growth: number; reduction: number } }) {
  const k = tone(pct, t);
  return (
    <span className="var-cell">
      <span className={`dot ${k ?? "none"}`} aria-hidden="true" />
      <span className="var-num">{pct !== null ? fmtPct(pct) : "—"}</span>
    </span>
  );
}

const fold = (s: string) => s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();

function ownParam(level: Level, row: BreakdownRow): string {
  const [byId, none] = PARENT[level];
  if (row.id === null) return none ? `${none}=true` : "";
  return byId ? `${byId}=${row.id}` : "";
}

/** Tabela do painel com drill-down (área → setor → pacote GMD → conta) e drill-up (recolher).
 * `query` traz os filtros da página (empresa, CC, pacote, anos, meses); cada nível é buscado ao expandir,
 * filtrado por toda a linha de cima (ex.: contas do pacote Viagens no setor Fiscal da área Tributos). */
export function DrillTable({ query, refLabel, onSelect, why = false }: {
  query: string; refLabel: string; onSelect?: (level: Level, row: BreakdownRow) => void;
  why?: boolean;  // botão "por quê?" (defesa do orçamento) em cada linha
}) {
  const [root, setRoot] = useState<Breakdown | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [children, setChildren] = useState<Record<string, BreakdownRow[] | "loading">>({});
  const [chains, setChains] = useState<Record<string, string>>({});  // filtros acumulados de cada linha aberta
  const [sort, setSort] = useState<Sort>("value_desc");
  const [search, setSearch] = useState("");

  useEffect(() => {
    let alive = true;
    setChildren({});
    setChains({});
    api<Breakdown>(`/dashboard/breakdown?group_by=${LEVELS[0]}${query ? `&${query}` : ""}`)
      .then(async (d) => {
        if (!alive) return;
        setRoot(d);
        setError(null);
        // por padrão a área já vem aberta (mostra os setores)
        const open = d.rows.filter((r) => r.has_children);
        const results = await Promise.all(
          open.map(async (r) => {
            const chain = ownParam(LEVELS[0], r);
            const kids = await api<Breakdown>(
              `/dashboard/breakdown?group_by=${LEVELS[1]}${chain ? `&${chain}` : ""}${query ? `&${query}` : ""}`,
            ).catch(() => null);
            return [`/${r.id ?? "none"}`, chain, kids?.rows ?? null] as const;
          }),
        );
        if (!alive) return;
        setChildren(Object.fromEntries(results.filter(([, , rows]) => rows).map(([k, , rows]) => [k, rows as BreakdownRow[]])));
        setChains(Object.fromEntries(results.filter(([, , rows]) => rows).map(([k, chain]) => [k, chain])));
      })
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [query]);

  async function toggle(key: string, level: number, row: BreakdownRow, parentChain: string) {
    if (children[key]) {
      // drill-up: recolhe a linha e tudo abaixo dela
      setChildren((c) => Object.fromEntries(Object.entries(c).filter(([k]) => k !== key && !k.startsWith(`${key}/`))));
      return;
    }
    setChildren((c) => ({ ...c, [key]: "loading" }));
    const chain = [parentChain, ownParam(LEVELS[level], row)].filter(Boolean).join("&");
    setChains((c) => ({ ...c, [key]: chain }));
    try {
      const d = await api<Breakdown>(
        `/dashboard/breakdown?group_by=${LEVELS[level + 1]}${chain ? `&${chain}` : ""}${query ? `&${query}` : ""}`,
      );
      setChildren((c) => ({ ...c, [key]: d.rows }));
    } catch (e) {
      setError((e as Error).message);
      setChildren((c) => Object.fromEntries(Object.entries(c).filter(([k]) => k !== key)));
    }
  }

  if (error) return <Empty>{error}</Empty>;
  if (!root) return <Loading />;
  if (!root.rows.length) return <Empty>Sem dados para os filtros.</Empty>;
  const hasBase = root.base !== null;
  const expanded = Object.keys(children).length > 0;
  const th = root.thresholds;
  const pctLabel = (v: number) => `${Math.round(v * 100)}%`;

  const needle = fold(search.trim());
  const rowMatches = (r: BreakdownRow) => fold(`${r.name} ${r.code ?? ""}`).includes(needle);
  function matches(r: BreakdownRow, key: string): boolean {
    if (!needle || rowMatches(r)) return true;
    const kids = children[key];
    return Array.isArray(kids) && kids.some((k) => matches(k, `${key}/${k.id ?? "none"}`));
  }

  function renderRows(rows: BreakdownRow[], level: number, parentKey: string, parentChain = "", parentHit = false): JSX.Element[] {
    return sortRows(rows, sort).flatMap((r, index) => {
      const key = `${parentKey}/${r.id ?? "none"}`;
      // linha que bate com o filtro mostra todos os filhos abertos; senão, só os caminhos que levam a uma linha que bate
      if (!parentHit && !matches(r, key)) return [];
      const hit = parentHit || (Boolean(needle) && rowMatches(r));
      const kids = children[key];
      const line = (
        <tr key={key} className={`drill-level-${level}${index % 2 ? " drill-alt" : ""}${needle && rowMatches(r) ? " drill-hit" : ""}`}>
          <td>
            {r.has_children && level < LEVELS.length - 1 ? (
              <button
                type="button"
                className="drill-toggle"
                aria-expanded={Boolean(kids)}
                aria-label={kids ? `Recolher ${r.name}` : `Detalhar ${r.name}`}
                onClick={() => toggle(key, level, r, parentChain)}
              >
                {kids ? "−" : "+"}
              </button>
            ) : (
              <span className="drill-toggle drill-leaf" aria-hidden="true" />
            )}
            {onSelect && r.id !== null && FILTERABLE.includes(LEVELS[level]) ? (
              <button type="button" className="row-filter" title="Filtrar o painel por esta linha" onClick={() => onSelect(LEVELS[level], r)}>
                {r.name}
              </button>
            ) : (
              r.name
            )}
            {r.code && <span className="muted small mono"> {r.code}</span>}
            {why && <WhyButton qs={[parentChain, ownParam(LEVELS[level], r), query].filter(Boolean).join("&")} label={r.name} />}
            {why && (
              <Link className="why-btn trace-link" to={`/rastro?${[parentChain, ownParam(LEVELS[level], r), query].filter(Boolean).join("&")}`} title="Abrir o Rastro já neste recorte (até o lançamento)">
                ver rastro
              </Link>
            )}
          </td>
          <td className="right nowrap">{fmtMoney(r.ref)}</td>
          <td className="right nowrap muted">{fmtShare(r.share_ref)}</td>
          {hasBase && <td className="right nowrap">{fmtMoney(r.base)}</td>}
          {hasBase && <td className="right nowrap muted">{fmtShare(r.share_base)}</td>}
          {hasBase && <td className="right nowrap">{fmtMoney(r.var)}</td>}
          {hasBase && (
            <td className="right nowrap">
              <VarCell pct={r.var_pct} t={th} />
            </td>
          )}
        </tr>
      );
      if (kids === "loading") {
        return [
          line,
          <tr key={`${key}/loading`} className={`drill-level-${level + 1}`}>
            <td colSpan={hasBase ? 7 : 3} className="muted small">
              Carregando…
            </td>
          </tr>,
        ];
      }
      return kids ? [line, ...renderRows(kids, level + 1, key, chains[key] ?? "", hit)] : [line];
    });
  }

  const body = renderRows(root.rows, 0, "");

  return (
    <>
      <div className="section-tools">
        <span className="muted small">
          {onSelect ? "Clique no + para detalhar (área → setor → pacote → conta) e no nome do pacote ou da conta para filtrar o painel." : "Clique em + para detalhar (área → setor → pacote → conta)."} AV %: participação no total da coluna.
          {hasBase && (
            <>
              {" "}Semáforo: <span className="dot good" aria-hidden="true" />dentro da faixa (de −{pctLabel(th.reduction)} a +{pctLabel(th.growth)}),{" "}
              <span className="dot bad" aria-hidden="true" />fora da faixa (acima de +{pctLabel(th.growth)} ou queda maior que {pctLabel(th.reduction)})
              {" "}(limites de alerta do ciclo).
            </>
          )}
        </span>
        <div className="inline-controls">
          <input
            type="search"
            className="drill-search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filtrar área, setor, pacote ou conta…"
            aria-label="Filtrar linhas da tabela"
            title="Filtra as linhas carregadas (abra com + para buscar dentro de um nível)"
          />
          <label className="small muted">
            Ordenar{" "}
            <select value={sort} onChange={(e) => setSort(e.target.value as Sort)} aria-label="Ordenar linhas">
              {(Object.keys(SORT_LABELS) as Sort[]).map((k) => (
                <option key={k} value={k}>
                  {SORT_LABELS[k]}
                </option>
              ))}
            </select>
          </label>
          {expanded && (
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setChildren({})}>
              Recolher tudo
            </button>
          )}
        </div>
      </div>
      <div className="table-wrap">
        <table className="table drill-table">
          <thead>
            <tr>
              <th>Descrição</th>
              <th className="right">{refLabel}</th>
              <th className="right">AV %</th>
              {hasBase && <th className="right">{root.base_label}</th>}
              {hasBase && <th className="right">AV %</th>}
              {hasBase && <th className="right">Variação</th>}
              {hasBase && <th className="right">Variação %</th>}
            </tr>
          </thead>
          <tbody>
            {body.length ? body : (
              <tr>
                <td colSpan={hasBase ? 7 : 3} className="muted small">
                  Nenhuma linha aberta contém “{search.trim()}”. Abra os níveis com + para buscar pacotes e contas.
                </td>
              </tr>
            )}
          </tbody>
          <tfoot>
            <tr>
              <td>Total</td>
              <td className="right nowrap">{fmtMoney(root.total.ref)}</td>
              <td className="right nowrap muted">100%</td>
              {hasBase && <td className="right nowrap">{fmtMoney(root.total.base)}</td>}
              {hasBase && <td className="right nowrap muted">100%</td>}
              {hasBase && <td className="right nowrap">{fmtMoney(root.total.var)}</td>}
              {hasBase && (
                <td className="right nowrap">
                  <VarCell pct={root.total.var_pct} t={th} />
                </td>
              )}
            </tr>
          </tfoot>
        </table>
      </div>
    </>
  );
}
