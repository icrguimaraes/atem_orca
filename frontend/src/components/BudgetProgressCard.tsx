import type { BudgetProgress } from "../api";
import { SUBMISSION_STATUS, fmtInt, fmtMoney, fmtPct, fmtSignedMoney } from "../labels";
import { Legend, PairedBars, SERIES, StatusBar } from "./charts";
import { PlotlyChart, type Figure } from "./PlotlyChart";
import { Card, Empty, Stat, lines } from "./ui";

const ORDER = ["DRAFT", "IN_PROGRESS", "ADJUSTMENT_REQUESTED", "SUBMITTED", "UNDER_REVIEW", "APPROVED", "CONSOLIDATED"];

/** Andamento do orçamento: CCs por status e proposto × realizado do ano de referência por pacote (só CCs que já
 *  lançaram). Com `figures` (Painel), o mesmo visual dos maiores CCs: cartões de total, barras em Plotly e coluna Total. */
export function BudgetProgressCard({ progress, figures }: { progress: BudgetProgress; figures?: { rank?: Figure; total?: Figure | null } }) {
  const proposed = Number(progress.proposed_total);
  const base = Number(progress.annualized_started_total);
  const baseLabel = progress.ref_label ?? `${progress.ref_year} anualizado`;
  const pct = base ? String(proposed / base - 1) : null;
  return (
    <Card
      title={`Orçamento ${progress.target_year} em construção`}
      actions={
        <span className="muted small">
          {fmtInt(progress.started_cost_centers)} de {fmtInt(progress.total_cost_centers)} CCs com lançamentos ·{" "}
          proposto <strong>{fmtMoney(proposed)}</strong>
          {base ? <> ({fmtPct(String(proposed / base - 1))} vs {baseLabel} dos mesmos CCs)</> : null}
        </span>
      }
    >
      <StatusBar
        items={ORDER.map((k) => ({ key: k, label: SUBMISSION_STATUS[k].label, count: progress.status_counts[k] ?? 0, tone: SUBMISSION_STATUS[k].tone }))}
      />
      <div style={{ marginTop: 16 }}>
        {progress.by_package.length && figures?.rank ? (
          <>
            <div className="stats rank-stats">
              <Stat label={`Total · ${progress.target_year} proposto`} value={fmtMoney(proposed)} hint={lines("CCs que já lançaram")} />
              {base > 0 && (
                <>
                  <Stat label={`Total · ${baseLabel}`} value={fmtMoney(base)} hint={lines("mesmos CCs")} />
                  <div className="stat stat-inline-delta">
                    <span className="stat-label">Variação</span>
                    <span className="stat-value">
                      <span className={`delta ${Number(pct) > 0 ? "up" : Number(pct) < 0 ? "down" : ""}`}>{Number(pct) > 0 ? "▲" : Number(pct) < 0 ? "▼" : "•"} {fmtPct(pct)}</span>
                    </span>
                    <span className="stat-hint">
                      <span className="stat-line"><strong>{fmtSignedMoney(proposed - base)}</strong></span>
                      <span className="stat-line">vs {baseLabel}</span>
                    </span>
                  </div>
                </>
              )}
            </div>
            <div className="monthly-split">
              <PlotlyChart figure={figures.rank} ariaLabel="Proposto por pacote" />
              {figures.total && (
                <div className="monthly-total">
                  <PlotlyChart figure={figures.total} height={360} ariaLabel="Total proposto" />
                </div>
              )}
            </div>
          </>
        ) : progress.by_package.length ? (
          <>
            <Legend items={[{ label: `${progress.ref_year} anualizado`, color: SERIES.past }, { label: `${progress.target_year} proposto`, color: SERIES.budget }]} />
            <PairedBars
              refColor={SERIES.budget}
              prevLabel={`${progress.ref_year} anualizado`}
              refLabel={`${progress.target_year} proposto`}
              rows={progress.by_package.map((p) => ({
                label: p.package,
                prev: Number(p.ref_annualized),
                ref: Number(p.proposed),
                note: Number(p.ref_annualized) && Number(p.proposed) ? fmtPct(String(Number(p.proposed) / Number(p.ref_annualized) - 1)) : undefined,
              }))}
            />
          </>
        ) : (
          <Empty>Nenhum centro de custo lançou valores ainda.</Empty>
        )}
      </div>
    </Card>
  );
}
