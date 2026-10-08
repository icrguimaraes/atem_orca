import { useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

export interface FilterOption { value: string; label: string }
export interface FilterField {
  key: string;
  label: string;            // rótulo curto (Empresa, Centro de custo…)
  value: string;            // "" = sem filtro (a primeira opção)
  options: FilterOption[] | ((draft: Record<string, string>) => FilterOption[]);
  onChange: (value: string) => void;
  wide?: boolean;           // ocupa a linha inteira na grade do celular
  ariaLabel?: string;
}

/**
 * Filtros de página. No desktop: selects em linha (como antes). No celular: resumo em grade com rótulos
 * (um toque em qualquer campo abre a folha inferior com todos os filtros, "Limpar" e "Aplicar").
 * `onApply` recebe todos os valores de uma vez (evita setState em cascata); sem ele, aplica campo a campo.
 */
export function FilterBar({ fields, lead, extra, onApply, onReset, resetCount }: {
  fields: FilterField[];
  lead?: ReactNode;         // controles antes dos selects (ex.: botões de empresa no Painel)
  extra?: ReactNode;
  onApply?: (values: Record<string, string>) => void;
  onReset?: () => void;     // botão "Resetar filtros" (sempre visível; desabilitado sem filtro ativo)
  resetCount?: number;      // filtros ativos além dos selects (botões, busca…); sem ele, conta os selects
}) {
  const [open, setOpen] = useState(false);
  const current = Object.fromEntries(fields.map((f) => [f.key, f.value]));
  const [draft, setDraft] = useState<Record<string, string>>(current);
  useEffect(() => { if (open) setDraft(current); }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  const optionsOf = (f: FilterField, values: Record<string, string>) => (typeof f.options === "function" ? f.options(values) : f.options);
  const labelOf = (f: FilterField, values: Record<string, string>) => {
    const opts = optionsOf(f, values);
    return opts.find((o) => o.value === values[f.key])?.label ?? opts[0]?.label ?? "";
  };
  const active = fields.filter((f) => f.value !== "").length;
  const draftActive = fields.filter((f) => (draft[f.key] ?? "") !== "").length;
  const resetN = resetCount ?? active;
  const reset = onReset && (
    <button type="button" className="btn btn-ghost btn-sm filter-reset" disabled={!resetN} onClick={onReset}>
      Resetar filtros{resetN ? ` (${resetN})` : ""}
    </button>
  );

  function apply() {
    if (onApply) onApply(draft);
    else fields.forEach((f) => { if ((draft[f.key] ?? "") !== f.value) f.onChange(draft[f.key] ?? ""); });
    setOpen(false);
  }
  function setDraftValue(f: FilterField, value: string) {
    setDraft((d) => {
      const next = { ...d, [f.key]: value };
      // campos cuja opção atual deixou de existir (ex.: CC de outra empresa) voltam para "todos"
      for (const g of fields) {
        if (g.key !== f.key && next[g.key] && !optionsOf(g, next).some((o) => o.value === next[g.key])) next[g.key] = "";
      }
      return next;
    });
  }

  return (
    <>
      <div className="filters desktop-filters">
        {lead}
        {fields.map((f) => (
          <select key={f.key} value={f.value} onChange={(e) => f.onChange(e.target.value)} aria-label={f.ariaLabel ?? f.label}>
            {optionsOf(f, current).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        ))}
        {extra}
        {reset}
      </div>

      <div className="filter-summary" role="group" aria-label="Filtros">
        {lead && <div className="filter-extra">{lead}</div>}
        {fields.map((f) => (
          <button key={f.key} type="button" className={`filter-field ${f.wide ? "wide" : ""} ${f.value ? "on" : ""}`} onClick={() => setOpen(true)}>
            <span className="filter-label">{f.label}</span>
            <span className="filter-value"><span>{labelOf(f, current)}</span><span aria-hidden>▾</span></span>
          </button>
        ))}
        {extra && <div className="filter-extra">{extra}</div>}
        {reset && <div className="filter-extra">{reset}</div>}
      </div>

      {open && createPortal(
        <div className="sheet-backdrop" onClick={() => setOpen(false)}>
          <div className="sheet filter-sheet" role="dialog" aria-label="Filtros" onClick={(e) => e.stopPropagation()}>
            <div className="sheet-handle" aria-hidden />
            <h3>Filtros{active ? ` · ${active} ativo${active > 1 ? "s" : ""}` : ""}</h3>
            {fields.map((f) => (
              <label key={f.key} className="filter-sheet-field">
                {f.label}
                <select value={draft[f.key] ?? ""} onChange={(e) => setDraftValue(f, e.target.value)}>
                  {optionsOf(f, draft).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </label>
            ))}
            <div className="filter-sheet-actions">
              <button type="button" className="btn btn-ghost" onClick={() => setDraft(Object.fromEntries(fields.map((f) => [f.key, ""])))}>Limpar</button>
              <button type="button" className="btn btn-primary" onClick={apply}>Aplicar{draftActive ? ` (${draftActive})` : ""}</button>
            </div>
          </div>
        </div>,
        document.body,
      )}
    </>
  );
}
