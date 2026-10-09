import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { MONTHS, fmtCompact, fmtDateTime, fmtMoney, fmtPct, fmtSignedMoney } from "../labels";
import { Badge, Loading } from "./ui";

/* Defesa do orçamento (08/10/2026): "por quê?" de uma linha do Painel — orçamento do ciclo × realizado do ano
   anterior anualizado, com a decomposição, o que o gestor justificou e as perguntas ao gestor da área. O resumo
   aparece ao passar o mouse; o clique abre o painel lateral. */

interface Driver { name: string; sub: string | null; base: string; proposed: string; var: string; var_pct: string | null; module: string | null; account_id?: number; cost_center_id?: number }
interface WhyItem {
  key: string; module: string; kind: string; entity_id: number; cost_center_id: number; cost_center: string; subject: string; group: string;
  base: string | null; proposed: string | null; var?: string; details: string[]; line_texts: string[]; text: string;
  justified: boolean; movement_type?: string;
}
/** Lançamento como estava na pergunta (snapshot) ou como está agora (current). */
export interface ItemState {
  type: string; id: number; cost_center_id: number; cost_center: string; label: string; account?: string | null;
  package?: string | null; description?: string | null; supplier?: string | null; justification?: string | null;
  total: string | null; total_label?: string; values?: string[]; movement_type?: string;
}
export interface QuestionItem {
  type: string; type_label: string; snapshot: ItemState; current: ItemState | null; deleted: boolean; moved: boolean;
  changed: boolean; link: string | null;
}
export interface Question {
  id: number; subject: string; scope: Record<string, unknown>; question: string; status: "OPEN" | "ANSWERED" | "CLOSED";
  status_label: string; asked_by: string | null; asked_at: string | null; answer: string | null; answered_by: string | null;
  answered_at: string | null; mine: boolean; can_answer: boolean; can_close: boolean; cost_center_id: number | null;
  item_type: string | null; item: QuestionItem | null;
}
/** Linha do OPEX listada no "por quê?" (GET /dashboard/why/lines). */
interface WhyLine {
  id: number; cost_center_id: number; cost_center: string | null; account: string | null; package: string | null;
  line_type: string; description: string | null; supplier: string | null; justification: string | null;
  account_justification: string | null; total: string; values: string[]; questions: number; open_questions: number;
}
/** O que vai ser questionado: tipo + id do lançamento e o contexto mostrado no formulário. */
export interface AskTarget { type: "OPEX_LINE" | "PERSONNEL_MOVEMENT" | "CAPEX_PROJECT"; id: number; label: string; context: string[]; movementType?: string }
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


/** Movimentação de pessoal: o nome do profissional fica embaçado e só aparece ao passar o mouse (dado sensível). */
export function Subject({ text, movementType }: { text: string; movementType?: string }) {
  if (!movementType) return <>{text}</>;
  const i = text.indexOf(" de ");
  if (i < 0) return <span className="blur-name" title="Passe o mouse para ver o nome">{text}</span>;
  return <>{text.slice(0, i + 4)}<span className="blur-name" title="Passe o mouse para ver o nome">{text.slice(i + 4)}</span></>;
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
      <button ref={btn} type="button" className="why-btn" onClick={(e) => { e.stopPropagation(); setHover(null); setOpen(true); }} aria-label={`Por que mudou: ${label}`}>
        por quê?
      </button>
      {hover && tip && !open && createPortal(
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

/** Formulário de pergunta sobre um lançamento, já com o contexto dele. */
export function AskForm({ target, why, onSent, onCancel }: { target: AskTarget; why?: string; onSent: (q: Question) => void; onCancel: () => void }) {
  const [question, setQuestion] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function send() {
    if (!question.trim()) return setErr("Escreva a pergunta antes de enviar");
    setBusy(true);
    setErr(null);
    try {
      onSent(await api<Question>("/questions", {
        method: "POST",
        body: JSON.stringify({ item_type: target.type, item_id: target.id, subject: target.label, question, scope: why ? { why } : {} }),
      }));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="question-form ask-form">
      <div className="item-context">
        <strong><Subject text={target.label} movementType={target.movementType} /></strong>
        {target.context.filter(Boolean).map((c) => <div key={c} className="small muted">{c}</div>)}
      </div>
      <textarea rows={3} value={question} autoFocus onChange={(e) => { setQuestion(e.target.value); setErr(null); }} placeholder="O que é este lançamento? Por que este valor?" aria-label="Pergunta ao gestor" />
      <div className="inline-controls">
        <button type="button" className="btn btn-sm btn-primary" disabled={busy} onClick={send}>Enviar ao gestor</button>
        <button type="button" className="btn btn-sm btn-ghost" onClick={onCancel}>Cancelar</button>
      </div>
      {err && <div className="error-text">{err}</div>}
    </div>
  );
}

/** Contexto do lançamento de uma pergunta: valor na pergunta × atual, CC movido, excluído e link para abrir. */
export function ItemContext({ item }: { item: QuestionItem }) {
  const s = item.snapshot;
  const c = item.current;
  const label = s.total_label ?? "Valor";
  return (
    <div className="item-context">
      <div className="small">
        <Badge tone="neutral">{item.type_label}</Badge> <strong><Subject text={s.label} movementType={s.movement_type} /></strong>
      </div>
      <div className="small muted">
        {[s.cost_center, s.account, s.package].filter(Boolean).join(" · ")}
        {s.supplier && s.supplier !== s.label ? ` · ${s.supplier}` : ""}
        {s.description && s.description !== s.label ? ` · ${s.description}` : ""}
      </div>
      <div className="small">
        {s.total !== null && <>{label} na pergunta: <strong>{fmtMoney(s.total)}</strong></>}
        {c && c.total !== null && item.changed && <> · atual: <strong>{fmtMoney(c.total)}</strong></>}
        {c && !item.changed && s.total !== null && <span className="muted"> (sem alteração)</span>}
      </div>
      {item.moved && c && <div className="small warn-text">Lançamento movido para {c.cost_center}.</div>}
      {item.deleted && <div className="small muted">O lançamento não existe mais na versão atual (excluído ou reimportado); acima, como estava na pergunta.</div>}
      {item.link && <Link to={item.link} className="small">Abrir no orçamento do CC</Link>}
    </div>
  );
}

/** Lançamentos OPEX do recorte (maiores primeiro, com busca e "mostrar mais"), cada um com "Questionar". */
function LinesSection({ qs, showCc, onAsked }: { qs: string; showCc: boolean; onAsked: (q: Question) => void }) {
  const [search, setSearch] = useState("");
  const [term, setTerm] = useState("");
  const [page, setPage] = useState<{ items: WhyLine[]; count: number; total: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [asking, setAsking] = useState<number | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [sent, setSent] = useState<number | null>(null);

  useEffect(() => {
    const t = window.setTimeout(() => setTerm(search.trim()), 300);
    return () => window.clearTimeout(t);
  }, [search]);
  useEffect(() => {
    let alive = true;
    setError(null);
    api<{ items: WhyLine[]; count: number; total: string }>(`/dashboard/why/lines?${qs}&limit=20&q=${encodeURIComponent(term)}`)
      .then((d) => alive && setPage(d))
      .catch((e: Error) => alive && setError(e.message));
    return () => { alive = false; };
  }, [qs, term]);

  async function more() {
    if (!page) return;
    try {
      const d = await api<{ items: WhyLine[]; count: number; total: string }>(`/dashboard/why/lines?${qs}&limit=20&offset=${page.items.length}&q=${encodeURIComponent(term)}`);
      setPage({ ...d, items: [...page.items, ...d.items] });
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (!page && !error) return null;
  if (page && page.count === 0 && !term) return null;
  return (
    <>
      <h3 className="why-h">Lançamentos OPEX{page ? ` · ${page.count} (${fmtMoney(page.total)})` : ""}</h3>
      <input className="why-line-search" type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Buscar por descrição, fornecedor, conta ou CC" aria-label="Buscar lançamento" />
      {error && <div className="error-text">{error}</div>}
      {page && page.items.length === 0 && <div className="muted small">Nenhum lançamento encontrado.</div>}
      {page && (
        <ul className="why-list">
          {page.items.map((l) => {
            const label = l.description || l.supplier || l.account || "Lançamento";
            const just = l.justification || l.account_justification;
            return (
              <li key={l.id} className="why-item">
                <div className="why-item-head">
                  <span>
                    <strong>{label}</strong>
                    {showCc && l.cost_center && <span className="muted small"> · {l.cost_center}</span>}
                  </span>
                  <strong className="nowrap">{fmtMoney(l.total)}</strong>
                </div>
                <div className="small muted">
                  {[l.account, l.package, l.supplier && l.supplier !== label ? l.supplier : null].filter(Boolean).join(" · ")}
                  {l.questions > 0 && <> · <Badge tone={l.open_questions ? "warn" : "info"}>{l.questions} pergunta(s)</Badge></>}
                </div>
                {just && <div className="why-quote">“{just}”{!l.justification && <span className="muted small"> (justificativa da conta)</span>}</div>}
                {open === l.id && (
                  <div className="why-months">
                    {MONTHS.map((m, k) => <span key={m}><span className="muted">{m}</span> {fmtMoney(l.values[k] ?? "0")}</span>)}
                  </div>
                )}
                <div className="inline-controls">
                  <button type="button" className="btn btn-sm btn-ghost" onClick={() => setOpen(open === l.id ? null : l.id)}>{open === l.id ? "Ocultar meses" : "Meses"}</button>
                  {asking !== l.id && <button type="button" className="btn btn-sm" onClick={() => { setAsking(l.id); setSent(null); }}>Questionar</button>}
                </div>
                {sent === l.id && <div className="good-text small">Pergunta enviada ao gestor do CC. Ela aparece nas tarefas dele e em Perguntas.</div>}
                {asking === l.id && (
                  <AskForm
                    target={{ type: "OPEX_LINE", id: l.id, label, context: [[l.cost_center, l.account].filter(Boolean).join(" · "), `Total ${fmtMoney(l.total)}`] }}
                    why={qs}
                    onCancel={() => setAsking(null)}
                    onSent={(q) => {
                      setAsking(null);
                      setSent(l.id);
                      setPage((p) => p && { ...p, items: p.items.map((x) => (x.id === l.id ? { ...x, questions: x.questions + 1, open_questions: x.open_questions + 1 } : x)) });
                      onAsked(q);
                    }}
                  />
                )}
              </li>
            );
          })}
        </ul>
      )}
      {page && page.items.length < page.count && (
        <button type="button" className="btn btn-sm btn-ghost" onClick={more}>Mostrar mais ({page.count - page.items.length})</button>
      )}
    </>
  );
}

function ItemLine({ i, showCc, onAsked, why }: { i: WhyItem; showCc: boolean; onAsked?: (q: Question) => void; why?: string }) {
  const [asking, setAsking] = useState(false);
  const [sent, setSent] = useState(false);
  const text = i.text || i.line_texts.join(" / ");
  const askType = i.kind === "PERSONNEL_MOVEMENT" || i.kind === "CAPEX_PROJECT" ? i.kind : null;
  return (
    <li className="why-item">
      <div className="why-item-head">
        <span>
          <strong><Subject text={i.subject} movementType={i.movement_type} /></strong>
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
      {askType && onAsked && !asking && (
        <div className="inline-controls">
          <button type="button" className="btn btn-sm" onClick={() => { setAsking(true); setSent(false); }}>Questionar</button>
        </div>
      )}
      {sent && <div className="good-text small">Pergunta enviada ao gestor do CC.</div>}
      {askType && onAsked && asking && (
        <AskForm
          target={{ type: askType, id: i.entity_id, label: i.subject, movementType: i.movement_type, context: [i.cost_center, ...i.details.slice(0, 2)] }}
          why={why}
          onCancel={() => setAsking(false)}
          onSent={(q) => { setAsking(false); setSent(true); onAsked(q); }}
        />
      )}
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
      {q.item && <ItemContext item={q.item} />}
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
  const navigate = useNavigate();

  useEffect(() => {
    loadWhy(qs).then(setData).catch((e: Error) => setError(e.message));
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [qs, onClose]);

  function addQuestion(q: Question) {
    setData((d) => d && { ...d, questions: [q, ...d.questions] });
    cache.delete(qs);
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
              {data.scope.cost_center_ids.length > 0 && <LinesSection qs={qs} showCc={multiCc} onAsked={addQuestion} />}
              {data.personnel.length > 0 && (
                <>
                  <h3 className="why-h">Pessoal · {Object.entries(data.personnel_summary).map(([k, n]) => `${n} ${k.toLowerCase()}`).join(", ")}</h3>
                  <ul className="why-list">{data.personnel.map((i) => <ItemLine key={i.key} i={i} showCc={multiCc} onAsked={addQuestion} why={qs} />)}</ul>
                </>
              )}
              {data.capex.length > 0 && (
                <>
                  <h3 className="why-h">CAPEX · {data.capex.length} solicitação(ões)</h3>
                  <ul className="why-list">{data.capex.map((i) => <ItemLine key={i.key} i={i} showCc={multiCc} onAsked={addQuestion} why={qs} />)}</ul>
                </>
              )}

              <h3 className="why-h">Perguntas</h3>
              {data.questions.length === 0 && <div className="muted small">Nenhuma pergunta sobre este recorte. Para perguntar, use "Questionar" no lançamento.</div>}
              {data.questions.map((q) => <QuestionCard key={q.id} q={q} onChange={updateQuestion} />)}
            </>
          )}
        </div>
        {data && (
          <div className="drawer-foot">
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
