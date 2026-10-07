import { useState } from "react";
import { api, type Finding, type FindingFix } from "../api";
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

/** Aviso que fica como veio: a Controladoria registra o motivo e o(s) item(ns) saem da lista de pendentes. */
export function KeepModal({ findings, onClose, onDone }: { findings: Finding[]; onClose: () => void; onDone: (note: string) => void }) {
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const first = findings[0];
  const many = findings.length > 1;

  async function save() {
    setBusy(true);
    setError(null);
    try {
      if (!many) {
        const r = await api<{ note: string }>("/findings/keep", { method: "POST", body: JSON.stringify({ key: first.key, note }) });
        onDone(r.note);
        return;
      }
      const r = await api<BulkResult>("/findings/keep-many", { method: "POST", body: JSON.stringify({ keys: findings.map((f) => f.key), note }) });
      onDone(bulkNote(r, "mantido(s)"));
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <Modal
      title={`Manter · ${first.kind_label}${many ? ` · ${findings.length} itens` : ""}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>Cancelar</button>
          <button type="button" className="btn btn-primary" disabled={!note.trim() || busy} onClick={save}>{busy ? "Salvando…" : many ? `Manter os ${findings.length}` : "Manter como está"}</button>
        </>
      }
    >
      <div className="stack">
        {many ? (
          <p className="small">O mesmo motivo vale para os {findings.length} itens deste tipo na lista ({new Set(findings.map((f) => f.cost_center_id)).size} CC).</p>
        ) : (
          <>
            <p className="small"><strong>{first.cost_center}</strong> · {first.subject}</p>
            <p className="muted small">{first.message}</p>
          </>
        )}
        {error && <Alert>{error}</Alert>}
        <label>Por que {many ? "os itens ficam" : "o item fica"} como está?
          <textarea rows={4} value={note} onChange={(e) => setNote(e.target.value)} autoFocus placeholder="Ex.: licença perpétua, ativo intangível" />
        </label>
      </div>
    </Modal>
  );
}

export interface BulkResult { done: { key: string; note: string }[]; failed: { key: string; error: string }[] }

export function bulkNote(r: BulkResult, verb: string): string {
  const fail = r.failed.length ? ` · ${r.failed.length} não aplicado(s): ${r.failed[0].error}` : "";
  return `${r.done.length} ${verb}${fail}`;
}

/** Valor digitado/escolhido → corpo da correção (mesmo formato do /findings/fix). */
function fixBody(type: FindingFix["type"], value: string): Record<string, unknown> {
  switch (type) {
    case "month": return { month: Number(value) };
    case "money": return { amount: parseMoney(value) ?? 0 };
    case "ticket": return { ticket_amount: parseMoney(value) ?? 0 };
    case "text": return { text: value };
    case "project_type": return { project_type_code: value };
    case "sector": return { area_id: Number(value) };
    case "cost_center": return { cost_center_id: Number(value) };
    default: return {};
  }
}

function needsValue(type: FindingFix["type"]) {
  return ["month", "money", "ticket", "text", "project_type", "sector", "cost_center"].includes(type);
}

/** Campo da correção (mês, setor, tipo de projeto, valor ou texto). */
function FixControl({ fix, value, onChange, projectTypes, sectors, compact }: {
  fix: FindingFix; value: string; onChange: (v: string) => void;
  projectTypes: { value: string; label: string }[]; sectors: { id: number; label: string }[]; compact?: boolean;
}) {
  const label = fix.label ?? (fix.type === "ticket" ? `Passagem (R$) · ${fix.route ?? ""}` : "Correção");
  if (fix.type === "month") {
    return (
      <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}>
        <option value="">Mês…</option>
        {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
      </select>
    );
  }
  if (fix.type === "sector") {
    return (
      <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}>
        <option value="">Setor…</option>
        {sectors.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
      </select>
    );
  }
  if (fix.type === "cost_center") {
    return (
      <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}>
        <option value="">Centro de custo…</option>
        {(fix.options ?? []).map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
      </select>
    );
  }
  if (fix.type === "project_type") {
    return (
      <select value={value} onChange={(e) => onChange(e.target.value)} aria-label="Tipo de projeto">
        <option value="">Tipo de projeto…</option>
        {projectTypes.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
      </select>
    );
  }
  if (fix.type === "text") {
    return <textarea rows={compact ? 1 : 2} value={value} onChange={(e) => onChange(e.target.value)} aria-label={label} placeholder={label} />;
  }
  return <input value={value} onChange={(e) => onChange(e.target.value)} inputMode="decimal" aria-label={label} placeholder={fix.type === "ticket" ? "Passagem R$" : "Novo salário R$"} />;
}

/** Correção na própria linha do apontamento; o cronograma do CAPEX abre a janela de meses. */
export function InlineFix({ finding, projectTypes, sectors, onDone, onSchedule }: {
  finding: Finding; projectTypes: { value: string; label: string }[]; sectors: { id: number; label: string }[];
  onDone: (note: string) => void; onSchedule: () => void;
}) {
  const fix = finding.fix!;
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ready = !needsValue(fix.type) || (fix.type === "money" || fix.type === "ticket" ? (parseMoney(value) ?? 0) > 0 : value.trim() !== "");

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ note: string }>("/findings/fix", { method: "POST", body: JSON.stringify({ key: finding.key, ...fixBody(fix.type, value) }) });
      onDone(r.note);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  if (fix.type === "schedule") return <button type="button" className="btn btn-sm" onClick={onSchedule}>Ajustar cronograma</button>;
  return (
    <div className="inline-fix">
      {needsValue(fix.type) && <FixControl fix={fix} value={value} onChange={setValue} projectTypes={projectTypes} sectors={sectors} compact />}
      <button type="button" className={`btn btn-sm${needsValue(fix.type) ? "" : " btn-primary"}`} disabled={!ready || busy} onClick={save}>
        {busy ? "Salvando…" : fix.type === "confirm" ? "Confirmar" : fix.type === "account" ? `Usar ${fix.account?.split(" · ")[0] ?? "conta do catálogo"}` : "Salvar"}
      </button>
      {error && <div className="error-text small">{error}</div>}
    </div>
  );
}

/** O grupo aceita a mesma correção para todos (valores individuais, como passagem e salário, não). */
export function canBulkFix(items: Finding[]) {
  const type = items[0]?.fix?.type;
  return items.length > 1 && !!type && ["confirm", "account", "month", "sector", "cost_center", "project_type", "text"].includes(type);
}

/** Mesma correção para todos os itens do grupo (confirmar, mês, setor, tipo de projeto, conta do catálogo, texto). */
export function BulkFix({ items, projectTypes, sectors, onDone }: {
  items: Finding[]; projectTypes: { value: string; label: string }[]; sectors: { id: number; label: string }[];
  onDone: (note: string) => void;
}) {
  const fix = items[0]?.fix;
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!fix || !canBulkFix(items)) return null;
  const ready = !needsValue(fix.type) || value.trim() !== "";

  async function save() {
    if (!window.confirm(`Aplicar a mesma correção aos ${items.length} itens?`)) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api<BulkResult>("/findings/fix-many", { method: "POST", body: JSON.stringify({ keys: items.map((i) => i.key), ...fixBody(fix!.type, value) }) });
      setValue("");
      onDone(bulkNote(r, "corrigido(s)"));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const verb = fix.type === "confirm" ? "Confirmar" : fix.type === "account" ? "Usar conta do catálogo em" : "Aplicar a";
  return (
    <div className="inline-fix bulk-fix">
      {needsValue(fix.type) && <FixControl fix={fix} value={value} onChange={setValue} projectTypes={projectTypes} sectors={sectors} compact />}
      <button type="button" className="btn btn-sm btn-primary" disabled={!ready || busy} onClick={save}>
        {busy ? "Aplicando…" : `${verb} todos (${items.length})`}
      </button>
      {error && <div className="error-text small">{error}</div>}
    </div>
  );
}
