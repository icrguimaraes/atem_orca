import { useState } from "react";
import { api } from "../api";
import { PlotlyChart, type Figure } from "./PlotlyChart";
import { Alert, Card, Loading, useLoad } from "./ui";

type EvolutionOption = { key: string; label: string; kind: string; value: string };
type Evolution = { options: EvolutionOption[]; selected: string[]; figure: Figure | null };

/** Evolução do orçamento (cascata), na Análise orçamentária desde 08/10/2026: os marcos a comparar são escolhidos no
 *  próprio visual (botões); a figura vem de /dashboard/evolution com os filtros da página (ano e mês não se aplicam). */
export function EvolutionCard({ query }: { query: string }) {
  const [marks, setMarks] = useState<string[] | null>(null); // null = marcos padrão
  const { data, error } = useLoad(
    () => api<Evolution>(`/dashboard/evolution?${marks ? `marks=${encodeURIComponent(marks.join(","))}` : ""}${query ? `&${query}` : ""}`),
    [marks?.join(","), query],
  );
  const options = data?.options ?? [];
  const selected = marks ?? data?.selected ?? [];
  const fig = data?.figure;
  const meta = (fig?.meta ?? {}) as Record<string, string | undefined>;
  function toggle(key: string) {
    const next = selected.includes(key) ? selected.filter((k) => k !== key) : [...selected, key];
    if (next.length < 2) return; // a cascata precisa de dois marcos
    setMarks(options.map((o) => o.key).filter((k) => next.includes(k)));
  }
  return (
    <Card
      title="Evolução do orçamento"
      actions={marks ? <button type="button" className="btn btn-ghost btn-sm" onClick={() => setMarks(null)}>Voltar ao padrão</button> : undefined}
    >
      {error && <Alert>{error}</Alert>}
      {options.length > 2 && (
        <div className="chip-group evolution-marks">
          <span className="chip-label">Comparar</span>
          <div className="month-chips" role="group" aria-label="Marcos da evolução">
            {options.map((o) => {
              const on = selected.includes(o.key);
              return (
                <button
                  key={o.key}
                  type="button"
                  className={on ? "active" : ""}
                  aria-pressed={on}
                  disabled={on && selected.length <= 2}
                  title={on && selected.length <= 2 ? "Mínimo de dois marcos" : undefined}
                  onClick={() => toggle(o.key)}
                >
                  {o.label}
                </button>
              );
            })}
          </div>
        </div>
      )}
      {!fig ? (
        data ? <p className="muted small">Sem marcos com valor para estes filtros.</p> : <Loading />
      ) : (
        <>
          <p className="muted small evolution-note">
            De <strong>{meta.first}</strong> a <strong>{meta.last}</strong>: os filtros de ano e mês não se aplicam (escolha os marcos acima); empresa, área, centro de custo, pacote e tipo valem.
            {" "}Variação total:{" "}
            <strong className={meta.total_diff?.startsWith("-") ? "down" : meta.total_diff?.startsWith("+") ? "up" : ""}>
              {meta.total_pct} ({meta.total_diff})
            </strong>.
          </p>
          <PlotlyChart figure={fig} height={400} ariaLabel="Evolução do orçamento" />
        </>
      )}
    </Card>
  );
}
