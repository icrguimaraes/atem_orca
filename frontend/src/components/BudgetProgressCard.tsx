import type { BudgetProgress } from "../api";
import { SUBMISSION_STATUS, fmtCompact, fmtInt, fmtPct } from "../labels";
import { Legend, PairedBars, SERIES, StatusBar } from "./charts";
import { Card, Empty } from "./ui";

const ORDER = ["DRAFT", "IN_PROGRESS", "ADJUSTMENT_REQUESTED", "SUBMITTED", "UNDER_REVIEW", "APPROVED", "CONSOLIDATED"];

/** Andamento do orçamento: CCs por status e proposto × anualizado por pacote (só CCs que já lançaram). */
export function BudgetProgressCard({ progress }: { progress: BudgetProgress }) {
  const proposed = Number(progress.proposed_total);
  const base = Number(progress.annualized_started_total);
  return (
    <Card
      title={`Orçamento ${progress.target_year} em construção`}
      actions={
        <span className="muted small">
          {fmtInt(progress.started_cost_centers)} de {fmtInt(progress.total_cost_centers)} CCs com lançamentos ·{" "}
          proposto <strong>{fmtCompact(proposed)}</strong>
          {base ? <> ({fmtPct(String(proposed / base - 1))} vs {progress.ref_year} anualizado dos mesmos CCs)</> : null}
        </span>
      }
    >
      <StatusBar
        items={ORDER.map((k) => ({ key: k, label: SUBMISSION_STATUS[k].label, count: progress.status_counts[k] ?? 0, tone: SUBMISSION_STATUS[k].tone }))}
      />
      <div style={{ marginTop: 16 }}>
        {progress.by_package.length ? (
          <>
            <Legend items={[{ label: `${progress.ref_year} anualizado`, color: SERIES.past }, { label: `${progress.target_year} proposto`, color: SERIES.ref }]} />
            <PairedBars
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
