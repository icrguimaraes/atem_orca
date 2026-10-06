import { useEffect, useRef, useState, type RefObject } from "react";
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
/** Largura real do container: o SVG é desenhado em escala 1:1 (texto e barras não esticam em telas largas). */
function useWidth(fallback = 760): [RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const width = Math.round(entries[0].contentRect.width);
      if (width > 0) setW(Math.max(360, width));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

export function MonthlyChart({ rows, prevYear, refYear, showBudget }: { rows: MonthlyRow[]; prevYear: number | null; refYear: number; showBudget: boolean }) {
  const [hover, setHover] = useState<number | null>(null);
  const [box, W] = useWidth();
  const H = 260, L = 84, R = 12, T = 12, B = 28;
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
    <div className="chart" ref={box}>
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label={prevYear ? `Realizado mensal ${prevYear} e ${refYear}` : `Realizado mensal ${refYear}`} onMouseLeave={() => setHover(null)}>
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
        <ExtremeLabels values={rows.map((r) => Number(r.prev))} x={(i) => L + slot * i + slot / 2} y={y} color={SERIES.prev} dx={-barW / 2 - 1} />
        <ExtremeLabels values={rows.map((r) => Number(r.ref))} x={(i) => L + slot * i + slot / 2} y={y} color={SERIES.ref} dx={barW / 2 + 1} />
        {showBudget && <polyline points={budgetPts} fill="none" stroke={SERIES.budget} strokeWidth={2} pointerEvents="none" />}
        {showBudget && <ExtremeLabels values={rows.map((r) => Number(r.budget))} x={(i) => L + slot * i + slot / 2} y={y} color={SERIES.budget} />}
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

/** Índices do maior e do menor valor (> 0) de uma série: gráficos sem rótulo mostram sempre os extremos. */
function extremes(values: number[]): { i: number; v: number; kind: "max" | "min" }[] {
  const pos = values.map((v, i) => ({ v, i })).filter((p) => p.v > 0);
  if (!pos.length) return [];
  const hi = pos.reduce((a, b) => (b.v > a.v ? b : a));
  const lo = pos.reduce((a, b) => (b.v < a.v ? b : a));
  return hi.i === lo.i ? [{ ...hi, kind: "max" }] : [{ ...hi, kind: "max" }, { ...lo, kind: "min" }];
}

function ExtremeLabels({ values, x, y, color, format = fmtCompact, dx = 0 }: {
  values: number[]; x: (i: number) => number; y: (v: number) => number; color: string; format?: (n: number) => string; dx?: number;
}) {
  return (
    <>
      {extremes(values).map((e) => (
        <text key={e.kind} x={x(e.i) + dx} y={y(e.v) - 7} className="extreme" textAnchor="middle" fill={color} pointerEvents="none">
          {format(e.v)}
        </text>
      ))}
    </>
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
            {prevLabel && <div className="paired-bar" style={{ width: `${(Math.max(r.prev, 0) / max) * 100}%`, background: SERIES.prev }} />}
            <div className="paired-bar" style={{ width: `${(Math.max(r.ref, 0) / max) * 100}%`, background: SERIES.ref }} />
          </div>
          <div className="paired-value">
            {fmtCompact(r.ref)}
            {r.note && <span className={`delta ${r.note.startsWith("+") ? "up" : r.note.startsWith("-") ? "down" : ""}`}>{r.note}</span>}
          </div>
          {hover === i && (
            <div className="tooltip tooltip-inline">
              <strong>{r.label}</strong>
              {prevLabel && <span><i style={{ background: SERIES.prev }} />{prevLabel}: {fmtMoney(r.prev)}</span>}
              <span><i style={{ background: SERIES.ref }} />{refLabel}: {fmtMoney(r.ref)}</span>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// ============================================================ gráficos adicionais

/** Barras mensais com N séries (máx. 3 categóricas) + linha opcional. Um eixo só, em R$. */
export function MonthlyBars({
  series,
  line,
  height = 260,
  format,
  axisFormat,
}: {
  series: { label: string; color: string; values: number[] }[];
  line?: { label: string; color: string; values: number[] };
  height?: number;
  format?: (n: number) => string;
  axisFormat?: (n: number) => string;
}) {
  const tip = format ?? fmtMoney;
  const axis = axisFormat ?? format ?? fmtCompact;
  const [hover, setHover] = useState<number | null>(null);
  const [box, W] = useWidth();
  const H = height, L = 84, R = 12, T = 12, B = 28;
  const plotW = W - L - R, plotH = H - T - B;
  const max = niceMax(Math.max(0, ...series.flatMap((s) => s.values), ...(line?.values ?? [])));
  const y = (v: number) => T + plotH - (Math.max(v, 0) / max) * plotH;
  const slot = plotW / 12;
  const gap = 2;
  const barW = Math.min(14, (slot - 10 - gap * (series.length - 1)) / series.length);
  const groupW = barW * series.length + gap * (series.length - 1);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => t * max);
  return (
    <div className="chart" ref={box}>
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label={series.map((s) => s.label).join(", ")} onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} className="grid" />
            <text x={L - 8} y={y(t) + 4} className="axis" textAnchor="end">{axis(t)}</text>
          </g>
        ))}
        {MONTHS.map((m, i) => {
          const x0 = L + slot * i + (slot - groupW) / 2;
          return (
            <g key={m}>
              {hover === i && <rect x={L + slot * i} y={T} width={slot} height={plotH} className="hover-band" />}
              {series.map((s, j) =>
                s.values[i] > 0 ? (
                  <path key={j} d={roundedTop(x0 + j * (barW + gap), y(s.values[i]), barW, T + plotH - y(s.values[i]))} fill={s.color} />
                ) : null,
              )}
              <text x={L + slot * i + slot / 2} y={H - 8} className="axis" textAnchor="middle">{m}</text>
              <rect x={L + slot * i} y={T} width={slot} height={plotH} fill="transparent" onMouseEnter={() => setHover(i)} />
            </g>
          );
        })}
        {series.map((s, j) => (
          <ExtremeLabels key={s.label} values={s.values} x={(i) => L + slot * i + (slot - groupW) / 2 + j * (barW + gap) + barW / 2} y={y} color={s.color} format={axis} />
        ))}
        {line && line.values.some((v) => v > 0) && (
          <>
            <polyline
              points={line.values.map((v, i) => `${L + slot * i + slot / 2},${y(v)}`).join(" ")}
              fill="none" stroke={line.color} strokeWidth={2} pointerEvents="none"
            />
            <ExtremeLabels values={line.values} x={(i) => L + slot * i + slot / 2} y={y} color={line.color} format={axis} />
            {line.values.map((v, i) => (
              <circle key={i} cx={L + slot * i + slot / 2} cy={y(v)} r={4} fill={line.color} className="ring" pointerEvents="none" />
            ))}
          </>
        )}
        <line x1={L} x2={W - R} y1={T + plotH} y2={T + plotH} className="baseline" />
      </svg>
      {hover !== null && (
        <div className="tooltip" style={{ left: `${((L + slot * hover + slot / 2) / W) * 100}%` }}>
          <strong>{MONTHS[hover]}</strong>
          {series.map((s) => (
            <span key={s.label}><i style={{ background: s.color }} />{s.label}: {tip(s.values[hover])}</span>
          ))}
          {line && <span><i style={{ background: line.color }} />{line.label}: {tip(line.values[hover])}</span>}
        </div>
      )}
    </div>
  );
}

/** Ponte (cascata horizontal): total inicial → variação por categoria → total final. */
export function Waterfall({
  start,
  end,
  steps,
}: {
  start: { label: string; value: number };
  end: { label: string; value: number };
  steps: { label: string; delta: number }[];
}) {
  const [hover, setHover] = useState<number | null>(null);
  let acc = start.value;
  const rows = [
    { label: start.label, from: 0, to: start.value, kind: "total-prev" as const, delta: start.value },
    ...steps.map((s) => {
      const from = acc;
      acc += s.delta;
      return { label: s.label, from, to: acc, kind: s.delta >= 0 ? ("up" as const) : ("down" as const), delta: s.delta };
    }),
    { label: end.label, from: 0, to: end.value, kind: "total-ref" as const, delta: end.value },
  ];
  const max = niceMax(Math.max(...rows.map((r) => Math.max(r.from, r.to))));
  const pct = (v: number) => `${(Math.max(v, 0) / max) * 100}%`;
  const color = { "total-prev": SERIES.prev, "total-ref": SERIES.ref, up: "var(--div-up)", down: "var(--div-down)" };
  return (
    <div className="waterfall">
      {rows.map((r, i) => (
        <div key={r.label + i} className={`wf-row ${hover === i ? "hover" : ""} ${r.kind.startsWith("total") ? "wf-total" : ""}`}
             onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
          <div className="paired-label" title={r.label}>{r.label}</div>
          <div className="wf-track">
            <div className="wf-bar" style={{ left: pct(Math.min(r.from, r.to)), width: `calc(${pct(Math.abs(r.to - r.from))} + 1px)`, background: color[r.kind] }} />
          </div>
          <div className="paired-value">
            {r.kind.startsWith("total") ? fmtCompact(r.to) : (
              <span className={`delta ${r.kind === "up" ? "up" : "down"}`}>{r.delta >= 0 ? "▲ +" : "▼ "}{fmtCompact(r.delta).replace("R$ -", "R$ ")}</span>
            )}
          </div>
          {hover === i && (
            <div className="tooltip tooltip-inline">
              <strong>{r.label}</strong>
              {r.kind.startsWith("total") ? <span>{fmtMoney(r.to)}</span> : (
                <>
                  <span>Variação: {fmtMoney(r.delta)}</span>
                  <span className="muted">Acumulado: {fmtMoney(r.to)}</span>
                </>
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

/** Mapa de calor (linhas × 12 meses), escala sequencial de um só tom. */
export function Heatmap({ rows }: { rows: { label: string; sub?: string | null; values: number[]; total: number }[] }) {
  const max = Math.max(1, ...rows.flatMap((r) => r.values));
  const shade = (v: number) =>
    v <= 0 ? "var(--surface-2)" : `color-mix(in oklab, var(--seq-hi) ${Math.round(12 + (v / max) * 88)}%, var(--seq-lo))`;
  return (
    <div className="table-wrap">
      <table className="heatmap">
        <thead>
          <tr>
            <th />
            {MONTHS.map((m) => <th key={m}>{m}</th>)}
            <th className="right">Total</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.label + (r.sub ?? "")}>
              <th className="hm-label" title={r.label}>
                <span>{r.label}</span>
                {r.sub && <span className="muted small mono">{r.sub}</span>}
              </th>
              {r.values.map((v, i) => (
                <td key={i} style={{ background: shade(v), color: v / max > 0.55 ? "var(--seq-ink-strong)" : "var(--text)" }}
                    title={`${r.label} · ${MONTHS[i]}: ${fmtMoney(v)}`}>
                  {v > 0 ? fmtCompact(v).replace("R$ ", "") : ""}
                </td>
              ))}
              <td className="right nowrap"><strong>{fmtCompact(r.total)}</strong></td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="hm-legend">
        <span className="muted small">menor</span>
        <span className="hm-ramp" />
        <span className="muted small">maior gasto no mês</span>
      </div>
    </div>
  );
}

/** Barras divergentes a partir de zero: aumentos à direita, reduções à esquerda. */
export function DivergingBars({ rows }: { rows: { label: string; sub?: string | null; delta: number; from: number; to: number }[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(1, ...rows.map((r) => Math.abs(r.delta)));
  return (
    <div className="diverging">
      {rows.map((r, i) => (
        <div key={r.label + i} className={`dv-row ${hover === i ? "hover" : ""}`} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
          <div className="paired-label" title={r.label}>{r.label}{r.sub && <span className="muted small mono"> · {r.sub}</span>}</div>
          <div className="dv-track">
            <div className="dv-axis" />
            <div className="dv-bar" style={{
              width: `${(Math.abs(r.delta) / max) * 50}%`,
              [r.delta >= 0 ? "left" : "right"]: "50%",
              background: r.delta >= 0 ? "var(--div-up)" : "var(--div-down)",
              borderRadius: r.delta >= 0 ? "0 4px 4px 0" : "4px 0 0 4px",
            }} />
          </div>
          <div className="paired-value"><span className={`delta ${r.delta >= 0 ? "up" : "down"}`}>{r.delta >= 0 ? "+" : ""}{fmtCompact(r.delta).replace("R$ -", "−R$ ")}</span></div>
          {hover === i && (
            <div className="tooltip tooltip-inline">
              <strong>{r.label}</strong>
              <span>Antes: {fmtMoney(r.from)}</span>
              <span>Agora: {fmtMoney(r.to)}</span>
              <span>Variação: {fmtMoney(r.delta)}{r.from ? ` (${fmtPct(String(r.delta / r.from))})` : ""}</span>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

/** Minigráfico de linha (12 meses). */
export function Sparkline({ values, width = 96, height = 26 }: { values: number[]; width?: number; height?: number }) {
  const last = values.reduce((acc, v, i) => (v ? i : acc), -1);
  if (last < 0) return <span className="muted small">—</span>;
  const max = Math.max(...values, 1);
  const pts = values.slice(0, last + 1).map((v, i) => [2 + (i / 11) * (width - 4), height - 3 - (v / max) * (height - 6)]);
  return (
    <svg width={width} height={height} className="sparkline" role="img" aria-label={`Mensal: ${values.map((v) => Math.round(v)).join(", ")}`}>
      <title>{values.map((v, i) => `${MONTHS[i]}: ${fmtMoney(v)}`).slice(0, last + 1).join("\n")}</title>
      <polyline points={pts.map((p) => p.join(",")).join(" ")} fill="none" stroke={SERIES.ref} strokeWidth={1.75} />
      <circle cx={pts[pts.length - 1][0]} cy={pts[pts.length - 1][1]} r={2.5} fill={SERIES.ref} />
    </svg>
  );
}

/** Barra empilhada de status (contagem), com legenda e rótulos. */
export function StatusBar({ items }: { items: { key: string; label: string; count: number; tone: string }[] }) {
  const total = items.reduce((s, i) => s + i.count, 0) || 1;
  return (
    <div className="statusbar-wrap">
      <div className="statusbar" role="img" aria-label={items.map((i) => `${i.label}: ${i.count}`).join(", ")}>
        {items.filter((i) => i.count > 0).map((i) => (
          <div key={i.key} className={`sb-seg sb-${i.tone}`} style={{ width: `${(i.count / total) * 100}%` }} title={`${i.label}: ${i.count}`}>
            {i.count / total > 0.08 ? i.count : ""}
          </div>
        ))}
      </div>
      <div className="legend">
        {items.map((i) => (
          <span key={i.key}><i className={`legend-swatch sb-${i.tone}`} />{i.label} · <strong>{i.count}</strong></span>
        ))}
      </div>
    </div>
  );
}

/** Ranking em barras horizontais de uma série, com participação no total. */
export function RankBars({ rows, color = SERIES.ref, limit = 10 }: { rows: { label: string; sub?: string | null; value: number }[]; color?: string; limit?: number }) {
  const [hover, setHover] = useState<number | null>(null);
  const total = rows.reduce((s, r) => s + Math.max(r.value, 0), 0) || 1;
  const shown = rows.slice(0, limit);
  const rest = rows.slice(limit).reduce((s, r) => s + r.value, 0);
  const all = rest > 0 ? [...shown, { label: `Outros (${rows.length - limit})`, value: rest }] : shown;
  const max = niceMax(Math.max(0, ...all.map((r) => r.value)));
  return (
    <div className="paired">
      {all.map((r, i) => (
        <div key={r.label} className={`paired-row ${hover === i ? "hover" : ""}`} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
          <div className="paired-label" title={r.label}>
            {r.label}
            {"sub" in r && r.sub && <span className="muted small mono"> · {r.sub}</span>}
          </div>
          <div className="paired-bars">
            <div className="paired-bar rank-bar" style={{ width: `${(Math.max(r.value, 0) / max) * 100}%`, background: color }} />
          </div>
          <div className="paired-value">
            {fmtCompact(r.value)}
            <span className="delta">{((r.value / total) * 100).toLocaleString("pt-BR", { maximumFractionDigits: 0 })}%</span>
          </div>
          {hover === i && (
            <div className="tooltip tooltip-inline">
              <strong>{r.label}</strong>
              <span><i style={{ background: color }} />{fmtMoney(r.value)} · {((r.value / total) * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}% do total</span>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
