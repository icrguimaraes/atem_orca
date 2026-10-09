import { useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { Alert, Card, useLoad } from "./ui";

type Criterion = { item: string; texto: string };
type Criteria = { target_year: number | null; items: Criterion[] };

/** Critérios do orçamento da versão (pedido da gestora, 09/10/2026): resumo só informativo, para quem lê os números
 *  saber o que está e o que não está considerado. Fica no Painel; a Controladoria edita aqui mesmo (auditado). */
export function CriteriaCard() {
  const { can } = useAuth();
  const { data, error, reload } = useLoad(() => api<Criteria>("/dashboard/criteria"));
  const [draft, setDraft] = useState<Criterion[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  if (!data || (!data.items.length && !can("CONTROLLER"))) return error ? <Alert>{error}</Alert> : null;

  async function save() {
    setBusy(true);
    setSaveError(null);
    try {
      await api("/dashboard/criteria", { method: "PUT", body: JSON.stringify({ items: draft }) });
      setDraft(null);
      reload();
    } catch (e) {
      setSaveError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const set = (i: number, patch: Partial<Criterion>) => setDraft((cur) => (cur ?? []).map((c, j) => (j === i ? { ...c, ...patch } : c)));

  return (
    <Card
      title={`Critérios do orçamento${data.target_year ? ` ${data.target_year}` : ""}`}
      actions={
        can("CONTROLLER") && !draft ? (
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setDraft(data.items.map((c) => ({ ...c })))}>Editar</button>
        ) : undefined
      }
    >
      <p className="muted small">Só para informação: o que esta versão considera em cada tema.</p>
      {!draft ? (
        data.items.length ? (
          <dl className="criteria-list">
            {data.items.map((c, i) => (
              <div key={i} className="criteria-row">
                <dt>{c.item}</dt>
                <dd>{c.texto}</dd>
              </div>
            ))}
          </dl>
        ) : (
          <p className="muted">Nenhum critério cadastrado.</p>
        )
      ) : (
        <div className="stack">
          {saveError && <Alert>{saveError}</Alert>}
          {draft.map((c, i) => (
            <div key={i} className="criteria-edit">
              <input value={c.item} maxLength={80} placeholder="Tema (ex.: Passagens)" aria-label="Tema" onChange={(e) => set(i, { item: e.target.value })} />
              <textarea value={c.texto} maxLength={600} rows={2} placeholder="Critério considerado" aria-label="Critério" onChange={(e) => set(i, { texto: e.target.value })} />
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setDraft(draft.filter((_, j) => j !== i))}>Remover</button>
            </div>
          ))}
          <div className="inline-controls">
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setDraft([...draft, { item: "", texto: "" }])}>Adicionar critério</button>
            <span style={{ flex: 1 }} />
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setDraft(null)}>Cancelar</button>
            <button type="button" className="btn btn-primary btn-sm" disabled={busy} onClick={save}>{busy ? "Salvando…" : "Salvar"}</button>
          </div>
        </div>
      )}
    </Card>
  );
}
