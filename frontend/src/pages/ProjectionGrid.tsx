import { Fragment, useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { FilterBar } from "../components/FilterBar";
import { Alert, BackButton, Card, Empty, Loading, Modal, PageHeader, SearchBox, Stat, lines, useLoad } from "../components/ui";
import { MONTHS, NATURE_LABELS, fmtDateTime, fmtMoney, fmtMoneyInput, fmtSignedMoney, parseMoney } from "../labels";
import { usePersistentState } from "../persist";
import { monthName, type LastSaved } from "./Projection";

/* Grade da projeção do CC (05/11/2026): conta × mês. Meses fechados = realizado (azul, travado); meses abertos
   editáveis, pré-preenchidos com a projeção vigente ou, se a conta não tem projeção, com o orçado (sugestão).
   Antes de gravar, a comparação mostra a projeção antiga × a nova, célula a célula. */

type Origin = "R" | "P" | "G" | "O" | "";

interface GridRow {
  account_id: number; code: string; name: string; nature: string; package: string | null;
  actual: string[]; projection: string[]; budget: string[]; values: string[]; origin: Origin[]; suggested: boolean;
}

interface Grid {
  year: number; cost_center: { id: number; code: string; name: string; company: string };
  closed: number; open_months: number[]; version_id: number | null; version_number: number | null;
  last_saved: LastSaved | null; can_edit: boolean; rows: GridRow[];
}

interface Change {
  account_id: number; code: string; name: string; nature: string; month: number;
  origin_before: Origin; before: string; after: string; difference: string;
}

interface Comparison {
  changes: Change[]; accepted_suggestions: number;
  by_month: { month: number; before: string; after: string }[];
  by_nature: { nature: string; before: string; after: string }[];
  totals: { actual: string; budget: string; projection_before: string; projection_after: string; closing_before: string; closing_after: string; difference: string };
  version_number?: number; saved?: number;
}

interface HistoryItem {
  id: number; at: string; by: string | null; comment: string | null;
  cells: { cell: string; before: string | null; after: string | null }[]; total_before: string | null; total_after: string | null;
}

interface Suggestion { account_id: number; code: string; name: string; nature: string; months: number[]; total: number }

interface AccountOption { id: number; code: string; name: string; nature: string; package: string | null }

const NATURES = ["OPEX", "PESSOAL", "CAPEX"];
const ORIGIN_LABEL: Record<Origin, string> = {
  R: "Realizado", P: "Projeção importada (planilha da área)", G: "Projeção gravada na tela", O: "Orçado sugerido — ainda não é projeção", "": "Sem valor",
};
const key = (acc: number, m: number) => `${acc}:${m}`;
const num = (v: string | number) => Number(v) || 0;
const same = (a: number, b: number) => Math.abs(a - b) < 0.005;

/** Texto do campo → número (aceita negativo e "1.234,56"); null = inválido. */
function parseCell(text: string): number | null {
  const t = text.trim();
  if (!t) return 0;
  const neg = t.startsWith("-");
  const r = parseMoney(neg ? t.slice(1) : t);
  if (r === undefined) return null;
  return (neg ? -1 : 1) * Number(r ?? 0);
}

export default function ProjectionGrid() {
  const { ccId } = useParams();
  const grid = useLoad(() => api<Grid>(`/projection/${ccId}`), [ccId]);
  const hist = useLoad(() => api<HistoryItem[]>(`/projection/${ccId}/history`), [ccId]);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [added, setAdded] = useState<GridRow[]>([]);
  const [nature, setNature] = useState("");
  const [show, setShow] = useState("");
  const [query, setQuery] = useState("");
  const [preview, setPreview] = useState<Comparison | null>(null);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<Comparison | null>(null);
  const [adding, setAdding] = useState(false);
  const [view, setView] = usePersistentState<"focus" | "year">("projecao.vista", "focus");
  const [useBudget, setUseBudget] = useState<boolean | null>(null); // resposta a "usar o orçado como projeção?"

  const g = grid.data;
  const rows = useMemo(() => [...(g?.rows ?? []), ...added], [g, added]);
  const open = g?.open_months ?? [];

  const valueOf = (r: GridRow, m: number): number => {
    const k = key(r.account_id, m);
    if (k in edits) return parseCell(edits[k]) ?? num(r.values[m - 1]);
    return num(r.values[m - 1]);
  };
  const isDirty = (r: GridRow, m: number) => key(r.account_id, m) in edits && !same(valueOf(r, m), num(r.values[m - 1]));
  const invalid = Object.entries(edits).filter(([, t]) => parseCell(t) === null).length;
  const dirtyCount = rows.reduce((n, r) => n + open.filter((m) => isDirty(r, m)).length, 0);
  const suggestedOpen = rows.reduce((n, r) => n + open.filter((m) => r.origin[m - 1] === "O" && !same(valueOf(r, m), 0)).length, 0);
  const pending = dirtyCount > 0 || added.length > 0;

  useEffect(() => {
    if (!pending) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [pending]);

  if (grid.error) return <Alert>{grid.error}</Alert>;
  if (!g) return <Loading />;

  const closingOf = (r: GridRow) => r.actual.slice(0, g.closed).reduce((s, v) => s + num(v), 0) + open.reduce((s, m) => s + valueOf(r, m), 0);
  const budgetOf = (r: GridRow) => r.budget.reduce((s, v) => s + num(v), 0);
  const q = query.trim().toUpperCase();
  const visible = rows.filter((r) =>
    (!nature || r.nature === nature) &&
    (!q || `${r.code} ${r.name}`.toUpperCase().includes(q)) &&
    (show === "all" ? true
      : show === "changed" ? open.some((m) => isDirty(r, m))
      : show === "suggested" ? r.origin.includes("O")
      : show === "values" ? !same(closingOf(r), 0) || !same(budgetOf(r), 0)
      // padrão: contas que importam para a projeção (valor ou orçado nos meses abertos, alteradas ou acrescentadas)
      : added.includes(r) || open.some((m) => key(r.account_id, m) in edits || !same(valueOf(r, m), 0) || !same(num(r.budget[m - 1]), 0))),
  );
  const hiddenCount = rows.length - visible.length;

  const setCell = (r: GridRow, m: number, text: string) => setEdits((e) => ({ ...e, [key(r.account_id, m)]: text }));
  const clearSuggestions = () => setEdits((e) => {
    const next = { ...e };
    rows.forEach((r) => open.forEach((m) => { if (r.origin[m - 1] === "O") next[key(r.account_id, m)] = ""; }));
    return next;
  });
  const reset = () => { setEdits({}); setAdded([]); setError(null); };

  // célula com o orçado sugerido que o gestor não tocou: só vira projeção se ele disser "sim" na revisão
  const untouchedSuggestion = (r: GridRow, m: number) => r.origin[m - 1] === "O" && !(key(r.account_id, m) in edits) && !same(valueOf(r, m), 0);
  const suggestions: Suggestion[] = rows
    .map((r) => {
      const months = open.filter((m) => untouchedSuggestion(r, m));
      return { account_id: r.account_id, code: r.code, name: r.name, nature: r.nature, months, total: months.reduce((s, m) => s + valueOf(r, m), 0) };
    })
    .filter((s) => s.months.length > 0);

  const payload = (withBudget: boolean) => ({
    version_id: g.version_id,
    values: rows.flatMap((r) => open.map((m) => ({
      account_id: r.account_id, month: m, amount: (!withBudget && untouchedSuggestion(r, m) ? 0 : valueOf(r, m)).toFixed(2),
    }))),
  });

  async function compareWith(withBudget: boolean) {
    setError(null);
    setBusy(true);
    try {
      setPreview(await api<Comparison>(`/projection/${ccId}/compare`, { method: "POST", body: JSON.stringify(payload(withBudget)) }));
    } catch (err) {
      setError((err as Error).message);
      setPreview(null);
    } finally {
      setBusy(false);
    }
  }

  function review() {
    setUseBudget(null);
    compareWith(true);
  }

  function choose(withBudget: boolean) {
    setUseBudget(withBudget);
    compareWith(withBudget);
  }

  async function save() {
    setBusy(true);
    try {
      const body = { ...payload(useBudget !== false), comment: comment || null };
      const result = await api<Comparison>(`/projection/${ccId}`, { method: "PUT", body: JSON.stringify(body) });
      setDone(result);
      setPreview(null);
      setComment("");
      reset();
      grid.reload();
      hist.reload();
    } catch (err) {
      setError((err as Error).message);
      setPreview(null);
    } finally {
      setBusy(false);
    }
  }

  // totais por natureza e geral (o que está na tela, inclusive o digitado)
  const totals = (list: GridRow[]) => {
    const months = Array.from({ length: 12 }, (_, i) => list.reduce((s, r) => s + (i + 1 <= g.closed ? num(r.actual[i]) : valueOf(r, i + 1)), 0));
    const closing = months.reduce((s, v) => s + v, 0);
    const budget = list.reduce((s, r) => s + budgetOf(r), 0);
    return { months, closing, budget };
  };
  const all = totals(rows);
  const actualYtd = all.months.slice(0, g.closed).reduce((s, v) => s + v, 0);
  const projected = all.closing - actualYtd;
  const vigente = rows.reduce((s, r) => s + open.reduce((t, m) => t + num(r.projection[m - 1]), 0), 0);
  const suggestedValue = rows.reduce((s, r) => s + open.reduce((t, m) => t + (r.origin[m - 1] === "O" && !(key(r.account_id, m) in edits) ? valueOf(r, m) : 0), 0), 0);
  const projHint = same(projected, vigente)
    ? "vigente"
    : `na tela · vigente ${fmtMoney(vigente)}${!same(suggestedValue, 0) ? ` · inclui ${fmtMoney(suggestedValue)} de orçado sugerido` : ""}`;
  const groups = NATURES.map((n) => ({ nature: n, rows: visible.filter((r) => r.nature === n) })).filter((x) => x.rows.length);
  // foco (06/11/2026, "dar um foco nas contas que serão alteradas"): os meses fechados viram uma coluna só (realizado
  // acumulado + média mensal) e os meses abertos ficam logo ao lado da conta; "Ano completo" mostra mês a mês
  const focus = view === "focus";
  const closedLabel = g.closed ? (g.closed === 1 ? MONTHS[0] : `${MONTHS[0]}–${MONTHS[g.closed - 1]}`) : "";
  const closedCols = focus ? (g.closed ? 2 : 0) : g.closed;
  const cols = 1 + closedCols + open.length + 3;
  const closedCellsOf = (values: number[], strong = false) => {
    const wrap = (v: string) => (strong ? <strong>{v}</strong> : v);
    if (!g.closed) return null;
    if (focus) {
      const ytd = values.slice(0, g.closed).reduce((s, v) => s + v, 0);
      return (
        <>
          <td className="right cell-actual">{wrap(fmtMoneyInput(String(ytd)) || "—")}</td>
          <td className="right cell-actual muted-col">{wrap(fmtMoneyInput(String(ytd / g.closed)) || "—")}</td>
        </>
      );
    }
    return values.slice(0, g.closed).map((v, i) => <td key={i} className="right cell-actual month-cell">{wrap(fmtMoneyInput(String(v)) || "—")}</td>);
  };

  return (
    <>
      <PageHeader
        title={`Projeção ${g.year} · ${g.cost_center.code}`}
        subtitle={`${g.cost_center.name} · empresa ${g.cost_center.company} · realizado fechado até ${monthName(g.closed)}${g.last_saved ? ` · última gravação ${fmtDateTime(g.last_saved.at)} por ${g.last_saved.by ?? "—"}` : ""}`}
        actions={<BackButton fallback="/projecao" />}
      />
      {done && (
        <Alert tone="good">
          Projeção gravada: {done.saved} célula(s) alterada(s), versão {done.version_number} da projeção da empresa. Já vale no Painel, no Rastro e em Projetos.
        </Alert>
      )}
      {error && <Alert>{error}</Alert>}
      {open.length === 0 && <Alert tone="info">Todos os meses de {g.year} já têm realizado: não há o que projetar.</Alert>}
      <div className="stats">
        <Stat label={`Realizado até ${monthName(g.closed)}`} value={fmtMoney(actualYtd)} tone="info" />
        <Stat label="Projeção dos meses abertos" value={fmtMoney(projected)} hint={lines(projHint, pending ? "alterações ainda não gravadas" : null)} />
        <Stat label="Fechamento do ano" value={fmtMoney(all.closing)} hint={lines("realizado + projeção")} />
        <Stat label={`Orçado ${g.year}`} value={fmtMoney(all.budget)} tone="budget" hint={lines(`desvio ${fmtSignedMoney(all.closing - all.budget)}`)} />
      </div>

      <FilterBar
        fields={[
          { key: "nature", label: "Natureza", value: nature, onChange: setNature, options: [{ value: "", label: "OPEX, Pessoal e CAPEX" }, ...NATURES.map((n) => ({ value: n, label: NATURE_LABELS[n] ?? n }))] },
          { key: "show", label: "Contas", value: show, onChange: setShow, options: [{ value: "", label: "Com valor ou orçado nos meses abertos" }, { value: "all", label: "Todas as contas" }, { value: "values", label: "Com valor no ano" }, { value: "suggested", label: "Com sugestão do orçado" }, { value: "changed", label: "Só alteradas agora" }] },
        ]}
        onReset={() => { setNature(""); setShow(""); setQuery(""); }}
        resetCount={(nature ? 1 : 0) + (show ? 1 : 0) + (query ? 1 : 0)}
        extra={<SearchBox value={query} onChange={setQuery} placeholder="Buscar conta" />}
      />

      <Card
        title="Conta × mês"
        actions={g.can_edit && open.length > 0 ? (
          <div className="page-actions">
            <button className="btn btn-ghost btn-sm" onClick={() => setAdding(true)}>Acrescentar conta</button>
            {suggestedOpen > 0 && <button className="btn btn-ghost btn-sm" onClick={clearSuggestions} title="Zera as células com o orçado sugerido">Zerar sugestões do orçado</button>}
            {pending && <button className="btn btn-ghost btn-sm" onClick={reset}>Desfazer</button>}
            <button className="btn btn-primary btn-sm" disabled={busy || invalid > 0 || rows.length === 0} onClick={review}>
              {busy ? "Comparando…" : "Revisar e gravar"}
            </button>
          </div>
        ) : undefined}
      >
        <div className="proj-legend small">
          <span><span className="cell-actual">■</span> Realizado (travado)</span>
          <span><span className="cell-origin origin-P">P</span> projeção importada</span>
          <span><span className="cell-origin origin-G">G</span> gravada na tela</span>
          <span><span className="cell-origin origin-O">O</span> orçado sugerido (conta sem projeção)</span>
          <span><span className="cell-dirty-sample" /> alterado agora</span>
          {!g.can_edit && <strong>Perfil de consulta: só leitura.</strong>}
          {g.closed > 0 && (
            <span className="proj-view" role="group" aria-label="Exibição dos meses">
              <button type="button" className="btn btn-ghost btn-sm" aria-pressed={focus} onClick={() => setView("focus")}>Foco nos meses abertos</button>
              <button type="button" className="btn btn-ghost btn-sm" aria-pressed={!focus} onClick={() => setView("year")}>Ano completo</button>
            </span>
          )}
        </div>
        {hiddenCount > 0 && !show && !nature && !query && (
          <p className="muted small proj-hidden">
            {hiddenCount} conta(s) só com realizado em meses fechados estão ocultas.{" "}
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setShow("all")}>Mostrar todas</button>
          </p>
        )}
        {invalid > 0 && <Alert tone="warn">{invalid} valor(es) inválido(s): use números como 1.234,56 ou -500.</Alert>}
        {rows.length === 0 ? (
          <Empty>Este centro de custo não tem realizado, projeção nem orçado em {g.year}. Use "Acrescentar conta".</Empty>
        ) : (
          <div className="table-wrap grid-wrap">
            <table className={`table grid-table proj-grid ${focus ? "proj-focus" : ""}`}>
              <thead>
                <tr>
                  <th className="sticky-col">Conta</th>
                  {focus
                    ? g.closed > 0 && (
                      <>
                        <th className="right th-closed">Realizado {closedLabel}</th>
                        <th className="right th-closed">Média/mês</th>
                      </>
                    )
                    : MONTHS.slice(0, g.closed).map((m) => <th key={m} className="right th-closed">{m}</th>)}
                  {open.map((m) => <th key={m} className="right th-open">{MONTHS[m - 1]}{focus && <span className="th-sub">projeção</span>}</th>)}
                  <th className="right">Fechamento</th>
                  <th className="right">Orçado {g.year}</th>
                  <th className="right">Desvio</th>
                </tr>
              </thead>
              <tbody>
                {groups.map(({ nature: n, rows: list }) => {
                  const t = totals(list);
                  return (
                    <Fragment key={n}>
                      <tr className="group-row"><td className="sticky-col" colSpan={1}>{NATURE_LABELS[n] ?? n}</td><td colSpan={cols - 1} /></tr>
                      {list.map((r) => {
                        const closing = closingOf(r);
                        const budget = budgetOf(r);
                        return (
                          <tr key={r.account_id}>
                            <td className="sticky-col">
                              <strong>{r.code}</strong> · {r.name}
                              {r.package && <div className="muted small">{r.package}</div>}
                            </td>
                            {closedCellsOf(r.actual.map(num))}
                            {open.map((m) => {
                              const i = m - 1;
                              const k = key(r.account_id, m);
                              const origin = r.origin[i];
                              const text = k in edits ? edits[k] : (same(num(r.values[i]), 0) ? "" : fmtMoneyInput(r.values[i]));
                              const bad = k in edits && parseCell(edits[k]) === null;
                              const dirty = isDirty(r, m);
                              const showOrigin = !dirty && origin && !(k in edits);
                              return (
                                <td key={m} className="month-cell open-cell">
                                  <div className="cell-in" title={`${monthName(m)}: ${dirty ? `alterado (antes ${fmtMoney(r.values[i])})` : ORIGIN_LABEL[origin]} · orçado ${fmtMoney(r.budget[i])}`}>
                                    <input
                                      className={`money ${bad ? "invalid" : ""} ${dirty ? "dirty" : ""} ${showOrigin && origin === "O" ? "suggested" : ""}`}
                                      inputMode="decimal"
                                      aria-label={`${r.code} ${monthName(m)}`}
                                      value={text}
                                      placeholder="0"
                                      disabled={!g.can_edit}
                                      onChange={(e) => setCell(r, m, e.target.value)}
                                      onBlur={(e) => { const v = parseCell(e.target.value); if (k in edits && v !== null && e.target.value.trim()) setCell(r, m, fmtMoneyInput(String(v))); }}
                                    />
                                    {showOrigin ? <span className={`cell-origin origin-${origin}`}>{origin}</span> : <span className="cell-origin" />}
                                  </div>
                                  {focus && !same(num(r.budget[i]), 0) && <div className="cell-ref">orç. {fmtMoneyInput(r.budget[i])}</div>}
                                </td>
                              );
                            })}
                            <td className="right"><strong>{fmtMoney(closing)}</strong></td>
                            <td className="right cell-budget">{fmtMoney(budget)}</td>
                            <td className={`right ${closing - budget > 0.005 ? "error-text" : ""}`}>{fmtSignedMoney(closing - budget)}</td>
                          </tr>
                        );
                      })}
                      <tr className="sub-row">
                        <td className="sticky-col"><strong>Total {NATURE_LABELS[n] ?? n}</strong></td>
                        {closedCellsOf(t.months)}
                        {open.map((m) => <td key={m} className="right open-cell">{fmtMoneyInput(String(t.months[m - 1])) || "—"}</td>)}
                        <td className="right"><strong>{fmtMoney(t.closing)}</strong></td>
                        <td className="right cell-budget">{fmtMoney(t.budget)}</td>
                        <td className="right">{fmtSignedMoney(t.closing - t.budget)}</td>
                      </tr>
                    </Fragment>
                  );
                })}
                {(() => {
                  const t = totals(visible);
                  return (
                    <tr className="sub-row total-row">
                      <td className="sticky-col"><strong>Total{visible.length !== rows.length ? " (filtrado)" : ""}</strong></td>
                      {closedCellsOf(t.months, true)}
                      {open.map((m) => <td key={m} className="right open-cell"><strong>{fmtMoneyInput(String(t.months[m - 1])) || "—"}</strong></td>)}
                      <td className="right"><strong>{fmtMoney(t.closing)}</strong></td>
                      <td className="right cell-budget"><strong>{fmtMoney(t.budget)}</strong></td>
                      <td className="right"><strong>{fmtSignedMoney(t.closing - t.budget)}</strong></td>
                    </tr>
                  );
                })()}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <HistoryCard data={hist.data} error={hist.error} />

      {preview && (
        <CompareModal
          data={preview}
          suggestions={suggestions}
          useBudget={useBudget}
          onChoose={choose}
          comment={comment}
          setComment={setComment}
          busy={busy}
          onClose={() => setPreview(null)}
          onConfirm={save}
        />
      )}
      {adding && (
        <AddAccountModal
          existing={new Set(rows.map((r) => r.account_id))}
          onClose={() => setAdding(false)}
          onPick={(a) => {
            setAdded((list) => [...list, {
              account_id: a.id, code: a.code, name: a.name, nature: a.nature, package: a.package,
              actual: Array(12).fill("0"), projection: Array(12).fill("0"), budget: Array(12).fill("0"),
              values: Array(12).fill("0"), origin: Array(12).fill(""), suggested: false,
            }]);
            setAdding(false);
          }}
        />
      )}
    </>
  );
}

/** Projeção antiga × nova antes de gravar: resumo, por mês, por natureza e cada célula alterada. */
function CompareModal({ data, suggestions, useBudget, onChoose, comment, setComment, busy, onClose, onConfirm }: {
  data: Comparison; suggestions: Suggestion[]; useBudget: boolean | null; onChoose: (withBudget: boolean) => void;
  comment: string; setComment: (v: string) => void; busy: boolean; onClose: () => void; onConfirm: () => void;
}) {
  const t = data.totals;
  const nothing = data.changes.length === 0;
  const mustChoose = suggestions.length > 0 && useBudget === null;
  const suggestedTotal = suggestions.reduce((s, x) => s + x.total, 0);
  return (
    <Modal
      title="Projeção atual × nova projeção"
      wide
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>Voltar e ajustar</button>
          <button className="btn btn-primary" disabled={busy || nothing || mustChoose} title={mustChoose ? "Responda se usa o orçado nas contas sem projeção" : undefined} onClick={onConfirm}>
            {busy ? "Aguarde…" : "Confirmar e gravar"}
          </button>
        </>
      }
    >
      {suggestions.length > 0 && (
        <div className={`proj-ask ${useBudget === null ? "proj-ask-open" : ""}`}>
          <p>
            <strong>{suggestions.length} conta(s) não tiveram projeção</strong> nos meses abertos. A grade sugeriu o orçado
            ({fmtMoney(suggestedTotal)}). <strong>Deseja usar o orçamento como projeção?</strong>
          </p>
          <div className="table-wrap proj-ask-list">
            <table className="table">
              <thead><tr><th>Conta</th><th>Natureza</th><th>Meses</th><th className="right">Orçado sugerido</th></tr></thead>
              <tbody>
                {suggestions.map((s) => (
                  <tr key={s.account_id}>
                    <td><strong>{s.code}</strong> · {s.name}</td>
                    <td>{NATURE_LABELS[s.nature] ?? s.nature}</td>
                    <td>{s.months.map(monthName).join(", ")}</td>
                    <td className="right cell-budget">{fmtMoney(s.total)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="page-actions">
            <button className={`btn btn-sm ${useBudget === true ? "btn-primary" : "btn-ghost"}`} disabled={busy} onClick={() => onChoose(true)}>
              Sim, usar o orçado como projeção
            </button>
            <button className={`btn btn-sm ${useBudget === false ? "btn-primary" : "btn-ghost"}`} disabled={busy} onClick={() => onChoose(false)}>
              Não, deixar essas contas sem projeção
            </button>
          </div>
          {useBudget !== null && (
            <p className="muted small">
              {useBudget ? "O orçado dessas contas entra como projeção e aparece na lista abaixo." : "Essas contas ficam sem projeção; a comparação abaixo já está sem elas."}
            </p>
          )}
        </div>
      )}
      {nothing ? (
        <Alert tone="info">Nada mudou em relação à projeção vigente.</Alert>
      ) : (
        <>
          <div className="stats">
            <Stat label="Projeção atual" value={fmtMoney(t.projection_before)} />
            <Stat label="Nova projeção" value={fmtMoney(t.projection_after)} />
            <Stat label="Diferença" value={fmtSignedMoney(t.difference)} tone={num(t.difference) > 0 ? "bad" : "good"} hint={lines(`${data.changes.length} célula(s) alterada(s)`)} />
            <Stat label="Fechamento do ano" value={fmtMoney(t.closing_after)} tone="budget" hint={lines(`antes ${fmtMoney(t.closing_before)}`, `orçado ${fmtMoney(t.budget)}`)} />
          </div>
          <h3 className="modal-sub">Por mês</h3>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Mês</th><th className="right">Atual</th><th className="right">Nova</th><th className="right">Diferença</th></tr></thead>
              <tbody>
                {data.by_month.map((r) => {
                  const d = num(r.after) - num(r.before);
                  return (
                    <tr key={r.month} className={same(d, 0) ? "muted" : ""}>
                      <td>{monthName(r.month)}</td>
                      <td className="right">{fmtMoney(r.before)}</td>
                      <td className="right"><strong>{fmtMoney(r.after)}</strong></td>
                      <td className="right">{fmtSignedMoney(d)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {data.by_nature.length > 1 && (
            <>
              <h3 className="modal-sub">Por natureza</h3>
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>Natureza</th><th className="right">Atual</th><th className="right">Nova</th><th className="right">Diferença</th></tr></thead>
                  <tbody>
                    {data.by_nature.map((r) => (
                      <tr key={r.nature}>
                        <td>{NATURE_LABELS[r.nature] ?? r.nature}</td>
                        <td className="right">{fmtMoney(r.before)}</td>
                        <td className="right"><strong>{fmtMoney(r.after)}</strong></td>
                        <td className="right">{fmtSignedMoney(num(r.after) - num(r.before))}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
          <h3 className="modal-sub">O que foi alterado ({data.changes.length})</h3>
          <div className="table-wrap compare-cells">
            <table className="table">
              <thead><tr><th>Conta</th><th>Mês</th><th>Antes era</th><th className="right">Atual</th><th className="right">Nova</th><th className="right">Diferença</th></tr></thead>
              <tbody>
                {data.changes.slice(0, 400).map((c) => (
                  <tr key={`${c.account_id}:${c.month}`}>
                    <td><strong>{c.code}</strong> · {c.name}</td>
                    <td>{monthName(c.month)}</td>
                    <td className="small">{c.origin_before === "O" ? "orçado sugerido" : c.origin_before === "G" ? "gravada na tela" : c.origin_before === "P" ? "projeção importada" : "sem projeção"}</td>
                    <td className="right">{fmtMoney(c.before)}</td>
                    <td className="right"><strong>{fmtMoney(c.after)}</strong></td>
                    <td className={`right ${num(c.difference) > 0 ? "error-text" : ""}`}>{fmtSignedMoney(c.difference)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data.changes.length > 400 && <p className="muted small">Mostrando 400 de {data.changes.length} células; todas serão gravadas.</p>}
          <label className="proj-comment">
            Comentário (opcional, vai para o registro)
            <textarea rows={2} maxLength={2000} value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Ex.: revisão após o fechamento de outubro" />
          </label>
          <p className="muted small">Ao gravar, a nova projeção vale na hora (Painel, Rastro e Projetos). A anterior fica no histórico e a alteração vai para a auditoria.</p>
        </>
      )}
    </Modal>
  );
}

function HistoryCard({ data, error }: { data: HistoryItem[] | null; error: string | null }) {
  return (
    <Card title="Histórico de gravações">
      {error ? <Alert>{error}</Alert> : !data ? <Loading /> : data.length === 0 ? (
        <Empty>Nenhuma gravação na tela ainda. A projeção vigente veio da importação.</Empty>
      ) : (
        <div className="proj-history">
          {data.map((h) => (
            <details key={h.id}>
              <summary>
                <strong>{fmtDateTime(h.at)}</strong> · {h.by ?? "—"} · {h.cells.length} célula(s)
                {h.total_before !== null && h.total_after !== null && <> · projeção {fmtMoney(h.total_before)} → <strong>{fmtMoney(h.total_after)}</strong></>}
                {h.comment && <div className="muted small">{h.comment}</div>}
              </summary>
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>Conta · mês</th><th className="right">Antes</th><th className="right">Depois</th><th className="right">Diferença</th></tr></thead>
                  <tbody>
                    {h.cells.map((c) => (
                      <tr key={c.cell}>
                        <td>{c.cell}</td>
                        <td className="right">{fmtMoney(c.before ?? 0)}</td>
                        <td className="right"><strong>{fmtMoney(c.after ?? 0)}</strong></td>
                        <td className="right">{fmtSignedMoney(num(c.after ?? 0) - num(c.before ?? 0))}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          ))}
        </div>
      )}
    </Card>
  );
}

function AddAccountModal({ existing, onClose, onPick }: { existing: Set<number>; onClose: () => void; onPick: (a: AccountOption) => void }) {
  const { data, error } = useLoad(() => api<AccountOption[]>("/projection/accounts"), []);
  const [q, setQ] = useState("");
  const list = (data ?? []).filter((a) => !existing.has(a.id) && (!q.trim() || `${a.code} ${a.name}`.toUpperCase().includes(q.trim().toUpperCase())));
  return (
    <Modal title="Acrescentar conta" onClose={onClose}>
      <SearchBox value={q} onChange={setQ} placeholder="Código ou nome da conta" />
      {error ? <Alert>{error}</Alert> : !data ? <Loading /> : (
        <div className="table-wrap add-account-list">
          <table className="table">
            <tbody>
              {list.slice(0, 60).map((a) => (
                <tr key={a.id} className="clickable" onClick={() => onPick(a)}>
                  <td><strong>{a.code}</strong> · {a.name}<div className="muted small">{NATURE_LABELS[a.nature] ?? a.nature}{a.package ? ` · ${a.package}` : ""}</div></td>
                </tr>
              ))}
            </tbody>
          </table>
          {list.length === 0 && <Empty>Nenhuma conta encontrada.</Empty>}
        </div>
      )}
    </Modal>
  );
}
