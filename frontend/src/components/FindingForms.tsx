import { useState } from "react";
import { api, type Finding } from "../api";
import { MONTHS, fmtMoney } from "../labels";
import { MoneyInput, parseMoney } from "./opex/MoneyInput";
import { Alert, Modal } from "./ui";

const round2 = (n: number) => Math.round(n * 100) / 100;

/** Correção direta do apontamento: cronograma, conta do catálogo, justificativa, tipo de projeto, passagem, salário ou mês. */
export function FixModal({ finding, projectTypes, onClose, onDone }: {
  finding: Finding; projectTypes: { value: string; label: string }[]; onClose: () => void; onDone: (note: string) => void;
}) {
  const fix = finding.fix!;
  const total = Number(fix.total ?? 0);
  const [values, setValues] = useState<number[]>(MONTHS.map((_, i) => Number(fix.values?.[String(i + 1)] ?? 0)));
  const [oneMonth, setOneMonth] = useState("1");
  const [from, setFrom] = useState("1");
  const [to, setTo] = useState("12");
  const [text, setText] = useState("");
  const [projectType, setProjectType] = useState("");
  const [ticket, setTicket] = useState("");
  const [month, setMonth] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const scheduled = round2(values.reduce((s, v) => s + v, 0));
  const diff = round2(scheduled - total);
  const ticketValue = parseMoney(ticket) ?? 0;
  const ready =
    fix.type === "schedule" ? diff === 0 && total > 0
      : fix.type === "text" ? text.trim().length > 0
        : fix.type === "project_type" ? projectType !== ""
          : fix.type === "ticket" || fix.type === "money" ? ticketValue > 0
            : fix.type === "month" ? month !== ""
              : true;

  const allIn = () => setValues(MONTHS.map((_, i) => (i + 1 === Number(oneMonth) ? total : 0)));
  function spread() {
    const a = Number(from), b = Math.max(Number(to), a);
    const n = b - a + 1;
    const part = Math.floor((total / n) * 100) / 100;
    setValues(MONTHS.map((_, i) => {
      const m = i + 1;
      if (m < a || m > b) return 0;
      return m === b ? round2(total - part * (n - 1)) : part; // último mês absorve o arredondamento
    }));
  }

  async function save() {
    setBusy(true);
    setError(null);
    const body: Record<string, unknown> = { key: finding.key };
    if (fix.type === "schedule") body.values = Object.fromEntries(values.map((v, i) => [String(i + 1), v]));
    if (fix.type === "text") body.text = text;
    if (fix.type === "project_type") body.project_type_code = projectType;
    if (fix.type === "ticket") body.ticket_amount = ticketValue;
    if (fix.type === "money") body.amount = ticketValue;
    if (fix.type === "month") body.month = Number(month);
    try {
      const r = await api<{ note: string }>("/findings/fix", { method: "POST", body: JSON.stringify(body) });
      onDone(r.note);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <Modal
      wide={fix.type === "schedule"}
      title={`Corrigir · ${finding.kind_label}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>Cancelar</button>
          <button type="button" className="btn btn-primary" disabled={!ready || busy} onClick={save}>
            {busy ? "Salvando…" : fix.type === "account" ? "Usar conta do catálogo" : fix.type === "confirm" ? (fix.label ?? "Confirmar") : "Salvar correção"}
          </button>
        </>
      }
    >
      <div className="stack">
      <p className="small"><strong>{finding.cost_center}</strong> · {finding.subject}</p>
      <p className="muted small">{finding.message}</p>
      {error && <Alert>{error}</Alert>}

      {fix.type === "schedule" && (
        <>
          <div className="schedule-tools">
            <span>Tudo em</span>
            <select value={oneMonth} onChange={(e) => setOneMonth(e.target.value)} aria-label="Mês único">
              {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
            </select>
            <button type="button" className="btn btn-sm" onClick={allIn}>Aplicar</button>
            <span className="muted">·</span>
            <span>Dividir igualmente de</span>
            <select value={from} onChange={(e) => setFrom(e.target.value)} aria-label="Mês inicial">
              {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
            </select>
            <span>a</span>
            <select value={to} onChange={(e) => setTo(e.target.value)} aria-label="Mês final">
              {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
            </select>
            <button type="button" className="btn btn-sm" onClick={spread}>Distribuir</button>
          </div>
          <div className="schedule-grid">
            {MONTHS.map((m, i) => (
              <label key={m}>{m}
                <MoneyInput
                  value={values[i]}
                  label={`Cronograma ${m}`}
                  onCommit={(n) => setValues((v) => v.map((x, j) => (j === i ? n : x)))}
                  onPasteMany={(nums) => setValues((v) => v.map((x, j) => (j >= i && j - i < nums.length ? nums[j - i] : x)))}
                />
              </label>
            ))}
          </div>
          <div className="schedule-status">
            <span>Total do item: <strong>{fmtMoney(total)}</strong></span>
            <span>Cronograma: <strong>{fmtMoney(scheduled)}</strong></span>
            {diff === 0 ? <span className="good">✓ cronograma fecha com o total</span> : <span className="bad">Diferença: {fmtMoney(diff)}</span>}
          </div>
        </>
      )}

      {fix.type === "account" && (
        <p>A conta do item passa a ser <strong>{fix.account}</strong>, a indicada no catálogo de ativos.</p>
      )}

      {fix.type === "text" && (
        <label>{fix.label}
          <textarea rows={4} value={text} onChange={(e) => setText(e.target.value)} autoFocus />
        </label>
      )}

      {fix.type === "project_type" && (
        <label>Tipo de projeto
          <select value={projectType} onChange={(e) => setProjectType(e.target.value)}>
            <option value="">Selecione…</option>
            {projectTypes.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
        </label>
      )}

      {fix.type === "money" && (
        <label>{fix.label}
          <input value={ticket} onChange={(e) => setTicket(e.target.value)} inputMode="decimal" placeholder="0,00" autoFocus />
        </label>
      )}

      {fix.type === "month" && (
        <label>{fix.label}
          <select value={month} onChange={(e) => setMonth(e.target.value)} autoFocus>
            <option value="">Selecione…</option>
            {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
          </select>
        </label>
      )}

      {fix.type === "confirm" && (
        <p>Confirma que o item está certo como o sistema definiu? A pendência sai da lista e fica no registro da análise.</p>
      )}

      {fix.type === "ticket" && (
        <>
          <label>Valor da passagem (R$) · {fix.route}
            <input value={ticket} onChange={(e) => setTicket(e.target.value)} inputMode="decimal" placeholder="0,00" autoFocus />
          </label>
          <p className="muted small">Lançada na conta de passagens, no mês de ida da viagem.</p>
        </>
      )}
      </div>
    </Modal>
  );
}

/** Aviso que fica como veio: a Controladoria registra o motivo e o item sai da lista de pendentes. */
export function KeepModal({ finding, onClose, onDone }: { finding: Finding; onClose: () => void; onDone: (note: string) => void }) {
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ note: string }>("/findings/keep", { method: "POST", body: JSON.stringify({ key: finding.key, note }) });
      onDone(r.note);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <Modal
      title={`Manter · ${finding.kind_label}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>Cancelar</button>
          <button type="button" className="btn btn-primary" disabled={!note.trim() || busy} onClick={save}>{busy ? "Salvando…" : "Manter como está"}</button>
        </>
      }
    >
      <div className="stack">
      <p className="small"><strong>{finding.cost_center}</strong> · {finding.subject}</p>
      <p className="muted small">{finding.message}</p>
      {error && <Alert>{error}</Alert>}
      <label>Por que o item fica como está?
        <textarea rows={4} value={note} onChange={(e) => setNote(e.target.value)} autoFocus placeholder="Ex.: licença perpétua, ativo intangível" />
      </label>
      </div>
    </Modal>
  );
}
