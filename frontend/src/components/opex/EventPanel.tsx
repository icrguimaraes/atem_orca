import { useState, type FormEvent } from "react";
import { api, type OpexLine, type OpexOptions } from "../../api";
import { MONTHS, fmtMoney } from "../../labels";
import { Alert, Empty } from "../ui";
import { parseMoney } from "./MoneyInput";

/** Eventos (Comunicação e MKT): alimentação por pessoa (interno/externo) + materiais, no mês do evento. */
export function EventPanel({ submissionId, packageId, lines, options, editable, onChanged }: {
  submissionId: number; packageId: number; lines: OpexLine[]; options: OpexOptions; editable: boolean; onChanged: () => void;
}) {
  const accounts = options.accounts.filter((a) => a.package_id === packageId);
  const types = options.lookups.EVENT_TYPE ?? [];
  const empty = { account_id: String(accounts.find((a) => a.code === "6010501002")?.id ?? accounts[0]?.id ?? ""), description: "",
                  event_type: types[0]?.code ?? "Interno", month: "1", people: "0", graphic_material: "", structure: "", gifts: "", transport: "" };
  const [form, setForm] = useState(empty);
  const [error, setError] = useState<string | null>(null);
  const events = lines.filter((l) => l.line_type === "EVENT");
  const set = (k: keyof typeof empty, v: string) => setForm((f) => ({ ...f, [k]: v }));
  const meal = Number(types.find((t) => t.code === form.event_type)?.extra?.meal_per_person ?? 0);
  const preview = meal * Number(form.people || 0) + ["graphic_material", "structure", "gifts", "transport"]
    .reduce((s, k) => s + (parseMoney(form[k as keyof typeof empty]) ?? 0), 0);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api(`/opex/submissions/${submissionId}/lines`, {
        method: "POST",
        body: JSON.stringify({
          line_type: "EVENT", account_id: Number(form.account_id), package_id: packageId, description: form.description || null,
          event: {
            event_type: form.event_type, month: Number(form.month), people: Number(form.people || 0),
            graphic_material: parseMoney(form.graphic_material) ?? 0, structure: parseMoney(form.structure) ?? 0,
            gifts: parseMoney(form.gifts) ?? 0, transport: parseMoney(form.transport) ?? 0,
          },
        }),
      });
      setForm(empty);
      onChanged();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <div className="stack">
      {editable && (
        <form className="calc-form" onSubmit={submit}>
          <div className="form-row">
            <label>Evento / objetivo<input required value={form.description} onChange={(e) => set("description", e.target.value)} /></label>
            <label>Conta
              <select value={form.account_id} onChange={(e) => set("account_id", e.target.value)}>
                {accounts.map((a) => <option key={a.id} value={a.id}>{a.code} · {a.name}</option>)}
              </select>
            </label>
            <label>Interno/externo
              <select value={form.event_type} onChange={(e) => set("event_type", e.target.value)}>
                {types.map((t) => <option key={t.code} value={t.code}>{t.label} (R$ {t.extra?.meal_per_person}/pessoa)</option>)}
              </select>
            </label>
            <label>Mês
              <select value={form.month} onChange={(e) => set("month", e.target.value)}>
                {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
              </select>
            </label>
          </div>
          <div className="form-row">
            <label>Qtd. pessoas<input type="number" min={0} value={form.people} onChange={(e) => set("people", e.target.value)} /></label>
            <label>Material gráfico/audiovisual<input inputMode="decimal" value={form.graphic_material} onChange={(e) => set("graphic_material", e.target.value)} /></label>
            <label>Estrutura<input inputMode="decimal" value={form.structure} onChange={(e) => set("structure", e.target.value)} /></label>
            <label>Brindes<input inputMode="decimal" value={form.gifts} onChange={(e) => set("gifts", e.target.value)} /></label>
            <label>Deslocamento<input inputMode="decimal" value={form.transport} onChange={(e) => set("transport", e.target.value)} /></label>
          </div>
          {error && <Alert>{error}</Alert>}
          <div className="inline-controls">
            <button className="btn btn-primary">Adicionar evento</button>
            <span className="muted">Total do evento: <strong>{fmtMoney(preview)}</strong></span>
          </div>
        </form>
      )}
      {events.length === 0 ? (
        <Empty>Nenhum evento lançado.</Empty>
      ) : (
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Evento</th><th>Tipo</th><th>Mês</th><th className="right">Pessoas</th><th className="right">Total</th>{editable && <th />}</tr></thead>
          <tbody>
            {events.map((l) => (
              <tr key={l.id}>
                <td>{l.description}<div className="muted small mono">{options.accounts.find((a) => a.id === l.account_id)?.code}</div></td>
                <td>{l.attributes?.event_type}</td>
                <td>{MONTHS[Number(l.attributes?.month) - 1]}</td>
                <td className="right">{l.attributes?.people}</td>
                <td className="right"><strong>{fmtMoney(l.total)}</strong></td>
                {editable && (
                  <td><button className="btn btn-ghost btn-sm danger" onClick={async () => {
                    if (window.confirm("Excluir este evento?")) { await api(`/opex/lines/${l.id}`, { method: "DELETE" }); onChanged(); }
                  }}>✕</button></td>
                )}
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
    </div>
  );
}
