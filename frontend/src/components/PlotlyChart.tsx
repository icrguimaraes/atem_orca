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
    const layout = merge({ ...figure.layout, height: height ?? (figure.layout.height as number | undefined) ?? 300 }, themeLayout());
    void Plotly.react(el, figure.data, layout, {
      responsive: true,
      displaylogo: false,
      locale: "pt-BR",
      modeBarButtonsToRemove: ["lasso2d", "select2d", "autoScale2d", "toggleSpikelines"],
      toImageButtonOptions: { format: "png", filename: "grafico-orcamento", scale: 2 },
    });
    const node = el as unknown as PlotlyHTMLElement;
    const handler = (ev: { points?: { customdata?: unknown; x?: unknown; y?: unknown }[] }) => {
      const p = ev.points?.[0];
      const horizontal = (figure.data[0] as { orientation?: string } | undefined)?.orientation === "h";
      if (p && onClick) onClick(p.customdata, String(horizontal ? p.y : p.x));
    };
    if (onClick) node.on("plotly_click", handler);
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onTheme = () => void Plotly.relayout(el, themeLayout() as Record<string, unknown>);
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
