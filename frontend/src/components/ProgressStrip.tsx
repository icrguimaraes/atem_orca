import { Link } from "react-router-dom";
import type { BudgetProgress } from "../api";
import { SUBMISSION_STATUS, fmtInt } from "../labels";

const ORDER = ["DRAFT", "IN_PROGRESS", "ADJUSTMENT_REQUESTED", "SUBMITTED", "UNDER_REVIEW", "APPROVED", "CONSOLIDATED"];
const SENT = new Set(["SUBMITTED", "UNDER_REVIEW", "APPROVED", "CONSOLIDATED"]);

/** Faixa de uma linha com o andamento do envio do orçamento OPEX por centro de custo (substitui o card "Orçamento em
 * construção", 13/10/2026: as barras repetiam a tabela do Painel; o andamento por módulo está na Consolidação). */
export function ProgressStrip({ progress }: { progress: BudgetProgress }) {
  const counts = progress.status_counts;
  const parts = ORDER.filter((k) => (counts[k] ?? 0) > 0).map((k) => `${fmtInt(counts[k])} ${SUBMISSION_STATUS[k].label.toLowerCase()}`);
  if (!ORDER.some((k) => SENT.has(k) && (counts[k] ?? 0) > 0)) parts.push("nenhum enviado ainda");
  return (
    <p className="progress-strip">
      <strong>Andamento do OPEX</strong>
      <span>{fmtInt(progress.total_cost_centers)} centro{progress.total_cost_centers === 1 ? "" : "s"} de custo: {parts.join(" · ")}</span>
      <Link to="/consolidacao">andamento por módulo →</Link>
    </p>
  );
}
