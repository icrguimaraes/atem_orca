import { useEffect, useLayoutEffect, useRef, useState } from "react";
import Plotly from "plotly.js-basic-dist-min";
import type { Data, Layout, PlotlyHTMLElement } from "plotly.js";

export interface Figure { data: Data[]; layout: Partial<Layout>; meta?: Record<string, string> }

/** Lê os tokens do tema (claro/escuro) para o Plotly acompanhar o design system. */
function themeLayout(): Partial<Layout> {
  const css = getComputedStyle(document.documentElement);
  const text = css.getPropertyValue("--text").trim() || "#222";
  const muted = css.getPropertyValue("--axis").trim() || css.getPropertyValue("--muted").trim() || "#4a4a4a";
  const surface = css.getPropertyValue("--surface").trim() || "#fff";
  const border = css.getPropertyValue("--border").trim() || "#ddd";
  return {
    font: { color: text, family: "DM Sans, system-ui, sans-serif", size: 12 },
    hoverlabel: { bgcolor: surface, bordercolor: border, font: { color: text, family: "DM Sans, system-ui, sans-serif" } },
    xaxis: { tickfont: { color: muted }, linecolor: border },
    yaxis: { tickfont: { color: muted }, linecolor: border },
    legend: { font: { color: muted } },
  };
}

/** Tela de toque ou estreita: sem modebar (não há hover; ela ficaria fixa sobre o gráfico), sem arrastar
 *  (o dedo precisa rolar a página, não dar zoom) e com fontes/margens menores. */
function isTouchOrNarrow(): boolean {
  return window.matchMedia("(hover: none), (max-width: 860px)").matches;
}

type Trace = Record<string, unknown> & { type?: string; orientation?: string; x?: unknown[]; y?: unknown[]; text?: unknown };

/** O backend escreve os rótulos com o tom escuro de cada série (tema claro); aqui eles viram o token do tema
 *  atual, para continuarem legíveis no escuro. */
const INK_TOKENS: Record<string, string> = {
  "#3a64b4": "--series-1-ink", "#2b7a68": "--series-3-ink", "#6a4c9c": "--series-past-ink", "#7a6524": "--series-var-ink",
};

function themeInks(data: Data[]): Data[] {
  const css = getComputedStyle(document.documentElement);
  const map = (c: unknown) => {
    const token = typeof c === "string" ? INK_TOKENS[c.toLowerCase()] : undefined;
    return token ? css.getPropertyValue(token).trim() || c : c;
  };
  return (data as Trace[]).map((t) => {
    const font = t.textfont as { color?: unknown } | undefined;
    if (!font?.color) return t;
    return { ...t, textfont: { ...font, color: Array.isArray(font.color) ? font.color.map(map) : map(font.color) } };
  }) as Data[];
}

function isBar(t: Trace) {
  return t.type === "bar";
}

/** Adaptações dos dados para o celular:
 *  - barras verticais com categorias em texto (ex.: comparativo por CC) viram horizontais, com o nome à esquerda
 *    e o valor no fim da barra (categorias longas não cabem no eixo x de 390px);
 *  - barras horizontais divergentes (variação) passam a crescer todas para a direita, em valor absoluto: a cor
 *    e o sinal no texto continuam dizendo se é aumento ou redução, e o texto nunca invade os nomes à esquerda. */
function adaptTraces(data: Data[]): { data: Data[]; horizontal: boolean; swapped: boolean } {
  const traces = data as Trace[];
  if (traces.length === 0 || !traces.every(isBar)) return { data, horizontal: false, swapped: false };
  const vertical = traces.every((t) => t.orientation !== "h" && typeof t.x?.[0] === "string");
  let out: Trace[] = traces;
  if (vertical) {
    out = traces.map((t) => ({ ...t, x: t.y, y: t.x, orientation: "h", textposition: "outside", cliponaxis: false }));
  }
  if (!out.every((t) => t.orientation === "h")) return { data, horizontal: false, swapped: false };
  const hasNeg = out.some((t) => (t.x as unknown[]).some((v) => Number(v) < 0));
  if (hasNeg) out = out.map((t) => ({ ...t, x: (t.x as unknown[]).map((v) => Math.abs(Number(v))) }));
  return { data: out as Data[], horizontal: true, swapped: vertical };
}

function shortLabel(text: string, max = 22): string {
  return text.length > max ? text.slice(0, max - 1).trimEnd() + "…" : text;
}

function mobileLayout(base: Partial<Layout>, data: Data[], horizontal: boolean, swapped: boolean): Partial<Layout> {
  const traces = data as Trace[];
  const margin = { ...(base.margin as Record<string, number> | undefined) };
  if (margin.r && margin.r > 56) margin.r = 56;
  if (margin.l && margin.l > 8) margin.l = 8;
  const out: Record<string, unknown> = {
    margin,
    dragmode: false,
    font: { size: 11 },
    legend: { font: { size: 11 } },
    xaxis: { nticks: 4, automargin: true, tickangle: 0 },
    yaxis: { automargin: true, tickangle: 0 },
  };
  if (!horizontal) return out as Partial<Layout>;
  // valores já estão escritos no fim das barras: eixo de valor só com a grade, e folga para o texto caber
  const values = traces.flatMap((t) => (t.x as unknown[]) ?? []).map(Number).filter((v) => !Number.isNaN(v));
  const maxAbs = Math.max(0, ...values.map(Math.abs));
  const categories = [...new Set(traces.flatMap((t) => (t.y as unknown[]) ?? []).map(String))];
  out.xaxis = {
    showticklabels: false, showgrid: true, zeroline: false, automargin: true, title: undefined,
    range: [0, maxAbs * 1.9], tickvals: undefined, ticktext: undefined,
  };
  out.yaxis = {
    automargin: true, tickangle: 0, title: undefined, tickfont: { size: 11 },
    autorange: swapped ? "reversed" : ((base.yaxis as { autorange?: unknown } | undefined)?.autorange ?? true),
    tickvals: categories, ticktext: categories.map((c) => shortLabel(c)),
  };
  const rows = categories.length;
  const perRow = traces.length > 1 ? 16 * traces.length + 10 : 26;
  out.height = Math.max(Number(base.height) || 0, perRow * rows + 90);
  return out as Partial<Layout>;
}

function merge(base: Partial<Layout>, theme: Partial<Layout>): Partial<Layout> {
  const out: Record<string, unknown> = { ...base };
  for (const [k, v] of Object.entries(theme)) {
    const cur = out[k];
    out[k] = cur && typeof cur === "object" && v && typeof v === "object" ? { ...(cur as object), ...(v as object) } : v;
  }
  return out as Partial<Layout>;
}

interface HoverPoint { customdata?: unknown; x?: unknown; y?: unknown }
interface Tip { left: number; top: number; title: string; lines: [string, string][] }

/** Linhas do tooltip próprio: o backend põe, no último item do customdata de cada ponto, a lista
 *  [[texto, cor], …] (cor vazia = linha derivada, como a variação, que vai para o fim). */
function tipLines(points: HoverPoint[]): [string, string][] {
  const seen = new Set<string>();
  const lines: [string, string][] = [];
  for (const p of points) {
    const cd = p.customdata;
    const last = Array.isArray(cd) ? cd[cd.length - 1] : null;
    if (!Array.isArray(last)) continue;
    for (const l of last as unknown[]) {
      if (Array.isArray(l) && !seen.has(String(l[0]))) {
        seen.add(String(l[0]));
        lines.push([String(l[0]), String(l[1] ?? "")]);
      }
    }
  }
  return lines.sort((a, b) => Number(!a[1]) - Number(!b[1]));
}

/**
 * Renderiza uma figura Plotly gerada no backend (dados já agregados, rótulos pt-BR).
 * `onClick` recebe o customdata do ponto clicado (drill-down ou filtro). Figuras com `meta.tooltip`
 * ("unified": todas as séries do mês; "point": a barra sob o mouse) usam o tooltip do design system no lugar
 * do balão do Plotly.
 */
export function PlotlyChart({ figure, height, onClick, ariaLabel }: {
  figure: Figure; height?: number; onClick?: (customdata: unknown, label: string) => void; ariaLabel?: string;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const ref = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<Tip | null>(null);
  const tipRef = useRef<HTMLDivElement>(null);
  const [tipPos, setTipPos] = useState<{ left: number; top: number } | null>(null);
  // posição final medida antes de pintar: à direita do mouse; perto da borda, à esquerda; sempre dentro do gráfico
  useLayoutEffect(() => {
    const box = wrap.current;
    const el = tipRef.current;
    if (!tip || !box || !el) {
      setTipPos(null);
      return;
    }
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    const width = box.clientWidth;
    let left = tip.left + 14;
    if (left + w > width) left = tip.left - 14 - w;
    left = Math.max(0, Math.min(left, width - w));
    setTipPos({ left, top: Math.max(-4, tip.top - h / 2) });
  }, [tip]);
  const mode = figure.meta?.tooltip as "unified" | "point" | undefined;
  // eixo numérico (ex.: cascata da evolução): o título do tooltip vem no primeiro item do customdata
  const titleFromData = figure.meta?.tooltip_title === "customdata";
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const touch = isTouchOrNarrow();
    const adapted = touch ? adaptTraces(figure.data) : { data: figure.data, horizontal: false, swapped: false };
    const { horizontal, swapped } = adapted;
    // tooltip próprio: o Plotly só emite os eventos de hover, sem desenhar o balão
    const data = mode ? ((adapted.data as Trace[]).map((t) => ({ ...t, hoverinfo: "none", hovertemplate: undefined })) as Data[]) : adapted.data;
    const barsH = (data[0] as { orientation?: string } | undefined)?.orientation === "h";
    // desenha com as cores do tema atual; roda de novo quando o tema muda (claro/escuro)
    const render = () => {
      let layout = merge({ ...figure.layout, height: height ?? (figure.layout.height as number | undefined) ?? 300 }, themeLayout());
      if (touch) {
        const m = mobileLayout(figure.layout, data, horizontal, swapped);
        layout = merge(layout, m);
        if (m.height) layout.height = m.height as number;
      }
      if (mode) layout = { ...layout, hovermode: mode === "unified" ? (barsH ? "y" : "x") : "closest" };
      // rankings horizontais: em gráfico largo o valor vai na margem direita e as barras usam a largura toda;
      // em gráfico estreito (card ao lado de outro) o valor vem logo depois da barra
      if (mode === "point" && barsH && !touch && el.clientWidth < 520) {
        const top = Math.max(0, ...(data as Trace[]).flatMap((t) => ((t.x as unknown[]) ?? []).map(Number)));
        layout = merge(layout, { margin: { r: 12 } as Layout["margin"], xaxis: { range: [0, (top || 1) * 1.8] } });
      }
      void Plotly.react(el, themeInks(data), layout, {
        responsive: true,
        displaylogo: false,
        displayModeBar: touch ? false : "hover",
        scrollZoom: false,
        locale: "pt-BR",
        modeBarButtonsToRemove: ["lasso2d", "select2d", "autoScale2d", "toggleSpikelines", "zoom2d", "pan2d", "zoomIn2d", "zoomOut2d"],
        toImageButtonOptions: { format: "png", filename: "grafico-orcamento", scale: 2 },
      });
    };
    render();
    const node = el as unknown as PlotlyHTMLElement;
    const handler = (ev: { points?: HoverPoint[] }) => {
      const p = ev.points?.[0];
      if (p && onClick) onClick(p.customdata, String(barsH ? p.y : p.x));
    };
    if (onClick) node.on("plotly_click", handler);
    const onHover = (ev: { points?: HoverPoint[]; event?: MouseEvent }) => {
      const points = ev.points ?? [];
      const box = wrap.current?.getBoundingClientRect();
      if (!points.length || !box) return;
      const first = points[0];
      const lines = tipLines(mode === "point" ? [first] : points);
      if (!lines.length) {
        setTip(null);
        return;
      }
      const left = (ev.event?.clientX ?? box.left + box.width / 2) - box.left;
      const top = (ev.event?.clientY ?? box.top + 24) - box.top;
      const title = titleFromData && Array.isArray(first.customdata) ? String(first.customdata[0]) : String(barsH ? first.y : first.x);
      setTip({ left, top, title, lines });
    };
    const onUnhover = () => setTip(null);
    if (mode) {
      node.on("plotly_hover", onHover);
      node.on("plotly_unhover", onUnhover);
    }
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    // o merge preserva tickvals/ticktext/grade do backend: só as cores do tema mudam
    const onTheme = () => render();
    media.addEventListener("change", onTheme);
    const observer = new MutationObserver(onTheme); // troca de tema pelo app (atributo data-theme)
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => {
      media.removeEventListener("change", onTheme);
      observer.disconnect();
      if (onClick) node.removeAllListeners?.("plotly_click");
      if (mode) {
        node.removeAllListeners?.("plotly_hover");
        node.removeAllListeners?.("plotly_unhover");
      }
      setTip(null);
    };
  }, [figure, height, onClick, mode, titleFromData]);
  useEffect(() => () => { if (ref.current) Plotly.purge(ref.current); }, []);
  return (
    <div className="plotly-wrap" ref={wrap} onMouseLeave={() => setTip(null)}>
      <div ref={ref} className="plotly-chart" role="img" aria-label={ariaLabel} style={{ cursor: onClick ? "pointer" : undefined }} />
      {tip && (
        <div
          ref={tipRef}
          className="tooltip"
          style={{ left: tipPos?.left ?? tip.left, top: tipPos?.top ?? tip.top, transform: "none", visibility: tipPos ? "visible" : "hidden" }}
        >
          <strong>{tip.title}</strong>
          {tip.lines.map(([text, color]) => (
            <span key={text}>
              {color && <i style={{ background: color }} />}
              {text}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
