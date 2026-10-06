import { useEffect, useRef, useState, type RefObject } from "react";
import { MONTHS, fmtCompact, fmtMoney, fmtPct } from "../labels";

/** Séries: ano de referência (slot 1), ano anterior (slot 2), orçamento (slot 3). Cores em styles.css. */
export const SERIES = {
  ref: "var(--series-1)",
  prev: "var(--series-2)",
  budget: "var(--series-3)",
  past: "var(--series-past)", // ano anterior: roxo, fora do azul (realizado) e do verde (orçamento)
};

/** Cor dos rótulos de cada série: tom mais escuro da cor da barra (no escuro, mais claro), legível em 11 px. */
const INK: Record<string, string> = {
  [SERIES.ref]: "var(--series-1-ink)",
  [SERIES.budget]: "var(--series-3-ink)",
  [SERIES.past]: "var(--series-past-ink)",
};
export const inkOf = (color: string) => INK[color] ?? color;

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

/** Rótulo curto para dentro do gráfico: "1,5 mi" em vez de "R$ 1,5 mi". */
function short(v: number): string {
  return fmtCompact(v).replace(/^R\$\s?/, "");
}

/** Comparativo mensal em colunas: ano anterior (cinza), ano em foco (azul) e orçado (verde-água), lado a lado.
 * Com espaço, cada barra recebe o próprio valor; senão, só máximo e mínimo de cada série. */
export function MonthlyChart({ rows, prevYear, refYear, showBudget, showRef = true, prevColor = SERIES.past, refLabel, prevLabel, budgetLabel }: {
  rows: MonthlyRow[]; prevYear: number | null; refYear: number; showBudget: boolean; showRef?: boolean; prevColor?: string;
  refLabel?: string; prevLabel?: string; budgetLabel?: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const [box, W] = useWidth();
  const H = 290, L = 84, R = 12, T = 26, B = 28;
  const plotW = W - L - R, plotH = H - T - B;
  type Key = "prev" | "ref" | "budget";
  const series: { key: Key; label: string; color: string }[] = [
    ...(prevYear !== null ? [{ key: "prev" as Key, label: prevLabel ?? String(prevYear), color: prevColor }] : []),
    ...(showRef ? [{ key: "ref" as Key, label: refLabel ?? String(refYear), color: SERIES.ref }] : []),
    ...(showBudget ? [{ key: "budget" as Key, label: budgetLabel ?? `Orçado ${refLabel ?? refYear}`, color: SERIES.budget }] : []),
  ];
  // degradê por série: a maior barra em cor cheia, as menores esmaecendo (mesmo padrão dos rankings)
  const ranks = series.map((s) => {
    const sorted = rows.map((r) => Number(r[s.key])).filter((v) => v > 0).sort((a, b) => b - a);
    return (v: number) => (sorted.length > 1 ? sorted.indexOf(v) / (sorted.length - 1) : 0);
  });
  const shade = (color: string, j: number, v: number) => `color-mix(in srgb, ${color} ${Math.round(100 - ranks[j](v) * 55)}%, transparent)`;
  const values = rows.flatMap((r) => series.map((s) => Number(r[s.key])));
  const max = niceMax(Math.max(...values, 0));
  const y = (v: number) => T + plotH - (Math.max(v, 0) / max) * plotH;
  const slot = plotW / 12;
  const n = series.length;
  const barW = Math.max(6, Math.min(n === 1 ? 44 : n === 2 ? 24 : 18, (slot - 12 - 3 * (n - 1)) / n));
  const labelAll = slot - 6 >= n * 40; // um rótulo de ~40 px por barra
  // com rótulo em todas as barras, os centros das barras vizinhas ficam a 40 px para os textos não encostarem
  const gap = labelAll && n > 1 ? Math.min(Math.max(3, 40 - barW), (slot - 8 - barW * n) / (n - 1)) : 3;
  const groupW = barW * n + gap * (n - 1);
  const xOf = (i: number, j: number) => L + slot * i + slot / 2 - groupW / 2 + j * (barW + gap);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => t * max);
  const h = hover !== null ? rows[hover] : null;

  return (
    <div className="chart" ref={box}>
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label={`Comparativo mensal ${series.map((s) => s.label).join(" e ")}`} onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} className="grid" />
            <text x={L - 8} y={y(t) + 4} className="axis" textAnchor="end">{fmtCompact(t)}</text>
          </g>
        ))}
        {rows.map((r, i) => (
          <g key={r.month}>
            {hover === i && <rect x={L + slot * i} y={T} width={slot} height={plotH} className="hover-band" />}
            {series.map((s, j) => {
              const v = Number(r[s.key]);
              if (v <= 0) return null;
              return (
                <g key={s.key}>
                  <path d={roundedTop(xOf(i, j), y(v), barW, T + plotH - y(v))} style={{ fill: shade(s.color, j, v) }} />
                  {labelAll && (
                    <text x={xOf(i, j) + barW / 2} y={y(v) - 6} className="extreme" textAnchor="middle" fill={inkOf(s.color)} pointerEvents="none">
                      {short(v)}
                    </text>
                  )}
                </g>
              );
            })}
            <text x={L + slot * i + slot / 2} y={H - 8} className="axis" textAnchor="middle">{MONTHS[r.month - 1]}</text>
            <rect x={L + slot * i} y={T} width={slot} height={plotH} fill="transparent" onMouseEnter={() => setHover(i)} />
          </g>
        ))}
        {!labelAll &&
          series.map((s, j) => (
            <ExtremeLabels key={s.key} values={rows.map((r) => Number(r[s.key]))} x={(i) => xOf(i, j) + barW / 2} y={y} color={s.color} format={short} />
          ))}
        <line x1={L} x2={W - R} y1={T + plotH} y2={T + plotH} className="baseline" />
      </svg>
      {h && hover !== null && (
        <div className="tooltip" style={{ left: `${((L + slot * hover + slot / 2) / W) * 100}%` }}>
          <strong>{MONTHS[h.month - 1]}</strong>
          {series.map((s) => (
            <span key={s.key}><i style={{ background: s.color }} />{s.label}: {fmtMoney(h[s.key])}</span>
          ))}
          {prevYear !== null && Number(h.prev) > 0 && Number(h.ref) > 0 && <span className="muted">Variação: {fmtPct(String((Number(h.ref) - Number(h.prev)) / Number(h.prev)))}</span>}
          {showBudget && Number(h.budget) > 0 && Number(h.ref) > 0 && <span className="muted">Realizado vs orçado: {fmtPct(String((Number(h.ref) - Number(h.budget)) / Number(h.budget)))}</span>}
        </div>
      )}
    </div>
  );
}

/** Total acumulado mês a mês: ano em foco (azul, linha com marcadores) sobre a base (orçado em verde-água
 * ou ano anterior em cinza, área). A série do ano em foco para no último mês com realizado. */
export function CumulativeChart({ rows, prevYear, refYear, showBudget, prevColor = SERIES.past, refLabel, prevLabel, budgetLabel }: {
  rows: MonthlyRow[]; prevYear: number | null; refYear: number; showBudget: boolean; prevColor?: string; refLabel?: string;
  prevLabel?: string; budgetLabel?: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const [box, W] = useWidth();
  const H = 300, L = 84, R = 24, T = 30, B = 28;
  const plotW = W - L - R, plotH = H - T - B;
  const cum = (key: "prev" | "ref" | "budget") => rows.reduce<number[]>((acc, r) => [...acc, (acc[acc.length - 1] ?? 0) + Number(r[key])], []);
  const ref = cum("ref");
  const lastRef = rows.reduce((last, r, i) => (Number(r.ref) > 0 ? i : last), -1);
  const baseKey = showBudget ? "budget" : prevYear !== null ? "prev" : null;
  const base = baseKey ? cum(baseKey) : null;
  const baseColor = baseKey === "budget" ? SERIES.budget : prevColor;
  const baseLabel = baseKey === "budget" ? (budgetLabel ?? `Orçado ${refLabel ?? refYear}`) : (prevLabel ?? String(prevYear));
  const curLabel = refLabel ?? String(refYear);
  const max = niceMax(Math.max(ref[lastRef] ?? 0, ...(base ?? [0]), 0));
  const y = (v: number) => T + plotH - (Math.max(v, 0) / max) * plotH;
  const slot = plotW / 12;
  const x = (i: number) => L + slot * i + slot / 2;
  const labelAll = slot >= 54;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => t * max);
  const refPts = ref.slice(0, lastRef + 1);
  const area = (pts: number[]) => `${x(0)},${y(0)} ${pts.map((v, i) => `${x(i)},${y(v)}`).join(" ")} ${x(pts.length - 1)},${y(0)}`;

  if (lastRef < 0 && !base) return <div className="chart muted small">Sem dados.</div>;
  return (
    <div className="chart" ref={box}>
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label={`Total acumulado ${curLabel}${base ? ` e ${baseLabel}` : ""}`} onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} className="grid" />
            <text x={L - 8} y={y(t) + 4} className="axis" textAnchor="end">{fmtCompact(t)}</text>
          </g>
        ))}
        {base && <polygon points={area(base)} fill={baseColor} opacity={0.16} pointerEvents="none" />}
        {base && <polyline points={base.map((v, i) => `${x(i)},${y(v)}`).join(" ")} fill="none" stroke={baseColor} strokeWidth={2} pointerEvents="none" />}
        {refPts.length > 0 && <polygon points={area(refPts)} fill={SERIES.ref} opacity={0.14} pointerEvents="none" />}
        {refPts.length > 0 && <polyline points={refPts.map((v, i) => `${x(i)},${y(v)}`).join(" ")} fill="none" stroke={SERIES.ref} strokeWidth={2.5} strokeDasharray="5 4" pointerEvents="none" />}
        {base && base.map((v, i) => <circle key={`b${i}`} cx={x(i)} cy={y(v)} r={3.5} fill={baseColor} className="ring" pointerEvents="none" />)}
        {refPts.map((v, i) => <circle key={`r${i}`} cx={x(i)} cy={y(v)} r={4} fill={SERIES.ref} className="ring" pointerEvents="none" />)}
        {/* rótulos: a série mais alta no mês recebe o valor acima do ponto e a mais baixa abaixo, sem sobreposição */}
        {base &&
          base.map((v, i) =>
            labelAll || i === base.length - 1 || i === 0 ? (
              <text key={`bl${i}`} x={x(i)} y={i <= lastRef && ref[i] > v ? y(v) + 17 : y(v) - 9} className="extreme" textAnchor="middle" fill={inkOf(baseColor)} pointerEvents="none">{short(v)}</text>
            ) : null,
          )}
        {refPts.map((v, i) =>
          labelAll || i === refPts.length - 1 || i === 0 ? (
            <text key={`rl${i}`} x={x(i)} y={base && base[i] > v ? y(v) + 17 : y(v) - 9} className="extreme" textAnchor="middle" fill={inkOf(SERIES.ref)} pointerEvents="none">{short(v)}</text>
          ) : null,
        )}
        {rows.map((r, i) => (
          <g key={r.month}>
            {hover === i && <rect x={L + slot * i} y={T} width={slot} height={plotH} className="hover-band" />}
            <text x={x(i)} y={H - 8} className="axis" textAnchor="middle">{MONTHS[r.month - 1]}</text>
            <rect x={L + slot * i} y={T} width={slot} height={plotH} fill="transparent" onMouseEnter={() => setHover(i)} />
          </g>
        ))}
        <line x1={L} x2={W - R} y1={T + plotH} y2={T + plotH} className="baseline" />
      </svg>
      {hover !== null && (
        <div className="tooltip" style={{ left: `${(x(hover) / W) * 100}%` }}>
          <strong>Acumulado até {MONTHS[hover]}</strong>
          {hover <= lastRef && <span><i style={{ background: SERIES.ref }} />{curLabel}: {fmtMoney(ref[hover])}</span>}
          {base && <span><i style={{ background: baseColor }} />{baseLabel}: {fmtMoney(base[hover])}</span>}
          {base && hover <= lastRef && base[hover] > 0 && <span className="muted">Diferença: {fmtMoney(ref[hover] - base[hover])} ({fmtPct(String((ref[hover] - base[hover]) / base[hover]))})</span>}
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
        <text key={e.kind} x={x(e.i) + dx} y={y(e.v) - 7} className="extreme" textAnchor="middle" fill={inkOf(color)} pointerEvents="none">
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
export interface RankRow { label: string; sub?: string | null; value: number; prev?: number; note?: string }

/** Ranking em barras horizontais, do maior para o menor, com degradê no tom da série
 * (a maior barra em cor cheia, as menores esmaecendo em direção ao fundo). */
export function TopBars({ rows, label, prevLabel, prevColor = SERIES.past, color = SERIES.ref }: {
  rows: RankRow[]; label: string; prevLabel?: string | null; prevColor?: string; color?: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  // sem eixo, a maior barra ocupa a trilha inteira (niceMax deixaria todas curtas)
  const max = Math.max(...rows.flatMap((r) => [r.value, r.prev ?? 0]), 0) || 1;
  const tone = (i: number) => `color-mix(in srgb, ${color} ${Math.round(100 - (i / Math.max(rows.length - 1, 1)) * 60)}%, transparent)`;
  return (
    <div className="paired" role="list" aria-label={label}>
      {rows.map((r, i) => (
        <div key={`${r.label}-${i}`} role="listitem" className={`paired-row ${hover === i ? "hover" : ""}`} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
          <div className="paired-label" title={r.label}>
            {r.label}
            {r.sub && <span className="muted small mono"> {r.sub}</span>}
          </div>
          <div className="paired-bars">
            <div className="top-bar" style={{ width: `${(Math.max(r.value, 0) / max) * 100}%`, background: tone(i) }} />
            {prevLabel && r.prev !== undefined && <div className="top-bar-prev" style={{ width: `${(Math.max(r.prev, 0) / max) * 100}%`, background: prevColor }} />}
          </div>
          <div className="paired-value">
            <strong>{fmtCompact(r.value)}</strong>
            {r.note && <span className={`delta ${r.note.startsWith("+") ? "up" : r.note.startsWith("-") ? "down" : ""}`}>{r.note}</span>}
          </div>
          {hover === i && (
            <div className="tooltip tooltip-inline">
              <strong>{r.label}</strong>
              <span><i style={{ background: color }} />{label}: {fmtMoney(r.value)}</span>
              {prevLabel && r.prev !== undefined && <span><i style={{ background: prevColor }} />{prevLabel}: {fmtMoney(r.prev)}</span>}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

export function PairedBars({ rows, prevLabel, refLabel, prevColor = SERIES.past }: { rows: CompareRow[]; prevLabel: string; refLabel: string; prevColor?: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(...rows.flatMap((r) => [r.prev, r.ref]), 0) || 1; // a maior barra ocupa a trilha inteira
  return (
    <div className="paired">
      {rows.map((r, i) => (
        <div key={r.label} className={`paired-row ${hover === i ? "hover" : ""}`} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
          <div className="paired-label" title={r.label}>{r.label}</div>
          <div className="paired-bars">
            {prevLabel && <div className="paired-bar" style={{ width: `${(Math.max(r.prev, 0) / max) * 100}%`, background: prevColor }} />}
            <div className="paired-bar" style={{ width: `${(Math.max(r.ref, 0) / max) * 100}%`, background: SERIES.ref }} />
          </div>
          <div className="paired-value">
            {fmtCompact(r.ref)}
            {r.note && <span className={`delta ${r.note.startsWith("+") ? "up" : r.note.startsWith("-") ? "down" : ""}`}>{r.note}</span>}
          </div>
          {hover === i && (
            <div className="tooltip tooltip-inline">
              <strong>{r.label}</strong>
              {prevLabel && <span><i style={{ background: prevColor }} />{prevLabel}: {fmtMoney(r.prev)}</span>}
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
  const color = { "total-prev": SERIES.past, "total-ref": SERIES.ref, up: "var(--div-up)", down: "var(--div-down)" };
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
export function Heatmap({ rows, budget = false }: { rows: { label: string; sub?: string | null; values: number[]; total: number }[]; budget?: boolean }) {
  const max = Math.max(1, ...rows.flatMap((r) => r.values));
  const [lo, hi] = budget ? ["var(--seqg-lo)", "var(--seqg-hi)"] : ["var(--seq-lo)", "var(--seq-hi)"]; // verde = orçamento
  const shade = (v: number) =>
    v <= 0 ? "var(--surface-2)" : `color-mix(in oklab, ${hi} ${Math.round(12 + (v / max) * 88)}%, ${lo})`;
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
export function DivergingBars({ rows, fromLabel = "Antes", toLabel = "Agora" }: { rows: { label: string; sub?: string | null; delta: number; from: number; to: number }[]; fromLabel?: string; toLabel?: string }) {
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
              <span>{fromLabel}: {fmtMoney(r.from)}</span>
              <span>{toLabel}: {fmtMoney(r.to)}</span>
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
