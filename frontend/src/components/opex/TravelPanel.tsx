import { useState, type FormEvent } from "react";
import { api, type OpexLine, type OpexOptions } from "../../api";
import { MONTHS, fmtMoney } from "../../labels";
import { Alert, Empty } from "../ui";
import { parseMoney } from "./MoneyInput";

const ACCOUNT_LABEL: Record<string, string> = { "6010301011": "Passagem", "6010301036": "Diária", "6010301001": "Hospedagem" };

/** Viagens: cada viagem gera passagem, diária e hospedagem no mês de ida (regra do template I - Viagens). */
export function TravelPanel({ submissionId, lines, options, editable, onChanged }: {
  submissionId: number; lines: OpexLine[]; options: OpexOptions; editable: boolean; onChanged: () => void;
}) {
  const lk = options.lookups;
  const empty = { trip_type: "Nacional", job_level: lk.JOB_LEVEL?.[0]?.code ?? "", origin: "AM", destination: "", departure_month: "1",
                  return_month: "1", days: "1", purpose: "", ticket_amount: "", one_way: false };
  const [form, setForm] = useState(empty);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const accounts = new Map(options.accounts.map((a) => [a.id, a.code]));
  const trips = Object.values(
    lines.filter((l) => l.line_type === "TRAVEL").reduce<Record<string, OpexLine[]>>((acc, l) => {
      (acc[l.group_ref ?? String(l.id)] ??= []).push(l);
      return acc;
    }, {}),
  );
  const destinations = (lk.TRAVEL_DESTINATION ?? []).filter((d) => !form.trip_type || d.extra?.trip_type === form.trip_type);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const ticket = form.ticket_amount ? parseMoney(form.ticket_amount) : null;
    try {
      await api(`/opex/submissions/${submissionId}/lines`, {
        method: "POST",
        body: JSON.stringify({
          line_type: "TRAVEL",
          description: form.purpose || null,
          travel: { ...form, return_month: form.one_way ? null : form.return_month, ticket_amount: ticket, one_way: undefined },
        }),
      });
      setForm({ ...empty, trip_type: form.trip_type, job_level: form.job_level, origin: form.origin });
      onChanged();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function remove(line: OpexLine) {
    if (!window.confirm("Excluir esta viagem (passagem, diária e hospedagem)?")) return;
    await api(`/opex/lines/${line.id}`, { method: "DELETE" });
    onChanged();
  }

  const set = (k: keyof typeof empty, v: string | boolean) =>
    setForm((f) => {
      const next = { ...f, [k]: v };
      // volta acompanha a ida para não gerar volta antes da ida
      if (k === "departure_month" && Number(next.return_month) < Number(v)) next.return_month = String(v);
      return next;
    });

  return (
    <div className="stack">
      {editable && (
        <form className="calc-form" onSubmit={submit}>
          <div className="form-row">
            <label>Objetivo da viagem<input value={form.purpose} onChange={(e) => set("purpose", e.target.value)} placeholder="Ex.: auditoria base Belém" /></label>
            <label>Tipo
              <select value={form.trip_type} onChange={(e) => set("trip_type", e.target.value)}>
                {(lk.TRIP_TYPE ?? []).map((t) => <option key={t.code} value={t.code}>{t.label}</option>)}
              </select>
            </label>
            <label>Cargo do viajante
              <select value={form.job_level} onChange={(e) => set("job_level", e.target.value)}>
                {(lk.JOB_LEVEL ?? []).map((t) => <option key={t.code} value={t.code}>{t.label}</option>)}
              </select>
            </label>
          </div>
          <div className="form-row">
            <label>Origem (UF)
              <select value={form.origin} onChange={(e) => set("origin", e.target.value)}>
                {(lk.TRAVEL_ORIGIN ?? []).map((t) => <option key={t.code} value={t.code}>{t.label}</option>)}
              </select>
            </label>
            <label>Destino
              <select required value={form.destination} onChange={(e) => set("destination", e.target.value)}>
                <option value="">Selecione…</option>
                {destinations.map((t) => <option key={t.code} value={t.code}>{t.label}</option>)}
              </select>
            </label>
            <label>Mês de ida
              <select value={form.departure_month} onChange={(e) => set("departure_month", e.target.value)}>
                {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
              </select>
            </label>
            <label>Mês de volta
              <select value={form.return_month} disabled={form.one_way} onChange={(e) => set("return_month", e.target.value)}>
                {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
              </select>
            </label>
            <label>Dias<input type="number" min={0} required value={form.days} onChange={(e) => set("days", e.target.value)} /></label>
            <label>Passagem ida e volta (R$)
              <input value={form.ticket_amount} onChange={(e) => set("ticket_amount", e.target.value)} placeholder="tabela, se cadastrada" inputMode="decimal" />
            </label>
          </div>
          <label className="check">
            <input type="checkbox" checked={form.one_way} onChange={(e) => set("one_way", e.target.checked)} />
            Só ida (passagem = {Number(options.params.one_way_factor) * 100}% do valor de ida e volta)
          </label>
          {error && <Alert>{error}</Alert>}
          <div><button className="btn btn-primary" disabled={busy}>{busy ? "Calculando…" : "Adicionar viagem"}</button></div>
          <p className="muted small">Diária e hospedagem vêm da tabela do ciclo (tipo × cargo × dias). Tudo é lançado no mês de ida.</p>
        </form>
      )}
      {trips.length === 0 ? (
        <Empty>Nenhuma viagem lançada.</Empty>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr><th>Viagem</th><th>Trajeto</th><th>Mês</th><th className="right">Passagem</th><th className="right">Diária</th><th className="right">Hospedagem</th><th className="right">Total</th>{editable && <th />}</tr>
            </thead>
            <tbody>
              {trips.map((group) => {
                const a = group[0].attributes ?? {};
                const by = (label: string) => group.find((l) => ACCOUNT_LABEL[accounts.get(l.account_id) ?? ""] === label)?.total ?? "0";
                const total = group.reduce((s, l) => s + Number(l.total), 0);
                return (
                  <tr key={group[0].group_ref ?? group[0].id}>
                    <td>{group[0].description ?? "—"}<div className="muted small">{a.job_level} · {a.days} dia(s)</div>
                      {(a.warnings ?? []).map((w: string) => <div key={w} className="error-text">{w}</div>)}
                      {a.ticket_estimate && <div className="muted small">Passagem estimada pela Controladoria ({a.ticket_estimate.how}) — não orçada no template</div>}
                    </td>
                    <td>{a.origin} → {a.destination}<div className="muted small">{a.trip_type}{a.return_month ? "" : " · só ida"}</div></td>
                    <td>{MONTHS[Number(a.departure_month) - 1]}</td>
                    <td className="right">{fmtMoney(by("Passagem"))}</td>
                    <td className="right">{fmtMoney(by("Diária"))}</td>
                    <td className="right">{fmtMoney(by("Hospedagem"))}</td>
                    <td className="right"><strong>{fmtMoney(total)}</strong></td>
                    {editable && <td><button className="btn btn-ghost btn-sm danger" aria-label="Excluir viagem" title="Excluir viagem" onClick={() => remove(group[0])}>✕</button></td>}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
