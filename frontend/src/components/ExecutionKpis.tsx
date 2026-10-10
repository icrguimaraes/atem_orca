import type { ReactNode } from "react";
import type { Execution } from "../api";
import { fmtMoney, fmtPct, fmtShare, fmtSignedMoney } from "../labels";

/** Direção do desvio em texto e ícone (não só cor): acima do orçado = vermelho ▲, abaixo = verde ▼. */
export function DeviationTag({ value, pct, above = "acima do orçado", below = "abaixo do orçado" }: {
  value: string | number; pct: string | null | undefined; above?: string; below?: string;
}) {
  const n = Number(value);
  const cls = n > 0 ? "up" : n < 0 ? "down" : "flat";
  const icon = n > 0 ? "▲" : n < 0 ? "▼" : "•";
  const word = n > 0 ? above : n < 0 ? below : "em linha";
  return (
    <span className={`dev-tag ${cls}`}>
      <span aria-hidden="true">{icon}</span> {pct !== null && pct !== undefined ? `${fmtPct(pct)} ` : ""}{word}
    </span>
  );
}

function Kpi({ label, swatch, value, tone, children }: {
  label: string; swatch?: "actual" | "budget" | "proj"; value: ReactNode; tone?: "up" | "down" | "muted"; children?: ReactNode;
}) {
  return (
    <div className="kpi">
      <span className="kpi-label">
        {swatch && <i className={`kpi-swatch ${swatch}`} aria-hidden="true" />}
        {label}
      </span>
      <span className={`kpi-value${tone ? ` ${tone}` : ""}`}>{value}</span>
      {children && <span className="kpi-hint">{children}</span>}
    </div>
  );
}

/** Execução do orçamento (Painel, 10/10/2026): orçado do ano, realizado contábil até o mês fechado, desvio no mesmo
 * período (R$ e %), % de execução e projeção de fechamento — esta só com a projeção do gestor completa; senão,
 * "Indisponível" e o motivo (nada é estimado no lugar). `linear` é a anualização linear, mostrada só como referência. */
export function ExecutionKpis({ ex, monthsTxt, linear }: { ex: Execution; monthsTxt: string; linear?: string | null }) {
  if (!ex.available) return null;
  const fc = ex.forecast;
  const exec = ex.execution_pct !== null && ex.execution_pct !== undefined ? Number(ex.execution_pct) : null;
  const phase = ex.budget_phase_pct !== null && ex.budget_phase_pct !== undefined ? Number(ex.budget_phase_pct) : null;
  const partial = (ex.closed ?? 12) < 12;
  const upTo = partial ? ` até ${ex.closed_month}` : "";
  const varN = Number(ex.var);
  return (
    <section className="kpi-band" aria-label={`Execução do orçamento ${ex.year}`}>
      <div className="kpi-band-head">
        <h2>Execução do orçamento {ex.year}{monthsTxt}</h2>
        <span className="muted small">
          {partial ? `Realizado contábil até ${ex.closed_month}, sem a projeção do gestor` : "Ano fechado no realizado contábil"}
        </span>
      </div>
      <div className="kpi-grid">
        <Kpi label={`${ex.budget_label}${monthsTxt ? " (meses filtrados)" : ""}`} swatch="budget" value={fmtMoney(ex.budget_year ?? 0)}>
          {partial && <>Orçado{upTo}: <strong>{fmtMoney(ex.budget_ytd ?? 0)}</strong></>}
        </Kpi>
        <Kpi label={ex.actual_label ?? "Realizado"} swatch="actual" value={fmtMoney(ex.actual_ytd ?? 0)}>
          {ex.monthly_avg && <>Média mensal: <strong>{fmtMoney(ex.monthly_avg)}</strong></>}
        </Kpi>
        <Kpi label={`Desvio vs ${ex.budget_ytd_label}`} value={fmtSignedMoney(ex.var ?? 0)} tone={varN > 0 ? "up" : varN < 0 ? "down" : undefined}>
          {ex.var_pct !== null && ex.var_pct !== undefined ? <DeviationTag value={ex.var ?? 0} pct={ex.var_pct} /> : "Sem orçado no período"}
        </Kpi>
        <Kpi label="% de execução do orçado" value={exec !== null ? fmtShare(ex.execution_pct) : "—"}>
          {exec !== null && (
            <span
              className="kpi-meter"
              role="img"
              aria-label={`Executado ${fmtShare(ex.execution_pct)} do orçado do ano${phase !== null ? `; o orçado${upTo} equivale a ${fmtShare(ex.budget_phase_pct)}` : ""}`}
            >
              <span className="kpi-meter-fill" style={{ width: `${Math.min(100, Math.max(0, exec * 100))}%` }} />
              {phase !== null && partial && <span className="kpi-meter-mark" style={{ left: `${Math.min(100, Math.max(0, phase * 100))}%` }} />}
            </span>
          )}
          {phase !== null && partial ? <>Orçado{upTo} = {fmtShare(ex.budget_phase_pct)} do ano (marca)</> : "do orçado do ano"}
        </Kpi>
        {fc?.available && fc.value !== null ? (
          <Kpi
            label={fc.kind === "projection" ? "Projeção de fechamento" : `Fechamento ${ex.year}`}
            swatch={fc.kind === "projection" ? "proj" : "actual"}
            value={fmtMoney(fc.value)}
          >
            {fc.var !== null && (
              <>
                <strong>{fmtSignedMoney(fc.var)}</strong> <DeviationTag value={fc.var} pct={fc.var_pct} />
              </>
            )}
            {fc.kind === "projection" && <span className="stat-line">realizado + projeção do gestor {fc.from_month}–{fc.to_month}</span>}
          </Kpi>
        ) : (
          <Kpi label="Projeção de fechamento" swatch="proj" value="Indisponível" tone="muted">
            {fc?.reason}
            {linear && <span className="stat-line">Anualização linear (só referência): {fmtMoney(linear)}</span>}
          </Kpi>
        )}
      </div>
    </section>
  );
}
