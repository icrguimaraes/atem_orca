import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { fmtCompact, fmtDateTime, fmtMoney, fmtPct, fmtSignedMoney } from "../labels";
import { Badge, Loading } from "./ui";

/* Defesa do orçamento (08/10/2026): "por quê?" de uma linha do Painel — orçamento do ciclo × realizado do ano
   anterior anualizado, com a decomposição, o que o gestor justificou e as perguntas ao gestor da área. O resumo
   aparece ao passar o mouse; o clique abre o painel lateral. */

interface Driver { name: string; sub: string | null; base: string; proposed: string; var: string; var_pct: string | null; module: string | null; account_id?: number; cost_center_id?: number }
interface WhyItem {
  key: string; module: string; cost_center_id: number; cost_center: string; subject: string; group: string;
  base: string | null; proposed: string | null; var?: string; details: string[]; line_texts: string[]; text: string;
  justified: boolean; movement_type?: string;
}
export interface Question {
  id: number; subject: string; scope: Record<string, unknown>; question: string; status: "OPEN" | "ANSWERED" | "CLOSED";
  status_label: string; asked_by: string | null; asked_at: string | null; answer: string | null; answered_by: string | null;
  answered_at: string | null; mine: boolean; can_answer: boolean; can_close: boolean; cost_center_id: number | null;
}
export interface WhyData {
  title: string; target_year: number; ref_year: number; closed: number | null; base_label: string;
  total: { base: string; proposed: string; var: string; var_pct: string | null };
  by_module: { module: string; label: string; base: string; proposed: string; var: string; var_pct: string | null }[];
  drivers: { accounts: Driver[]; cost_centers: Driver[] };
  alerts: { tone: "warn" | "bad"; text: string }[];
  opex: WhyItem[]; opex_count: number; personnel: WhyItem[]; personnel_summary: Record<string, number>; capex: WhyItem[];
  missing: number; questions: Question[];
  scope: { cost_center_ids: number[]; single_cost_center: { id: number; code: string; name: string } | null; account_id: number | null; department_id: number | null };
}

const cache = new Map<string, Promise<WhyData>>();
function loadWhy(qs: string, fresh = false): Promise<WhyData> {
  if (fresh) cache.delete(qs);
  if (!cache.has(qs)) {
    const p = api<WhyData>(`/dashboard/why?${qs}`);
    p.catch(() => cache.delete(qs));
    cache.set(qs, p);
  }
  return cache.get(qs)!;
}

const tone = (v: string) => (Number(v) > 0 ? "error-text" : Number(v) < 0 ? "good-text" : "muted");
const varText = (v: string, pct: string | null) => `${fmtSignedMoney(v)}${pct !== null ? ` (${fmtPct(pct)})` : ""}`;

/** Botão "por quê?" de uma linha: resumo no hover (carregado sob demanda) e painel lateral no clique. */
export function WhyButton({ qs, label }: { qs: string; label: string }) {
  const [open, setOpen] = useState(false);
  const [tip, setTip] = useState<WhyData | "loading" | null>(null);
  const [hover, setHover] = useState<{ x: number; y: number } | null>(null);
  const timer = useRef<number>();
  const btn = useRef<HTMLButtonElement>(null);
  const full = `${qs}${qs ? "&" : ""}label=${encodeURIComponent(label)}`;

  function enter() {
    const r = btn.current?.getBoundingClientRect();
    // tooltip fora da tabela (a área de rolagem cortaria): posição fixa, sem passar da borda direita da tela
    if (r) setHover({ x: Math.max(8, Math.min(r.left, window.innerWidth - 356)), y: r.bottom + 6 });
    timer.current = window.setTimeout(() => {
      setTip((t) => t ?? "loading");
      loadWhy(full).then(setTip).catch(() => setTip(null));
    }, 250);
  }
  function leave() {
    setHover(null);
    window.clearTimeout(timer.current);
  }

  return (
    <span className="why-wrap" onMouseEnter={enter} onMouseLeave={leave}>
      <button ref={btn} type="button" className="why-btn" onClick={(e) => { e.stopPropagation(); setOpen(true); }} aria-label={`Por que mudou: ${label}`}>
        por quê?
      </button>
      {hover && tip && createPortal(
        <div className="why-tip" role="tooltip" style={{ left: hover.x, top: hover.y }}>
          {tip === "loading" ? (
            <span className="muted">Carregando…</span>
          ) : (
            <>
              <strong className={tone(tip.total.var)}>{varText(tip.total.var, tip.total.var_pct)}</strong>
              <span className="muted"> · orçamento {tip.target_year} × {tip.base_label.toLowerCase()}</span>
              {tip.by_module.map((m) => (
                <div key={m.module}>{m.label}: <span className={tone(m.var)}>{fmtSignedMoney(m.var)}</span></div>
              ))}
              {tip.drivers.accounts.slice(0, 3).map((d) => (
                <div key={d.name} className="muted">{d.name}: {fmtSignedMoney(d.var)}</div>
              ))}
              {tip.missing > 0 && <div className="error-text">{tip.missing} item(ns) sem justificativa</div>}
              {tip.questions.some((q) => q.status === "OPEN") && <div>Pergunta aguardando resposta</div>}
              <div className="muted small">Clique para detalhar e questionar</div>
            </>
          )}
        </div>,
        document.body,
      )}
      {open && <WhyDrawer qs={full} onClose={() => setOpen(false)} />}
    </span>
  );
}

function Bars({ rows }: { rows: Driver[] }) {
  const max = Math.max(1, ...rows.map((r) => Math.abs(Number(r.var))));
  return (
    <div className="why-bars">
      {rows.filter((r) => Number(r.var)).map((r) => (
        <div key={r.name} className="why-bar-row" title={`${fmtMoney(r.base)} → ${fmtMoney(r.proposed)}`}>
          <span className="why-bar-name">{r.name}{r.sub && <span className="muted small"> · {r.sub}</span>}</span>
          <span className="why-bar-track"><span className={`why-bar ${Number(r.var) > 0 ? "up" : "down"}`} style={{ width: `${Math.round((Math.abs(Number(r.var)) / max) * 100)}%` }} /></span>
          <span className={`why-bar-val ${tone(r.var)}`}>{fmtSignedMoney(r.var)}</span>
        </div>
      ))}
    </div>
  );
}

function ItemLine({ i, showCc }: { i: WhyItem; showCc: boolean }) {
  const text = i.text || i.line_texts.join(" / ");
  return (
    <li className="why-item">
      <div className="why-item-head">
        <span>
          <strong>{i.subject}</strong>
          {showCc && <span className="muted small"> · {i.cost_center}</span>}
        </span>
        {i.justified ? <Badge tone="good">justificado</Badge> : <Badge tone="bad">sem justificativa</Badge>}
      </div>
      {i.module === "OPEX" && i.base !== null && i.proposed !== null && (
        <div className="small">
          {fmtMoney(i.base)} → <strong>{fmtMoney(i.proposed)}</strong>
          {i.var !== undefined && <span className={tone(i.var)}> · {fmtSignedMoney(i.var)}</span>}
        </div>
      )}
      {i.module !== "OPEX" && i.details.length > 0 && <div className="small muted">{i.details.slice(0, 3).join(" · ")}</div>}
      {text ? <div className="why-quote">“{text}”</div> : i.module === "OPEX" && i.details.length > 0 && <div className="small muted">{i.details.slice(0, 3).join(" · ")}</div>}
    </li>
  );
}

function QuestionCard({ q, onChange }: { q: Question; onChange: (q: Question) => void }) {
  const [answer, setAnswer] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function act(path: string, body?: object) {
    setBusy(true);
    setErr(null);
    try {
      onChange(await api<Question>(`/questions/${q.id}/${path}`, { method: "POST", body: body ? JSON.stringify(body) : undefined }));
      setAnswer("");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="question">
      <div className="muted small">
        {q.asked_by ?? "—"} · {fmtDateTime(q.asked_at)} · sobre {q.subject}{" "}
        <Badge tone={q.status === "OPEN" ? "warn" : q.status === "ANSWERED" ? "info" : "neutral"}>{q.status_label}</Badge>
      </div>
      <div className="question-text">{q.question}</div>
      {q.answer && (
        <div className="answer">
          <div className="muted small">{q.answered_by} · {fmtDateTime(q.answered_at)}</div>
          {q.answer}
        </div>
      )}
      {q.can_answer && (
        <div className="question-form">
          <textarea rows={2} value={answer} onChange={(e) => { setAnswer(e.target.value); setErr(null); }} placeholder={q.answer ? "Complementar a resposta" : "Sua resposta (o que é, por que mudou, premissa)"} aria-label="Resposta" />
          <button type="button" className="btn btn-sm btn-primary" disabled={busy} onClick={() => (answer.trim() ? act("answer", { answer }) : setErr("Escreva a resposta antes de enviar"))}>
            Responder
          </button>
        </div>
      )}
      {q.can_close && q.status === "ANSWERED" && (
        <button type="button" className="btn btn-sm btn-ghost" disabled={busy} onClick={() => act("close")}>Encerrar pergunta</button>
      )}
      {err && <div className="error-text">{err}</div>}
    </div>
  );
}

/** Painel lateral "Por que mudou" (portal, sobre a página). */
export function WhyDrawer({ qs, onClose }: { qs: string; onClose: () => void }) {
  const [data, setData] = useState<WhyData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [by, setBy] = useState<"accounts" | "cost_centers">("accounts");
  const [asking, setAsking] = useState(false);
  const [question, setQuestion] = useState("");
  const [askErr, setAskErr] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    loadWhy(qs).then(setData).catch((e: Error) => setError(e.message));
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [qs, onClose]);

  async function ask() {
    if (!data) return;
    if (!question.trim()) {
      setAskErr("Escreva a pergunta antes de enviar");
      return;
    }
    try {
      const q = await api<Question>("/questions", {
        method: "POST",
        body: JSON.stringify({
          subject: data.title,
          question,
          scope: { cost_center_ids: data.scope.cost_center_ids, why: qs },
          account_id: data.scope.account_id,
          department_id: data.scope.department_id,
        }),
      });
      setData({ ...data, questions: [q, ...data.questions] });
      cache.delete(qs);
      setQuestion("");
      setAsking(false);
      setSent(true);
    } catch (e) {
      setAskErr((e as Error).message);
    }
  }
  function updateQuestion(q: Question) {
    if (!data) return;
    setData({ ...data, questions: data.questions.map((x) => (x.id === q.id ? q : x)) });
    cache.delete(qs);
  }
  function openJustifications() {
    try {
      const cc = data?.scope.single_cost_center;
      localStorage.setItem("atem.filters.justificativas.cc", JSON.stringify(cc ? String(cc.id) : ""));
    } catch {
      /* sem armazenamento: abre sem filtro */
    }
    navigate("/justificativas");
  }

  const multiCc = (data?.scope.cost_center_ids.length ?? 0) > 1;
  const cc = data?.scope.single_cost_center;
  return createPortal(
    <div className="drawer-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside className="drawer" role="dialog" aria-modal="true" aria-label="Por que mudou">
        <div className="drawer-head">
          <div>
            <h2>Por que mudou · {data?.title ?? "…"}</h2>
            {data && <div className="muted small">Orçamento {data.target_year} × {data.base_label}</div>}
          </div>
          <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Fechar">✕</button>
        </div>
        <div className="drawer-body">
          {error && <div className="error-text">{error}</div>}
          {!data && !error && <Loading />}
          {data && (
            <>
              <div className="why-total">
                <span className="muted small">{data.base_label}</span> <strong>{fmtMoney(data.total.base)}</strong>
                <span className="muted"> → </span>
                <span className="muted small">orçamento {data.target_year}</span> <strong>{fmtMoney(data.total.proposed)}</strong>
                <div className={tone(data.total.var)}><strong>{varText(data.total.var, data.total.var_pct)}</strong></div>
              </div>
              <div className="why-cards">
                {data.by_module.map((m) => (
                  <div key={m.module} className="why-card">
                    <div className="muted small">{m.label}</div>
                    <div className="why-card-value">{fmtCompact(m.proposed)}</div>
                    <div className={`small ${tone(m.var)}`}>{Number(m.base) ? varText(m.var, m.var_pct) : `${fmtSignedMoney(m.var)} (novo)`}</div>
                  </div>
                ))}
              </div>
              {data.alerts.map((a, i) => <div key={i} className={`why-alert ${a.tone}`}>{a.text}</div>)}

              <h3 className="why-h">
                De onde vem a variação
                {data.drivers.cost_centers.length > 0 && (
                  <span className="why-toggle">
                    <button type="button" className={by === "accounts" ? "active" : ""} onClick={() => setBy("accounts")}>por conta</button>
                    <button type="button" className={by === "cost_centers" ? "active" : ""} onClick={() => setBy("cost_centers")}>por CC</button>
                  </span>
                )}
              </h3>
              <Bars rows={data.drivers[by]} />

              {data.opex.length > 0 && (
                <>
                  <h3 className="why-h">O que o gestor disse · OPEX{data.opex_count > data.opex.length ? ` (${data.opex.length} de ${data.opex_count}, maiores variações)` : ""}</h3>
                  <ul className="why-list">{data.opex.map((i) => <ItemLine key={i.key} i={i} showCc={multiCc} />)}</ul>
                </>
              )}
              {data.personnel.length > 0 && (
                <>
                  <h3 className="why-h">Pessoal · {Object.entries(data.personnel_summary).map(([k, n]) => `${n} ${k.toLowerCase()}`).join(", ")}</h3>
                  <ul className="why-list">{data.personnel.map((i) => <ItemLine key={i.key} i={i} showCc={multiCc} />)}</ul>
                </>
              )}
              {data.capex.length > 0 && (
                <>
                  <h3 className="why-h">CAPEX · {data.capex.length} solicitação(ões)</h3>
                  <ul className="why-list">{data.capex.map((i) => <ItemLine key={i.key} i={i} showCc={multiCc} />)}</ul>
                </>
              )}

              <h3 className="why-h">Perguntas</h3>
              {data.questions.length === 0 && <div className="muted small">Nenhuma pergunta sobre este recorte.</div>}
              {data.questions.map((q) => <QuestionCard key={q.id} q={q} onChange={updateQuestion} />)}
              {sent && <div className="good-text">Pergunta enviada ao gestor da área. Ela aparece nas tarefas dele e em Perguntas.</div>}
              {asking ? (
                <div className="question-form">
                  <textarea rows={3} value={question} autoFocus onChange={(e) => { setQuestion(e.target.value); setAskErr(null); }} placeholder={`O que explica a variação de ${data.title}?`} aria-label="Pergunta ao gestor" />
                  <div className="inline-controls">
                    <button type="button" className="btn btn-sm btn-primary" onClick={ask}>Enviar ao gestor</button>
                    <button type="button" className="btn btn-sm btn-ghost" onClick={() => setAsking(false)}>Cancelar</button>
                  </div>
                  {askErr && <div className="error-text">{askErr}</div>}
                </div>
              ) : null}
            </>
          )}
        </div>
        {data && (
          <div className="drawer-foot">
            {!asking && data.scope.cost_center_ids.length > 0 && <button type="button" className="btn btn-primary btn-sm" onClick={() => { setAsking(true); setSent(false); }}>Questionar o gestor</button>}
            {cc && <Link to={`/orcamento/${cc.id}`} className="btn btn-sm">Orçamento do CC</Link>}
            <button type="button" className="btn btn-sm" onClick={openJustifications}>Justificativas</button>
            <Link to="/perguntas" className="btn btn-sm btn-ghost">Todas as perguntas</Link>
          </div>
        )}
      </aside>
    </div>,
    document.body,
  );
}
