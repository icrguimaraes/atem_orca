import { Link } from "react-router-dom";
import { api, type DeviationRow, type Deviations } from "../api";
import { fmtMoney, fmtSignedMoney } from "../labels";
import { usePersistentState } from "../persist";
import { DeviationTag } from "./ExecutionKpis";
import { Alert, Empty, useLoad } from "./ui";
import { WhyButton } from "./WhyPanel";

type Group = keyof Deviations["groups"];
const TABS: { key: Group; label: string }[] = [
  { key: "pair", label: "Conta × centro de custo" },
  { key: "cost_center", label: "Centros de custo" },
  { key: "account", label: "Contas" },
];
const SERIES_CLASS: Record<string, string> = { actual: "actual", budget: "budget", prev: "prev" };

/** Recorte de uma linha: filtros da página com o CC e/ou a conta da linha (por quê?, Rastro). */
function rowQuery(query: string, row: DeviationRow): string {
  const qs = new URLSearchParams(query);
  if (row.cost_center) qs.set("cost_center_id", String(row.cost_center.id));
  if (row.account) qs.set("account_id", String(row.account.id));
  return qs.toString();
}

function rowLabel(row: DeviationRow): string {
  if (row.account && row.cost_center) return `${row.account.name} · ${row.cost_center.name}`;
  return row.account?.name ?? row.cost_center?.name ?? "—";
}

/** Maiores desvios (Painel, 10/10/2026): onde o realizado mais se afasta do orçado (ou da base escolhida), por
 * conta × centro de custo, por CC e por conta, com empresa, valores, desvio em R$ e %, marca "fora da faixa" e as
 * ações do Painel (filtrar, por quê?, ver rastro). Nome do CC sem código, como nos outros visuais. */
export function DeviationHighlights({ query, selectedCostCenter, selectedAccount, onPick }: {
  query: string;
  selectedCostCenter: string;
  selectedAccount: number | null;
  /** filtra o Painel pelo CC e/ou conta da linha; `unpick` = a linha já estava filtrada (clicar de novo desmarca) */
  onPick: (costCenter: number | null, account: { id: number; label: string } | null, unpick: boolean) => void;
}) {
  const [tab, setTab] = usePersistentState<Group>("painel.deviationsTab", "pair");
  const load = useLoad(() => api<Deviations>(`/dashboard/deviations${query ? `?${query}` : ""}`), [query]);
  const d = load.data;
  const rows = d?.groups[tab] ?? [];
  const mainCls = d ? SERIES_CLASS[d.main_series] : "";
  const baseCls = d?.base_series ? SERIES_CLASS[d.base_series] : "";
  const isBudgetBase = d?.base_series === "budget";

  function select(row: DeviationRow) {
    const account = row.account ? { id: row.account.id, label: `${row.account.name} · ${row.account.code}` } : null;
    onPick(row.cost_center?.id ?? null, account, isSelected(row));
  }
  const isSelected = (row: DeviationRow) =>
    (!row.cost_center || selectedCostCenter === String(row.cost_center.id)) &&
    (!row.account || selectedAccount === row.account.id) &&
    Boolean(row.cost_center || row.account);

  return (
    <div className={`deviations${load.loading && d ? " is-loading" : ""}`} aria-busy={load.loading}>
      <div className="dev-head">
        <div className="seg-tabs" role="tablist" aria-label="Agrupar desvios por">
          {TABS.map((t) => (
            <button key={t.key} type="button" role="tab" aria-selected={tab === t.key} className={tab === t.key ? "active" : ""} onClick={() => setTab(t.key)}>
              {t.label}
            </button>
          ))}
        </div>
        {d && d.base_label && (
          <p className="muted small dev-basis">
            <i className={`kpi-swatch ${mainCls}`} aria-hidden="true" /> {d.main_label} × <i className={`kpi-swatch ${baseCls}`} aria-hidden="true" /> {d.base_label}
            {" · "}maiores diferenças em valor; clique no nome para filtrar o Painel
          </p>
        )}
      </div>
      {load.error && <Alert>{load.error}</Alert>}
      {!d && !load.error ? (
        <Empty>Carregando desvios…</Empty>
      ) : d && !d.base_label ? (
        <Empty>Sem base de comparação neste recorte: selecione um ano com orçado ou ative "Comparar com o ano anterior".</Empty>
      ) : d && !rows.length ? (
        <Empty>Nenhum desvio neste recorte.</Empty>
      ) : d ? (
        <ol className="dev-list">
          <li className="dev-row dev-row-head" aria-hidden="true">
            <span />
            <span>{tab === "account" ? "Conta" : tab === "cost_center" ? "Centro de custo · empresa" : "Conta · centro de custo · empresa"}</span>
            <span className="right">{d.base_label}</span>
            <span className="right">{d.main_label}</span>
            <span className="right">Desvio</span>
          </li>
          {rows.map((row, i) => {
            const n = Number(row.var);
            const qs = rowQuery(query, row);
            return (
              <li key={`${row.cost_center?.id ?? ""}-${row.account?.id ?? ""}`} className={`dev-row${isSelected(row) ? " picked" : ""}`}>
                <span className="dev-rank">{i + 1}</span>
                <span className="dev-item">
                  <button type="button" className="row-filter dev-name" title="Filtrar o Painel por esta linha (de novo para desmarcar)" onClick={() => select(row)}>
                    {row.account ? row.account.name : row.cost_center?.name}
                  </button>
                  {row.account && <span className="muted small mono"> {row.account.code}</span>}
                  <span className="dev-sub muted small">
                    {[row.account && row.cost_center ? row.cost_center.name : null, row.cost_center?.company].filter(Boolean).join(" · ")}
                  </span>
                  <span className="dev-actions">
                    {row.out_of_range && <span className="dev-flag" title="Fora da faixa dos limiares do ciclo">fora da faixa</span>}
                    <WhyButton qs={qs} label={rowLabel(row)} />
                    <Link className="why-btn trace-link" to={`/rastro?${qs}`} title="Abrir o Rastro neste recorte (até o lançamento)">ver rastro</Link>
                  </span>
                </span>
                <span className="dev-val right"><span className="dev-val-label">{d.base_label}</span>{fmtMoney(row.base)}</span>
                <span className="dev-val right"><span className="dev-val-label">{d.main_label}</span>{fmtMoney(row.main)}</span>
                <span className={`dev-var right ${n > 0 ? "up" : n < 0 ? "down" : ""}`}>
                  <strong>{fmtSignedMoney(row.var)}</strong>
                  {row.var_pct !== null ? (
                    <DeviationTag value={row.var} pct={row.var_pct} above={isBudgetBase ? "acima do orçado" : "acima"} below={isBudgetBase ? "abaixo do orçado" : "abaixo"} />
                  ) : (
                    <span className="dev-tag up">sem valor na base</span>
                  )}
                </span>
              </li>
            );
          })}
        </ol>
      ) : null}
    </div>
  );
}
