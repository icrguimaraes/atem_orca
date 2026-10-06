import { useEffect, useRef } from "react";
import Plotly from "plotly.js-basic-dist-min";
import type { Data, Layout, PlotlyHTMLElement } from "plotly.js";

export interface Figure { data: Data[]; layout: Partial<Layout>; meta?: Record<string, string> }

/** Lê os tokens do tema (claro/escuro) para o Plotly acompanhar o design system. */
function themeLayout(): Partial<Layout> {
  const css = getComputedStyle(document.documentElement);
  const text = css.getPropertyValue("--text").trim() || "#222";
  const muted = css.getPropertyValue("--muted").trim() || "#717171";
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

/**
 * Renderiza uma figura Plotly gerada no backend (dados já agregados, rótulos pt-BR).
 * `onClick` recebe o customdata do ponto clicado (usado no drill-down).
 */
export function PlotlyChart({ figure, height, onClick, ariaLabel }: {
  figure: Figure; height?: number; onClick?: (customdata: unknown, label: string) => void; ariaLabel?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const touch = isTouchOrNarrow();
    const { data, horizontal, swapped } = touch ? adaptTraces(figure.data) : { data: figure.data, horizontal: false, swapped: false };
    let layout = merge({ ...figure.layout, height: height ?? (figure.layout.height as number | undefined) ?? 300 }, themeLayout());
    if (touch) {
      const m = mobileLayout(figure.layout, data, horizontal, swapped);
      layout = merge(layout, m);
      if (m.height) layout.height = m.height as number;
    }
    void Plotly.react(el, data, layout, {
      responsive: true,
      displaylogo: false,
      displayModeBar: touch ? false : "hover",
      scrollZoom: false,
      locale: "pt-BR",
      modeBarButtonsToRemove: ["lasso2d", "select2d", "autoScale2d", "toggleSpikelines", "zoom2d", "pan2d", "zoomIn2d", "zoomOut2d"],
      toImageButtonOptions: { format: "png", filename: "grafico-orcamento", scale: 2 },
    });
    const node = el as unknown as PlotlyHTMLElement;
    const handler = (ev: { points?: { customdata?: unknown; x?: unknown; y?: unknown }[] }) => {
      const p = ev.points?.[0];
      const horizontal = (data[0] as { orientation?: string } | undefined)?.orientation === "h";
      if (p && onClick) onClick(p.customdata, String(horizontal ? p.y : p.x));
    };
    if (onClick) node.on("plotly_click", handler);
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    // relayout com chaves pontilhadas: substituir `xaxis` inteiro apagaria tickvals/ticktext/grade do backend
    const onTheme = () => {
      const t = themeLayout() as Record<string, Record<string, unknown>>;
      const flat: Record<string, unknown> = {
        "font.color": t.font.color,
        "hoverlabel.bgcolor": t.hoverlabel.bgcolor,
        "hoverlabel.bordercolor": t.hoverlabel.bordercolor,
        "hoverlabel.font.color": (t.hoverlabel.font as Record<string, unknown>).color,
        "xaxis.tickfont.color": (t.xaxis.tickfont as Record<string, unknown>).color,
        "xaxis.linecolor": t.xaxis.linecolor,
        "yaxis.tickfont.color": (t.yaxis.tickfont as Record<string, unknown>).color,
        "yaxis.linecolor": t.yaxis.linecolor,
        "legend.font.color": (t.legend.font as Record<string, unknown>).color,
      };
      void Plotly.relayout(el, flat);
    };
    media.addEventListener("change", onTheme);
    const observer = new MutationObserver(onTheme); // troca de tema pelo app (atributo data-theme)
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => {
      media.removeEventListener("change", onTheme);
      observer.disconnect();
      if (onClick) node.removeAllListeners?.("plotly_click");
    };
  }, [figure, height, onClick]);
  useEffect(() => () => { if (ref.current) Plotly.purge(ref.current); }, []);
  return <div ref={ref} className="plotly-chart" role="img" aria-label={ariaLabel} style={{ cursor: onClick ? "pointer" : undefined }} />;
}
