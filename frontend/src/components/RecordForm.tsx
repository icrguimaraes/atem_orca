import { useState, type FormEvent } from "react";
import { api } from "../api";
import { Alert, Modal } from "./ui";

export type Field =
  | { key: string; label: string; type: "text" | "number"; required?: boolean; readOnlyOnEdit?: boolean; hint?: string }
  | { key: string; label: string; type: "select"; options: { value: string | number; label: string }[]; required?: boolean; readOnlyOnEdit?: boolean; allowEmpty?: boolean }
  | { key: string; label: string; type: "checkbox" };

/** Formulário genérico de cadastro: cria (POST) ou altera (PATCH só dos campos mudados). */
export function RecordForm({
  title,
  endpoint,
  id,
  initial: initialRecord,
  fields,
  onClose,
  onSaved,
}: {
  title: string;
  endpoint: string;
  id?: string | number;
  initial: object;
  fields: Field[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const initial = initialRecord as Record<string, unknown>;
  const [values, setValues] = useState<Record<string, unknown>>(initial);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const editing = id !== undefined;

  function set(key: string, value: unknown) {
    setValues((v) => ({ ...v, [key]: value }));
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const payload: Record<string, unknown> = {};
    for (const f of fields) {
      const v = values[f.key];
      const normalized = v === "" ? null : f.type === "number" && v !== null && v !== undefined ? Number(v) : v;
      if (!editing || normalized !== initial[f.key]) payload[f.key] = normalized;
    }
    try {
      if (editing && Object.keys(payload).length === 0) return onClose();
      await api(editing ? `${endpoint}/${id}` : endpoint, {
        method: editing ? "PATCH" : "POST",
        body: JSON.stringify(editing ? payload : { ...initial, ...payload }),
      });
      onSaved();
      onClose();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title={title}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" type="button" onClick={onClose}>Cancelar</button>
          <button className="btn btn-primary" form="record-form" disabled={busy}>{busy ? "Salvando…" : "Salvar"}</button>
        </>
      }
    >
      <form id="record-form" className="stack" onSubmit={submit}>
        {fields.map((f) => {
          const locked = editing && "readOnlyOnEdit" in f && f.readOnlyOnEdit;
          if (f.type === "checkbox")
            return (
              <label key={f.key} className="check">
                <input type="checkbox" checked={Boolean(values[f.key])} onChange={(e) => set(f.key, e.target.checked)} />
                {f.label}
              </label>
            );
          if (f.type === "select")
            return (
              <label key={f.key}>
                {f.label}
                <select
                  value={values[f.key] === null || values[f.key] === undefined ? "" : String(values[f.key])}
                  required={f.required}
                  disabled={locked}
                  onChange={(e) => {
                    const opt = f.options.find((o) => String(o.value) === e.target.value);
                    set(f.key, opt ? opt.value : null);
                  }}
                >
                  {(f.allowEmpty || !values[f.key]) && <option value="">—</option>}
                  {f.options.map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              </label>
            );
          return (
            <label key={f.key}>
              {f.label}
              <input
                type={f.type}
                step="any"
                required={f.required}
                disabled={locked}
                value={values[f.key] === null || values[f.key] === undefined ? "" : String(values[f.key])}
                onChange={(e) => set(f.key, e.target.value)}
              />
              {"hint" in f && f.hint && <span className="muted small">{f.hint}</span>}
            </label>
          );
        })}
        {error && <Alert>{error}</Alert>}
      </form>
    </Modal>
  );
}
