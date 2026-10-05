import { useState } from "react";
import { MONTHS, fmtCompact, fmtMoney, fmtPct } from "../labels";

/** Séries: ano de referência (slot 1), ano anterior (slot 2), orçamento (slot 3). Cores em styles.css. */
export const SERIES = {
  ref: "var(--series-1)",
  prev: "var(--series-2)",
  budget: "var(--series-3)",
};

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const exp = 10 ** Math.floor(Math.log10(v));
  const step = [1, 2, 2.5, 5, 10].find((s) => s * exp >= v / 4) ?? 10;
  return Math.ceil(v / (step * exp)) * step * exp;
}

export function Legend({ items }: { items: { label: string; color: string; line?: boolean }[] }) {
  return (
    <div className="legend">
      {items.map((i) => (
        <span key={i.label}>
          <i className={i.line ? "legend-line" : "legend-swatch"} style={{ background: i.color }} />
          {i.label}
        </span>
      ))}
    </div>
  );
}

interface MonthlyRow { month: number; prev: string; ref: string; budget: string }

/** Barras agrupadas por mês (ano anterior × ano de referência) + linha opcional de orçamento. Um eixo só, em R$. */
export function MonthlyChart({ rows, prevYear, refYear, showBudget }: { rows: MonthlyRow[]; prevYear: number; refYear: number; showBudget: boolean }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 760, H = 260, L = 84, R = 12, T = 12, B = 28;
  const plotW = W - L - R, plotH = H - T - B;
  const values = rows.flatMap((r) => [Number(r.prev), Number(r.ref), showBudget ? Number(r.budget) : 0]);
  const max = niceMax(Math.max(...values, 0));
  const y = (v: number) => T + plotH - (Math.max(v, 0) / max) * plotH;
  const slot = plotW / 12;
  const barW = Math.min(16, (slot - 10) / 2);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => t * max);
  const budgetPts = rows.map((r, i) => `${L + slot * i + slot / 2},${y(Number(r.budget))}`).join(" ");
  const h = hover !== null ? rows[hover] : null;

  return (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Realizado mensal ${prevYear} e ${refYear}`} onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} className="grid" />
            <text x={L - 8} y={y(t) + 4} className="axis" textAnchor="end">{fmtCompact(t)}</text>
          </g>
        ))}
        {rows.map((r, i) => {
          const x0 = L + slot * i + slot / 2;
          return (
            <g key={r.month}>
              {hover === i && <rect x={L + slot * i} y={T} width={slot} height={plotH} className="hover-band" />}
              {[{ v: Number(r.prev), c: SERIES.prev, dx: -barW - 1 }, { v: Number(r.ref), c: SERIES.ref, dx: 1 }].map(
                (b, j) =>
                  b.v > 0 && (
                    <path
                      key={j}
                      d={roundedTop(x0 + b.dx, y(b.v), barW, T + plotH - y(b.v))}
                      fill={b.c}
                    />
                  ),
              )}
              <text x={x0} y={H - 8} className="axis" textAnchor="middle">{MONTHS[r.month - 1]}</text>
              <rect x={L + slot * i} y={T} width={slot} height={plotH} fill="transparent" onMouseEnter={() => setHover(i)} />
            </g>
          );
        })}
        {showBudget && <polyline points={budgetPts} fill="none" stroke={SERIES.budget} strokeWidth={2} pointerEvents="none" />}
        {showBudget &&
          rows.map((r, i) => (
            <circle key={i} cx={L + slot * i + slot / 2} cy={y(Number(r.budget))} r={4} fill={SERIES.budget} className="ring" pointerEvents="none" />
          ))}
        <line x1={L} x2={W - R} y1={T + plotH} y2={T + plotH} className="baseline" />
      </svg>
      {h && hover !== null && (
        <div className="tooltip" style={{ left: `${((L + slot * hover + slot / 2) / W) * 100}%` }}>
          <strong>{MONTHS[h.month - 1]}</strong>
          <span><i style={{ background: SERIES.prev }} />{prevYear}: {fmtMoney(h.prev)}</span>
          <span><i style={{ background: SERIES.ref }} />{refYear}: {fmtMoney(h.ref)}</span>
          {showBudget && <span><i style={{ background: SERIES.budget }} />Orçado {refYear}: {fmtMoney(h.budget)}</span>}
          {Number(h.prev) > 0 && Number(h.ref) > 0 && <span className="muted">Variação: {fmtPct(String((Number(h.ref) - Number(h.prev)) / Number(h.prev)))}</span>}
        </div>
      )}
    </div>
  );
}

function roundedTop(x: number, y: number, w: number, h: number, r = 4): string {
  const rr = Math.min(r, w / 2, h);
  return `M${x},${y + h} V${y + rr} Q${x},${y} ${x + rr},${y} H${x + w - rr} Q${x + w},${y} ${x + w},${y + rr} V${y + h} Z`;
}

interface CompareRow { label: string; prev: number; ref: number; note?: string }

/** Barras horizontais pareadas (mesmo período nos dois anos) — ranking de pacotes/CCs. */
export function PairedBars({ rows, prevLabel, refLabel }: { rows: CompareRow[]; prevLabel: string; refLabel: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = niceMax(Math.max(...rows.flatMap((r) => [r.prev, r.ref]), 0));
  return (
    <div className="paired">
      {rows.map((r, i) => (
        <div key={r.label} className={`paired-row ${hover === i ? "hover" : ""}`} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
          <div className="paired-label" title={r.label}>{r.label}</div>
          <div className="paired-bars">
            <div className="paired-bar" style={{ width: `${(Math.max(r.prev, 0) / max) * 100}%`, background: SERIES.prev }} />
            <div className="paired-bar" style={{ width: `${(Math.max(r.ref, 0) / max) * 100}%`, background: SERIES.ref }} />
          </div>
          <div className="paired-value">
            {fmtCompact(r.ref)}
            {r.note && <span className={`delta ${r.note.startsWith("+") ? "up" : r.note.startsWith("-") ? "down" : ""}`}>{r.note}</span>}
          </div>
          {hover === i && (
            <div className="tooltip tooltip-inline">
              <strong>{r.label}</strong>
              <span><i style={{ background: SERIES.prev }} />{prevLabel}: {fmtMoney(r.prev)}</span>
              <span><i style={{ background: SERIES.ref }} />{refLabel}: {fmtMoney(r.ref)}</span>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
