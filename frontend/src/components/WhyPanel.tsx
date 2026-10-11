import { useCallback, useEffect, useRef, useState, type ReactNode, type Ref } from "react";
import { createPortal } from "react-dom";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { MONTHS, fmtDateTime, fmtMoney, fmtPct, fmtSignedMoney } from "../labels";
import { Badge, Loading } from "./ui";

/* Defesa do orçamento (08/10/2026): "por quê?" de uma linha do Painel — orçamento do ciclo × realizado do ano
   anterior anualizado, com a decomposição, o que o gestor justificou e as perguntas ao gestor da área. O resumo
   aparece ao passar o mouse; o clique abre o painel lateral.
   10/10/2026 (docs/08-diagnostico-por-que-mudou.md): painel lateral redesenhado — cabeçalho e rodapé fixos, uma só
   área de rolagem; resumo em números soltos (a variação é o único destaque) e composição por tipo em colunas; avisos
   curtos com ação; justificativas do gestor no centro, uma linha por conta/item com base, orçamento e variação
   alinhados e um só indicador de situação (abrir a linha mostra o texto inteiro, os lançamentos e as perguntas dela);
   origem da variação como síntese que leva à conta ou filtra pelo CC; lançamentos, rastro e perguntas sob demanda.
   Variação pelo semáforo do ciclo (`thresholds`), sempre com ▲/▼ e o valor por extenso. */

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
  account_id?: number | null; item_type: string | null; item: QuestionItem | null;
}
/** Linha do OPEX listada no "por quê?" (GET /dashboard/why/lines). */
interface WhyLine {
  id: number; cost_center_id: number; cost_center: string | null; account: string | null; package: string | null;
  line_type: string; description: string | null; supplier: string | null; justification: string | null;
  account_justification: string | null; total: string; values: string[]; questions: number; open_questions: number;
}
/** O que vai ser questionado: tipo + id do lançamento e o contexto mostrado no formulário. */
export interface AskTarget { type: "OPEX_LINE" | "PERSONNEL_MOVEMENT" | "CAPEX_PROJECT"; id: number; label: string; context: string[]; movementType?: string }
type Thresholds = { growth: number; reduction: number };
export interface WhyData {
  title: string; target_year: number; ref_year: number; closed: number | null; base_label: string;
  total: { base: string; proposed: string; var: string; var_pct: string | null };
  by_module: { module: string; label: string; base: string; proposed: string; var: string; var_pct: string | null }[];
  drivers: { accounts: Driver[]; cost_centers: Driver[] };
  alerts: { tone: "warn" | "bad"; text: string }[];
  thresholds?: Thresholds;
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

const MODULE_SHORT: Record<string, string> = { OPEX: "OPEX", CAPEX: "CAPEX", PERSONNEL: "Pessoal" };
const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
/** Troca/insere parâmetros no recorte (o último valor de um parâmetro repetido valeria no backend; aqui fica um só). */
function withParams(qs: string, extra: Record<string, string | number>): string {
  const p = new URLSearchParams(qs);
  p.delete("label");
  for (const [k, v] of Object.entries(extra)) p.set(k, String(v));
  return p.toString();
}

/** Variação % calculada só quando a base não é zero (senão o item é "novo"). */
function pctOf(base: string | null, proposed: string | null): string | null {
  const b = Number(base ?? 0);
  return b ? String((Number(proposed ?? 0) - b) / b) : null;
}

/** Semáforo do ciclo (o mesmo da tabela do Painel): fora da faixa, para cima ou para baixo, é vermelho; dentro, verde.
 * Sem % (base zero) ou sem limiares, neutro. */
function sema(pct: string | null, th?: Thresholds): "good" | "bad" | "neutral" {
  if (pct === null || !th) return "neutral";
  const n = Number(pct);
  return n > th.growth || n < -th.reduction ? "bad" : "good";
}

/** Variação com seta e texto: ▲ +R$ 1.000,00 (+12,3%) · "novo" quando não há base. */
function Var({ v, pct, th, showPct = true }: { v: string; pct: string | null; th?: Thresholds; showPct?: boolean }) {
  const n = Number(v);
  const arrow = n > 0 ? "▲" : n < 0 ? "▼" : "=";
  const k = n ? sema(pct, th) : "neutral";
  const label = n > 0 ? "aumento" : n < 0 ? "redução" : "sem variação";
  return (
    <span className={`why-var ${k}`} aria-label={`${label} de ${fmtMoney(Math.abs(n))}`}>
      <span aria-hidden="true">{arrow}</span> {fmtSignedMoney(v)}
      {showPct && n !== 0 && <span className="why-var-pct"> {pct !== null ? `(${fmtPct(pct)})` : "(novo)"}</span>}
    </span>
  );
}

/** Célula de variação das listas: valor com seta e, embaixo, o % (ou "novo"). */
function VarCell({ v, pct, th }: { v: string | null | undefined; pct: string | null; th?: Thresholds }) {
  if (v === null || v === undefined) return <span className="wd-num muted">—</span>;
  const n = Number(v);
  return (
    <span className="wd-num wd-var">
      <Var v={v} pct={pct} th={th} showPct={false} />
      {n !== 0 && <span className="wd-pct">{pct !== null ? fmtPct(pct) : "novo"}</span>}
    </span>
  );
}

const Icon = {
  alert: <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.4" /><path d="M8 4.6v4.2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /><circle cx="8" cy="11.2" r="0.9" fill="currentColor" /></svg>,
  info: <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.4" /><path d="M8 7.2v4.2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /><circle cx="8" cy="4.8" r="0.9" fill="currentColor" /></svg>,
  chevron: <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M6 3.5 10.5 8 6 12.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>,
  close: <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>,
};

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

  const pending = tip && tip !== "loading" ? tip.questions.filter((q) => q.status === "OPEN").length : 0;
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
              <strong><Var v={tip.total.var} pct={tip.total.var_pct} th={tip.thresholds} /></strong>
              <div className="muted">Orçamento {tip.target_year} × {tip.base_label.toLowerCase()}</div>
              {tip.by_module.length > 1 && tip.by_module.map((m) => (
                <div key={m.module}>{m.label}: <Var v={m.var} pct={m.var_pct} th={tip.thresholds} showPct={false} /></div>
              ))}
              {tip.drivers.accounts.filter((d) => Number(d.var)).slice(0, 3).map((d) => (
                <div key={d.name} className="muted why-tip-row">{d.name}: {fmtSignedMoney(d.var)}</div>
              ))}
              {tip.missing > 0 && <div className="error-text">{plural(tip.missing, "item sem justificativa", "itens sem justificativa")}</div>}
              {pending > 0 && <div>{plural(pending, "pergunta pendente", "perguntas pendentes")}</div>}
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
        {c && c.total !== null && item.changed && <> · atual: <strong>{fmtMoney(c.total)}</strong>
          {s.total !== null && <span className="muted"> ({fmtSignedMoney(Number(c.total) - Number(s.total))}{Number(s.total) ? `; ${fmtPct(String(Number(c.total) / Number(s.total) - 1))}` : ""})</span>}</>}
        {c && !item.changed && s.total !== null && <span className="muted"> (sem alteração)</span>}
      </div>
      {item.moved && c && <div className="small"><strong>Lançamento movido para {c.cost_center}.</strong></div>}
      {item.deleted && <div className="small muted">O lançamento não existe mais na versão atual (excluído ou reimportado); acima, como estava na pergunta.</div>}
      {item.link && <Link to={item.link} className="small">Abrir no orçamento do CC</Link>}
    </div>
  );
}

/** Contexto do lançamento numa linha só (painel lateral): o mesmo conteúdo do ItemContext, sem caixa. */
function ItemContextLine({ item }: { item: QuestionItem }) {
  const s = item.snapshot;
  const c = item.current;
  return (
    <div className="wd-qctx">
      <span>{item.type_label}: <Subject text={s.label} movementType={s.movement_type} /></span>
      {[s.cost_center, s.account].filter(Boolean).length > 0 && <span className="muted"> · {[s.cost_center, s.account].filter(Boolean).join(" · ")}</span>}
      {s.total !== null && <span className="muted"> · {s.total_label ?? "valor"} na pergunta {fmtMoney(s.total)}</span>}
      {c && c.total !== null && item.changed && <span> → atual {fmtMoney(c.total)}</span>}
      {item.moved && c && <span> · movido para {c.cost_center}</span>}
      {item.deleted && <span className="muted"> · não existe mais na versão atual</span>}
      {item.link && <> · <Link to={item.link}>abrir no orçamento do CC</Link></>}
    </div>
  );
}

type LinesPage = { items: WhyLine[]; count: number; total: string };

/** Lançamentos OPEX de um recorte (maiores primeiro): busca opcional, meses e "Questionar" em cada um. */
function LinesList({ qs, why, search: withSearch, showCc, showAccount = true, pageSize, onAsked, onLoaded }: {
  qs: string; why: string; search?: boolean; showCc: boolean; showAccount?: boolean; pageSize: number; onAsked: (q: Question) => void;
  onLoaded?: (p: LinesPage) => void;
}) {
  const [search, setSearch] = useState("");
  const [term, setTerm] = useState("");
  const [page, setPage] = useState<LinesPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [asking, setAsking] = useState<number | null>(null);
  const [months, setMonths] = useState<number | null>(null);
  const [sent, setSent] = useState<number | null>(null);

  useEffect(() => {
    const t = window.setTimeout(() => setTerm(search.trim()), 300);
    return () => window.clearTimeout(t);
  }, [search]);
  useEffect(() => {
    let alive = true;
    setError(null);
    api<LinesPage>(`/dashboard/why/lines?${qs}&limit=${pageSize}&q=${encodeURIComponent(term)}`)
      .then((d) => {
        if (!alive) return;
        setPage(d);
        if (!term) onLoaded?.(d);
      })
      .catch((e: Error) => alive && setError(e.message));
    return () => { alive = false; };
  }, [qs, term, pageSize, onLoaded]);

  async function more() {
    if (!page) return;
    try {
      const d = await api<LinesPage>(`/dashboard/why/lines?${qs}&limit=${pageSize}&offset=${page.items.length}&q=${encodeURIComponent(term)}`);
      setPage({ ...d, items: [...page.items, ...d.items] });
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (!page && !error) return <div className="muted small">Carregando lançamentos…</div>;
  return (
    <div className="wd-lines">
      {withSearch && (
        <input className="wd-search" type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Buscar por descrição, fornecedor, conta ou CC" aria-label="Buscar lançamento" />
      )}
      {error && <div className="error-text">{error}</div>}
      {page && term && <div className="muted small">{plural(page.count, "lançamento encontrado", "lançamentos encontrados")} · {fmtMoney(page.total)}</div>}
      {page && page.items.length === 0 && <div className="muted small">Nenhum lançamento {term ? "encontrado" : "neste recorte"}.</div>}
      {page && page.items.length > 0 && (
        <ul className="wd-line-list">
          {page.items.map((l) => {
            const label = l.description || l.supplier || l.account || "Lançamento";
            const just = l.justification || l.account_justification;
            const ctx = [showCc ? l.cost_center : null, showAccount ? l.account : null, showAccount ? l.package : null, l.supplier && l.supplier !== label ? l.supplier : null].filter(Boolean).join(" · ");
            return (
              <li key={l.id} className="wd-line">
                <div className="wd-line-main">
                  <span className="wd-line-name">{label}</span>
                  <span className="wd-num">{fmtMoney(l.total)}</span>
                </div>
                <div className="wd-ctx">
                  {ctx}
                  {l.questions > 0 && <span className={l.open_questions ? "wd-pending" : undefined}> · {l.open_questions ? plural(l.open_questions, "pergunta pendente", "perguntas pendentes") : plural(l.questions, "pergunta", "perguntas")}</span>}
                </div>
                {just && <TextPreview text={just} note={!l.justification ? "justificativa da conta" : undefined} />}
                {months === l.id && (
                  <div className="why-months">
                    {MONTHS.map((m, k) => <span key={m}><span className="muted">{m}</span> {fmtMoney(l.values[k] ?? "0")}</span>)}
                  </div>
                )}
                <div className="wd-line-actions">
                  <button type="button" className="wd-link" onClick={() => setMonths(months === l.id ? null : l.id)}>{months === l.id ? "Ocultar meses" : "Meses"}</button>
                  {asking !== l.id && <button type="button" className="wd-link" onClick={() => { setAsking(l.id); setSent(null); }}>Questionar</button>}
                </div>
                {sent === l.id && <div className="good-text small">Pergunta enviada ao gestor do CC. Ela aparece nas tarefas dele e em Perguntas.</div>}
                {asking === l.id && (
                  <AskForm
                    target={{ type: "OPEX_LINE", id: l.id, label, context: [[l.cost_center, l.account].filter(Boolean).join(" · "), `Total ${fmtMoney(l.total)}`] }}
                    why={why}
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
        <button type="button" className="wd-link" onClick={more}>Mostrar mais ({page.count - page.items.length})</button>
      )}
    </div>
  );
}

/** Texto de justificativa: prévia de 2 linhas com "ver mais" (o texto inteiro continua disponível). */
function TextPreview({ text, note, open: forced }: { text: string; note?: string; open?: boolean }) {
  const [open, setOpen] = useState(false);
  const long = text.length > 160;
  const full = forced || open || !long;
  return (
    <div className="wd-text">
      <span className={full ? undefined : "wd-clamp"}>{text}</span>
      {note && <span className="muted"> ({note})</span>}
      {long && !forced && <button type="button" className="wd-link" onClick={() => setOpen(!open)}>{open ? "ver menos" : "ver mais"}</button>}
    </div>
  );
}

/** Perguntas ligadas a um item (conta do CC, movimentação ou CAPEX). */
function questionsOf(i: WhyItem, list: Question[]): Question[] {
  if (i.kind === "OPEX_ACCOUNT") {
    return list.filter((q) => q.account_id === i.entity_id && (q.cost_center_id === i.cost_center_id || q.item?.snapshot.cost_center_id === i.cost_center_id));
  }
  return list.filter((q) => q.item_type === i.kind && q.item?.snapshot.id === i.entity_id);
}

type JustTab = "OPEX" | "PERSONNEL" | "CAPEX";

/** Uma linha das justificativas: nome + contexto + situação, base / orçamento / variação alinhados, prévia do texto;
 * aberta, o texto inteiro, os lançamentos da conta (OPEX) ou "Questionar" (pessoal/CAPEX) e as perguntas do item. */
function JustRow({ i, open, onToggle, showCc, th, qs, questions, onAsked, onQuestion }: {
  i: WhyItem; open: boolean; onToggle: () => void; showCc: boolean; th?: Thresholds; qs: string; questions: Question[];
  onAsked: (q: Question) => void; onQuestion: (q: Question) => void;
}) {
  const [asking, setAsking] = useState(false);
  const [sent, setSent] = useState(false);
  const text = i.text || i.line_texts.join(" / ");
  const pending = questions.filter((q) => q.status === "OPEN").length;
  const askType = i.kind === "PERSONNEL_MOVEMENT" || i.kind === "CAPEX_PROJECT" ? i.kind : null;
  const state = !i.justified ? "missing" : pending ? "pending" : "ok";
  const stateText = !i.justified
    ? `Sem justificativa${pending ? ` · ${plural(pending, "pergunta pendente", "perguntas pendentes")}` : ""}`
    : pending ? plural(pending, "pergunta pendente", "perguntas pendentes") : "Justificado";
  const ctx = [showCc ? i.cost_center : null, i.module === "OPEX" ? i.group : i.details[0]].filter(Boolean).join(" · ");
  const lineQs = withParams(qs, { account_id: i.entity_id, cost_center_id: i.cost_center_id });
  return (
    <li className={`wd-item${open ? " open" : ""}`} data-key={i.key}>
      <button type="button" className="wd-row wd-row-btn" aria-expanded={open} onClick={onToggle}>
        <span className="wd-name">
          <span className="wd-chev" aria-hidden="true">{Icon.chevron}</span>
          <span className="wd-name-text">
            <span className="wd-title-line"><Subject text={i.subject} movementType={i.movement_type} /></span>
            <span className="wd-ctx">
              <span className={`wd-state ${state}`}><span className="wd-dot" aria-hidden="true" />{stateText}</span>
              {ctx && <span> · {ctx}</span>}
            </span>
          </span>
        </span>
        <span className="wd-num wd-c-base">{i.base !== null ? fmtMoney(i.base) : "—"}</span>
        <span className="wd-num wd-c-new">{i.proposed !== null ? fmtMoney(i.proposed) : "—"}</span>
        <span className="wd-c-var">{i.module === "OPEX" ? <VarCell v={i.var} pct={pctOf(i.base, i.proposed)} th={th} /> : <span className="wd-num muted">—</span>}</span>
      </button>
      <div className="wd-item-body">
        {text
          ? <TextPreview text={text} open={open} />
          : !open && <div className="wd-text muted">Sem justificativa registrada{i.module === "OPEX" && i.details.length ? ` · ${i.details.slice(0, 2).join(" · ")}` : ""}</div>}
        {open && (
          <div className="wd-detail">
            {i.module !== "OPEX" && i.details.length > 0 && (
              <div className="wd-detail-block">
                <div className="wd-label">Detalhes</div>
                <div className="wd-ctx">{i.details.join(" · ")}</div>
              </div>
            )}
            {i.module === "OPEX" && (
              <div className="wd-detail-block">
                <div className="wd-label">Lançamentos desta conta</div>
                <LinesList qs={lineQs} why={qs} showCc={false} showAccount={false} pageSize={5} onAsked={onAsked} />
              </div>
            )}
            {askType && (
              <div className="wd-detail-block">
                {!asking && <button type="button" className="wd-link" onClick={() => { setAsking(true); setSent(false); }}>Questionar este item</button>}
                {sent && <div className="good-text small">Pergunta enviada ao gestor do CC.</div>}
                {asking && (
                  <AskForm
                    target={{ type: askType, id: i.entity_id, label: i.subject, movementType: i.movement_type, context: [i.cost_center, ...i.details.slice(0, 2)] }}
                    why={qs}
                    onCancel={() => setAsking(false)}
                    onSent={(q) => { setAsking(false); setSent(true); onAsked(q); }}
                  />
                )}
              </div>
            )}
            {questions.length > 0 && (
              <div className="wd-detail-block">
                <div className="wd-label">Perguntas sobre este item</div>
                <ul className="wd-thread-list">{questions.map((q) => <QuestionEntry key={q.id} q={q} onChange={onQuestion} compact />)}</ul>
              </div>
            )}
          </div>
        )}
      </div>
    </li>
  );
}

function QuestionEntry({ q, onChange, compact }: { q: Question; onChange: (q: Question) => void; compact?: boolean }) {
  const [answer, setAnswer] = useState("");
  const [writing, setWriting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function act(path: string, body?: object) {
    setBusy(true);
    setErr(null);
    try {
      onChange(await api<Question>(`/questions/${q.id}/${path}`, { method: "POST", body: body ? JSON.stringify(body) : undefined }));
      setAnswer("");
      setWriting(false);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  // o campo de resposta abre sob demanda ("Responder" na pendente, "Complementar resposta" na respondida)
  const showForm = q.can_answer && writing;
  const canAdd = q.can_answer && !writing;
  const canClose = q.can_close && q.status === "ANSWERED";
  const state = q.status === "OPEN" ? "pending" : q.status === "ANSWERED" ? "answered" : "closed";
  return (
    <li className={`wd-q ${state}`}>
      <div className="wd-q-head">
        <span className={`wd-state ${state}`}><span className="wd-dot" aria-hidden="true" />{q.status_label}</span>
        {!compact && <span className="wd-ctx wd-ellipsis"> · sobre {q.subject}</span>}
      </div>
      {q.item && !compact && <ItemContextLine item={q.item} />}
      <div className="wd-msg">
        <div className="wd-msg-meta">Pergunta · {q.asked_by ?? "—"} · {fmtDateTime(q.asked_at)}</div>
        <div className="wd-msg-text">{q.question}</div>
      </div>
      {q.answer && (
        <div className="wd-msg reply">
          <div className="wd-msg-meta">Resposta · {q.answered_by ?? "—"} · {fmtDateTime(q.answered_at)}</div>
          <div className="wd-msg-text">{q.answer}</div>
        </div>
      )}
      {showForm && (
        <div className="question-form wd-answer">
          <textarea rows={2} value={answer} autoFocus onChange={(e) => { setAnswer(e.target.value); setErr(null); }} placeholder={q.answer ? "Complementar a resposta" : "Sua resposta (o que é, por que mudou, premissa)"} aria-label="Resposta" />
          <div className="inline-controls">
            <button type="button" className="btn btn-sm btn-primary" disabled={busy} onClick={() => (answer.trim() ? act("answer", { answer }) : setErr("Escreva a resposta antes de enviar"))}>
              Responder
            </button>
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => setWriting(false)}>Cancelar</button>
          </div>
        </div>
      )}
      {(canAdd || canClose) && (
        <div className="wd-line-actions">
          {canAdd && <button type="button" className="wd-link" onClick={() => setWriting(true)}>{q.status === "OPEN" ? "Responder" : "Complementar resposta"}</button>}
          {canClose && <button type="button" className="wd-link" disabled={busy} onClick={() => act("close")}>Encerrar pergunta</button>}
        </div>
      )}
      {err && <div className="error-text">{err}</div>}
    </li>
  );
}

/** Cabeçalho de seção: título, nota e controles à direita. */
function SecHead({ title, note, children }: { title: string; note?: ReactNode; children?: ReactNode }) {
  return (
    <div className="wd-sec-head">
      <h3 className="wd-h">{title}{note && <span className="wd-h-note">{note}</span>}</h3>
      {children && <div className="wd-sec-tools">{children}</div>}
    </div>
  );
}

function Segmented<T extends string>({ value, options, onChange, label }: { value: T; options: { value: T; label: ReactNode }[]; onChange: (v: T) => void; label: string }) {
  return (
    <span className="wd-seg" role="tablist" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" role="tab" aria-selected={value === o.value} className={value === o.value ? "on" : ""} onClick={() => onChange(o.value)}>{o.label}</button>
      ))}
    </span>
  );
}

/** Cabeçalho das colunas de valor (mesmas colunas em composição, justificativas e origem). */
function ColHead({ first, baseLabel, newLabel, indent }: { first: string; baseLabel: string; newLabel: string; indent?: boolean }) {
  return (
    <div className={`wd-row wd-colhead${indent ? " indent" : ""}`} aria-hidden="true">
      <span>{first}</span><span className="wd-num">{baseLabel}</span><span className="wd-num">{newLabel}</span><span className="wd-num">Variação</span>
    </div>
  );
}

/** Título do recorte: o nome do item numa linha e o setor/CC (em maiúsculas, depois de " · ") na linha de baixo.
 * Código de CC ("1001 · NOME") continua junto. */
function splitTitle(title: string): [string, string | null] {
  const i = title.indexOf(" · ");
  if (i < 0) return [title, null];
  const head = title.slice(0, i);
  const rest = title.slice(i + 3);
  if (/^[\d.\-/]+$/.test(head.trim()) || rest !== rest.toUpperCase() || !/[A-ZÀ-Ý]/.test(rest)) return [title, null];
  return [head, rest];
}

/** Painel lateral "Por que mudou?" (portal, sobre a página). */
export function WhyDrawer({ qs, onClose }: { qs: string; onClose: () => void }) {
  const [data, setData] = useState<WhyData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [by, setBy] = useState<"accounts" | "cost_centers">("accounts");
  const [driversAll, setDriversAll] = useState(false);
  const [tab, setTab] = useState<JustTab>("OPEX");
  const [onlyMissing, setOnlyMissing] = useState(false);
  const [ccFilter, setCcFilter] = useState<{ id: number; name: string } | null>(null);
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [justAll, setJustAll] = useState(false);
  const [linesOpen, setLinesOpen] = useState(false);
  const [lines, setLines] = useState<LinesPage | null>(null);
  const [qAll, setQAll] = useState(false);
  const [menu, setMenu] = useState(false);
  const menuRef = useRef(false);
  const bodyRef = useRef<HTMLDivElement>(null);
  const justRef = useRef<HTMLElement>(null);
  const linesRef = useRef<HTMLElement>(null);
  const qRef = useRef<HTMLElement>(null);
  const navigate = useNavigate();
  menuRef.current = menu;

  useEffect(() => {
    loadWhy(qs).then((d) => {
      setData(d);
      // aba inicial: o primeiro tipo com item sem justificativa (senão, o primeiro com itens)
      const lists: [JustTab, WhyItem[]][] = [["OPEX", d.opex], ["PERSONNEL", d.personnel], ["CAPEX", d.capex]];
      const first = lists.find(([, l]) => l.some((x) => !x.justified)) ?? lists.find(([, l]) => l.length > 0);
      if (first) setTab(first[0]);
    }).catch((e: Error) => setError(e.message));
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (menuRef.current) setMenu(false);
      else onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [qs, onClose]);

  // resumo dos lançamentos do recorte (o mesmo GET da lista, só a primeira página)
  const baseQs = withParams(qs, {});
  useEffect(() => {
    if (!data || data.scope.cost_center_ids.length === 0) return;
    let alive = true;
    api<LinesPage>(`/dashboard/why/lines?${qs}&limit=1`).then((d) => alive && setLines(d)).catch(() => alive && setLines(null));
    return () => { alive = false; };
  }, [qs, data]);
  const onLinesLoaded = useCallback((p: LinesPage) => setLines((cur) => cur ?? p), []);

  function addQuestion(q: Question) {
    setData((d) => d && { ...d, questions: [q, ...d.questions] });
    cache.delete(qs);
  }
  function updateQuestion(q: Question) {
    setData((d) => d && { ...d, questions: d.questions.map((x) => (x.id === q.id ? q : x)) });
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
  const scrollTo = (el: HTMLElement | null) => window.setTimeout(() => el?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
  /** Abre um item das justificativas e rola até ele. */
  function focusItem(i: WhyItem) {
    setTab(i.module as JustTab);
    setOnlyMissing(false);
    setCcFilter(null);
    setJustAll(true);
    setOpenKey(i.key);
    window.setTimeout(() => bodyRef.current?.querySelector(`[data-key="${CSS.escape(i.key)}"]`)?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
  }
  /** Ação "Questionar": abre os lançamentos (ou as justificativas de pessoal/CAPEX) e rola até eles. */
  function goAsk() {
    if (!data) return;
    if ((lines?.count ?? 0) > 0) {
      setLinesOpen(true);
      scrollTo(linesRef.current);
      return;
    }
    const t = data.personnel.length ? "PERSONNEL" : data.capex.length ? "CAPEX" : null;
    if (t) setTab(t);
    scrollTo(justRef.current);
  }
  function showMissing() {
    const lists: [JustTab, WhyItem[]][] = data ? [["OPEX", data.opex], ["PERSONNEL", data.personnel], ["CAPEX", data.capex]] : [];
    const t = lists.find(([, l]) => l.some((x) => !x.justified));
    if (t) setTab(t[0]);
    setOnlyMissing(true);
    setCcFilter(null);
    scrollTo(justRef.current);
  }

  const [titleMain, titleSub] = splitTitle(data?.title ?? "…");
  const multiCc = (data?.scope.cost_center_ids.length ?? 0) > 1;
  const cc = data?.scope.single_cost_center;
  const th = data?.thresholds;
  const pending = data ? data.questions.filter((q) => q.status === "OPEN").length : 0;
  const lineCount = lines?.count ?? 0;
  const canAsk = lineCount > 0 || !!data?.personnel.length || !!data?.capex.length;
  const askFirst = !!data && canAsk && (data.missing > 0 || pending > 0);
  const traceTo = `/rastro?${baseQs}`;
  const baseShort = data ? data.base_label.split(" (")[0] : "";
  const baseCol = data ? `Realizado ${data.ref_year} an.` : "";
  const newCol = data ? `Orçamento ${data.target_year}` : "";

  // justificativas: abas por tipo, filtro "só sem justificativa" e filtro por CC (vindo da origem da variação)
  const lists: Record<JustTab, WhyItem[]> = data ? { OPEX: data.opex, PERSONNEL: data.personnel, CAPEX: data.capex } : { OPEX: [], PERSONNEL: [], CAPEX: [] };
  const tabs = (Object.keys(lists) as JustTab[]).filter((t) => lists[t].length > 0);
  const curTab = tabs.includes(tab) ? tab : tabs[0];
  const tabItems = curTab ? lists[curTab] : [];
  const filtered = tabItems.filter((i) => (!onlyMissing || !i.justified) && (!ccFilter || i.cost_center_id === ccFilter.id));
  const JUST_LIMIT = 6;
  const shownItems = justAll ? filtered : filtered.slice(0, JUST_LIMIT);
  const tabNote =
    curTab === "OPEX" && data && data.opex_count > data.opex.length
      ? `${data.opex.length} de ${data.opex_count} contas, maiores variações`
      : curTab === "PERSONNEL" && data
        ? Object.entries(data.personnel_summary).map(([k, n]) => `${n} ${k.toLowerCase()}`).join(", ")
        : "";

  // origem da variação: síntese (5 maiores) que leva à conta ou filtra o CC
  const drivers = data ? data.drivers[by].filter((r) => Number(r.var)) : [];
  const shownDrivers = driversAll ? drivers : drivers.slice(0, 5);
  const maxDriver = Math.max(1, ...drivers.map((d) => Math.abs(Number(d.var))));
  function pickDriver(d: Driver) {
    if (!data) return;
    if (d.cost_center_id !== undefined && by === "cost_centers") {
      setCcFilter({ id: d.cost_center_id, name: d.name });
      setOnlyMissing(false);
      setJustAll(false);
      scrollTo(justRef.current);
      return;
    }
    const match = data.opex.filter((i) => i.entity_id === d.account_id);
    if (match.length) focusItem(match[0]);
  }

  // perguntas em ordem cronológica: pendentes sempre visíveis + as 3 mais recentes; o resto em "ver histórico"
  const qSorted = data ? [...data.questions].sort((a, b) => (a.asked_at ?? "").localeCompare(b.asked_at ?? "") || a.id - b.id) : [];
  const recent = new Set(qSorted.slice(-3).map((q) => q.id));
  const qShown = qAll ? qSorted : qSorted.filter((q) => q.status === "OPEN" || recent.has(q.id));
  const qHidden = qSorted.length - qShown.length;
  const qCount = (s: Question["status"]) => qSorted.filter((q) => q.status === s).length;
  const qSummary = [
    qCount("OPEN") && plural(qCount("OPEN"), "pendente", "pendentes"),
    qCount("ANSWERED") && plural(qCount("ANSWERED"), "respondida", "respondidas"),
    qCount("CLOSED") && plural(qCount("CLOSED"), "encerrada", "encerradas"),
  ].filter(Boolean).join(" · ");

  const totalPct = data?.total.var_pct ?? null;
  const totalSema = data && Number(data.total.var) ? sema(totalPct, th) : "neutral";

  // ações secundárias do rodapé (no celular, no menu "Mais")
  const secondary: { key: string; node: ReactNode }[] = [];
  if (!askFirst && canAsk) secondary.push({ key: "ask", node: <button type="button" onClick={() => { setMenu(false); goAsk(); }}>Questionar</button> });
  secondary.push({ key: "trace", node: <Link to={traceTo}>Ver rastro</Link> });
  if (cc) secondary.push({ key: "cc", node: <Link to={`/orcamento/${cc.id}`}>Orçamento do CC</Link> });
  secondary.push({ key: "just", node: <button type="button" onClick={openJustifications}>Justificativas</button> });
  secondary.push({ key: "q", node: <Link to="/perguntas">Todas as perguntas</Link> });

  return createPortal(
    <div className="drawer-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside className="drawer wd" role="dialog" aria-modal="true" aria-label="Por que mudou?">
        <header className="wd-head">
          <div className="wd-head-main">
            <div className="wd-eyebrow">Por que mudou?</div>
            <h2 className="wd-title">{titleMain}</h2>
            {titleSub && <div className="wd-title-sub">{titleSub}</div>}
            {data && <div className="wd-period">Orçamento {data.target_year} × {data.base_label} · comparação entre anos</div>}
            {data && (data.opex_count + data.personnel.length + data.capex.length > 0 || pending > 0) && (
              <div className="wd-status">
                {data.missing > 0
                  ? <button type="button" className="wd-state missing" onClick={showMissing}><span className="wd-dot" aria-hidden="true" />{data.missing} sem justificativa</button>
                  : <span className="wd-state ok"><span className="wd-dot" aria-hidden="true" />Tudo justificado</span>}
                {pending > 0 && (
                  <button type="button" className="wd-state pending" onClick={() => scrollTo(qRef.current)}><span className="wd-dot" aria-hidden="true" />{plural(pending, "pergunta pendente", "perguntas pendentes")}</button>
                )}
              </div>
            )}
          </div>
          <button type="button" className="wd-close" onClick={onClose} aria-label="Fechar">{Icon.close}</button>
        </header>

        <div className="wd-body" ref={bodyRef}>
          {error && <div className="error-text">{error}</div>}
          {!data && !error && <Loading />}
          {data && (
            <>
              {/* resumo: base, orçamento e a variação (único número em destaque) */}
              <section className="wd-sec wd-summary" aria-label="Resumo financeiro">
                <div className="wd-figs">
                  <div className="wd-fig"><span className="wd-label">{baseShort}</span><span className="wd-fig-num">{fmtMoney(data.total.base)}</span></div>
                  <div className="wd-fig"><span className="wd-label">Orçamento {data.target_year}</span><span className="wd-fig-num">{fmtMoney(data.total.proposed)}</span></div>
                  <div className="wd-fig wd-fig-main">
                    <span className="wd-label">Variação</span>
                    <span className={`wd-fig-num why-var ${totalSema}`}>
                      <span aria-hidden="true">{Number(data.total.var) > 0 ? "▲" : Number(data.total.var) < 0 ? "▼" : "="}</span> {fmtSignedMoney(data.total.var)}
                    </span>
                    <span className="wd-fig-sub">
                      {Number(data.total.var) !== 0 && (totalPct !== null ? fmtPct(totalPct) : "novo")}
                      {totalSema !== "neutral" && th && (
                        <> · {totalSema === "good"
                          ? `dentro da faixa do ciclo (+${Math.round(th.growth * 100)}% / -${Math.round(th.reduction * 100)}%)`
                          : Number(data.total.var) > 0 ? `acima do limite do ciclo (+${Math.round(th.growth * 100)}%)` : `queda além do limite do ciclo (-${Math.round(th.reduction * 100)}%)`}</>
                      )}
                    </span>
                  </div>
                </div>
                {data.by_module.length > 1 && (
                  <div className="wd-table" role="table" aria-label="Composição por tipo">
                    <ColHead first="Composição" baseLabel={baseCol} newLabel={newCol} />
                    {data.by_module.map((m) => (
                      <div key={m.module} className="wd-row" role="row">
                        <span className="wd-name"><span className="wd-name-text"><span className="wd-title-line">{m.label}</span></span></span>
                        <span className="wd-num wd-c-base">{fmtMoney(m.base)}</span>
                        <span className="wd-num wd-c-new">{fmtMoney(m.proposed)}</span>
                        <span className="wd-c-var"><VarCell v={m.var} pct={m.var_pct} th={th} /></span>
                      </div>
                    ))}
                  </div>
                )}
              </section>

              {/* avisos curtos com ação */}
              {data.alerts.length > 0 && (
                <ul className="wd-notes" aria-label="Avisos">
                  {data.alerts.map((a, k) => (
                    <li key={k} className={`wd-note ${a.tone}`}>
                      <span className="wd-note-icon">{a.tone === "bad" ? Icon.alert : Icon.info}</span>
                      <span className="wd-note-text">{a.text}</span>
                      {a.tone === "bad" && data.missing > 0 && <button type="button" className="wd-link" onClick={showMissing}>Ver itens</button>}
                      {a.tone === "warn" && canAsk && <button type="button" className="wd-link" onClick={goAsk}>Questionar</button>}
                    </li>
                  ))}
                </ul>
              )}

              {/* justificativas do gestor: a seção central */}
              {tabs.length > 0 && curTab && (
                <section className="wd-sec" ref={justRef as Ref<HTMLElement>}>
                  <SecHead title="Justificativas do gestor" note={tabNote || undefined}>
                    {tabs.length > 1 && (
                      <Segmented
                        label="Tipo"
                        value={curTab}
                        onChange={(t) => { setTab(t); setJustAll(false); setOpenKey(null); }}
                        options={tabs.map((t) => ({ value: t, label: <>{MODULE_SHORT[t]} <span className="wd-seg-n">{t === "OPEX" ? data.opex_count : lists[t].length}</span></> }))}
                      />
                    )}
                    {tabItems.some((i) => !i.justified) && (
                      <label className="wd-check"><input type="checkbox" checked={onlyMissing} onChange={(e) => { setOnlyMissing(e.target.checked); setJustAll(false); }} /> Só sem justificativa</label>
                    )}
                  </SecHead>
                  {ccFilter && (
                    <div className="wd-filter">Centro de custo: <strong>{ccFilter.name}</strong> <button type="button" className="wd-link" onClick={() => setCcFilter(null)}>limpar</button></div>
                  )}
                  <ColHead indent first={curTab === "OPEX" ? "Conta" : curTab === "PERSONNEL" ? "Movimentação" : "Solicitação"} baseLabel={curTab === "OPEX" ? baseCol : curTab === "PERSONNEL" ? "Salário atual" : "Base"} newLabel={curTab === "OPEX" ? newCol : curTab === "PERSONNEL" ? "Novo salário" : "Valor"} />
                  {filtered.length === 0 && <div className="wd-empty">Nenhum item com esse filtro.</div>}
                  <ul className="wd-items">
                    {shownItems.map((i) => (
                      <JustRow
                        key={i.key}
                        i={i}
                        open={openKey === i.key}
                        onToggle={() => setOpenKey(openKey === i.key ? null : i.key)}
                        showCc={multiCc}
                        th={th}
                        qs={qs}
                        questions={questionsOf(i, data.questions)}
                        onAsked={addQuestion}
                        onQuestion={updateQuestion}
                      />
                    ))}
                  </ul>
                  {filtered.length > JUST_LIMIT && (
                    <button type="button" className="wd-link wd-more" onClick={() => setJustAll(!justAll)}>{justAll ? "ver menos" : `ver todas (${filtered.length})`}</button>
                  )}
                </section>
              )}

              {/* origem da variação: síntese; clicar leva à conta ou filtra pelo CC */}
              {drivers.length > 0 && (
                <section className="wd-sec">
                  <SecHead title="Origem da variação" note={by === "accounts" ? "maiores variações · clique para abrir a conta" : "maiores variações · clique para filtrar as justificativas"}>
                    {data.drivers.cost_centers.length > 0 && (
                      <Segmented label="Agrupar" value={by} onChange={(v) => { setBy(v); setDriversAll(false); }} options={[{ value: "accounts", label: "Por conta" }, { value: "cost_centers", label: "Por CC" }]} />
                    )}
                  </SecHead>
                  <ul className="wd-items wd-drvs">
                    {shownDrivers.map((d) => {
                      const target = by === "cost_centers" ? true : data.opex.some((i) => i.entity_id === d.account_id);
                      const share = Math.abs(Number(d.var)) / maxDriver;
                      const cells = (
                        <>
                          <span className="wd-drv-name" title={`${d.name}${d.sub ? ` · ${d.sub}` : ""} — ${fmtMoney(d.base)} → ${fmtMoney(d.proposed)}`}>
                            {d.name}{d.sub && <span className="wd-ctx"> · {d.sub}</span>}
                          </span>
                          <span className="wd-drv-bar" aria-hidden="true"><span style={{ width: `${Math.max(2, Math.round(share * 100))}%` }} /></span>
                          <span className="wd-drv-var"><Var v={d.var} pct={d.var_pct} th={th} showPct={false} /><span className="wd-pct"> {d.var_pct !== null ? fmtPct(d.var_pct) : "novo"}</span></span>
                        </>
                      );
                      return (
                        <li key={d.name} className="wd-item">
                          {target
                            ? <button type="button" className="wd-drv wd-row-btn" onClick={() => pickDriver(d)} title={by === "cost_centers" ? "Filtrar as justificativas por este CC" : "Abrir a justificativa desta conta"}>{cells}</button>
                            : <div className="wd-drv">{cells}</div>}
                        </li>
                      );
                    })}
                  </ul>
                  {drivers.length > 5 && (
                    <button type="button" className="wd-link wd-more" onClick={() => setDriversAll(!driversAll)}>{driversAll ? "ver menos" : `ver todas (${drivers.length})`}</button>
                  )}
                </section>
              )}

              {/* investigação: lançamentos e rastro sob demanda */}
              <section className="wd-sec" ref={linesRef as Ref<HTMLElement>}>
                <SecHead title="Investigar" />
                <div className="wd-inv">
                  {lines && lines.count > 0 && (
                    <div className="wd-inv-row">
                      <button type="button" className="wd-inv-btn" aria-expanded={linesOpen} onClick={() => setLinesOpen(!linesOpen)}>
                        <span className={`wd-chev${linesOpen ? " on" : ""}`} aria-hidden="true">{Icon.chevron}</span>
                        <span className="wd-inv-title">Lançamentos OPEX</span>
                        <span className="wd-inv-sum">{plural(lines.count, "lançamento", "lançamentos")} · <span className="wd-num">{fmtMoney(lines.total)}</span></span>
                      </button>
                      {linesOpen && <LinesList qs={qs} why={qs} search showCc={multiCc} pageSize={20} onAsked={addQuestion} onLoaded={onLinesLoaded} />}
                    </div>
                  )}
                  <div className="wd-inv-row">
                    <Link to={traceTo} className="wd-inv-btn">
                      <span className="wd-chev" aria-hidden="true">{Icon.chevron}</span>
                      <span className="wd-inv-title">Rastro</span>
                      <span className="wd-inv-sum">do total do recorte até o lançamento</span>
                    </Link>
                  </div>
                </div>
              </section>

              <section className="wd-sec" ref={qRef as Ref<HTMLElement>}>
                <SecHead title={`Perguntas${qSorted.length ? ` (${qSorted.length})` : ""}`} note={qSummary || undefined} />
                {qSorted.length === 0 && <div className="wd-empty">Nenhuma pergunta sobre este recorte. Para perguntar, use "Questionar" no lançamento.</div>}
                {qHidden > 0 && <button type="button" className="wd-link wd-more" onClick={() => setQAll(true)}>ver histórico ({plural(qHidden, "anterior", "anteriores")})</button>}
                {qShown.length > 0 && <ul className="wd-thread-list">{qShown.map((q) => <QuestionEntry key={q.id} q={q} onChange={updateQuestion} />)}</ul>}
                {qAll && qSorted.length > 3 && <button type="button" className="wd-link wd-more" onClick={() => setQAll(false)}>ocultar histórico</button>}
              </section>
            </>
          )}
        </div>

        {data && (
          <footer className="wd-foot">
            {askFirst && <button type="button" className="btn btn-sm btn-primary" onClick={goAsk}>Questionar</button>}
            <nav className="wd-foot-links" aria-label="Ações">
              {secondary.map((s) => <span key={s.key}>{s.node}</span>)}
            </nav>
            <span className="wd-menu-wrap">
              <button type="button" className="wd-foot-more" aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu((m) => !m)}>Mais</button>
              {menu && (
                <>
                  <span className="why-menu-shield" onMouseDown={() => setMenu(false)} aria-hidden="true" />
                  <div className="why-menu" role="menu">{secondary.map((s) => <span key={s.key} role="menuitem">{s.node}</span>)}</div>
                </>
              )}
            </span>
          </footer>
        )}
      </aside>
    </div>,
    document.body,
  );
}
