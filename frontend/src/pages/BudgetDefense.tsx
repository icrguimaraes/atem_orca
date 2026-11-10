import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, download } from "../api";
import { FilterBar } from "../components/FilterBar";
import { Alert, Badge, Card, Empty, Loading, PageHeader, SearchBox, useLoad } from "../components/ui";
import { fmtDate, fmtInt, fmtMoney, fmtMoneyInput, fmtPct, fmtSignedMoney, parseMoney, type Tone } from "../labels";
import { usePersistentState } from "../persist";

/* Defesa do orçamento (10/11/2026): o VP apresenta e defende o orçamento contra o que o Planejamento cortou —
   base (Revisão 1 · VP) × proposta (Revisão 2 · Planejamento). Números lidos do sistema: as duas versões, o realizado
   do ano anterior e do ano de referência, as justificativas do gestor, as perguntas e os apontamentos. O sistema
   sugere a prioridade de pedir de volta; a Controladoria ajusta (auditado). Exporta Excel e imprime em PDF. */

interface Tag { key: string; label: string }
interface DefItem {
  key: string; cost_center_id: number; cost_center: string; module: string; group: string; group_label: string;
  account: string; account_name: string; package: string | null; base: string; proposal: string; difference: string;
  difference_pct: string | null; actual_prev: string; actual_ref: string; kind: "CUT" | "INCREASE"; absent: boolean;
  justifications: string[]; questions_open: number; tags: Tag[]; reasons: string[];
  suggested_priority?: string; priority: string | null; priority_label?: string; suggested_argument?: string;
  argument: string | null; request_amount: string; request_custom?: boolean; edited: boolean; updated_at?: string | null;
}
interface Group {
  group: string; label: string; base: string; proposal: string; difference: string; difference_pct: string | null;
  actual_prev: string; actual_ref: string; cut: string; ask: string;
}
interface Version { id: number; label: string; status: string; is_base: boolean; total?: string }
interface Point { key: string; kind: string; kind_label: string; cost_center: string; subject: string; message: string; amount: string | null }
interface OpenQuestion {
  id: number; subject: string; question: string; answer: string | null; status: string; status_label: string;
  cost_center: string | null; asked_at: string | null; version: string;
}
interface DefData {
  target_year: number;
  years: { prev: number; ref: number; prev_label: string; ref_label: string };
  base: Version & { total: string }; proposal: Version & { total: string };
  difference: string; difference_pct: string | null; groups: Group[];
  totals: { cuts: string; cuts_count: number; increases: string; increases_count: number; actual_prev: string; actual_ref: string };
  items: DefItem[];
  ask: { keys: string[]; by_priority: { priority: string; label: string; count: number; cut: string; ask: string }[]; total: string; proposal_with_ask: string };
  open_points: { planning: Point[]; questions: OpenQuestion[] };
  versions: Version[]; priorities: { value: string; label: string }[]; can_edit: boolean; terms: string[];
}

const PRIORITY_TONE: Record<string, Tone> = { HIGH: "bad", MEDIUM: "warn", LOW: "neutral", ACCEPT: "good" };
const SHOW = [
  { value: "", label: "Só os cortes" },
  { value: "INCREASE", label: "Só os aumentos" },
  { value: "ALL", label: "Cortes e aumentos" },
];

function fold(s: string) {
  return s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}
const sum = (xs: string[]) => xs.reduce((a, b) => a + Number(b), 0);

/** Cartão do resumo: filete na cor da série (roxo = revisão base, verde = orçamento proposto). */
function Kpi({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: "rev" | "budget" | "cut" | "ask" }) {
  return (
    <div className={`def-kpi${tone ? ` def-kpi-${tone}` : ""}`}>
      <span className="def-kpi-label">{label}</span>
      <span className="def-kpi-value">{value}</span>
      {hint && <span className="def-kpi-hint">{hint}</span>}
    </div>
  );
}

function Groups({ data }: { data: DefData }) {
  const g = data.groups;
  const total = (k: keyof Group) => sum(g.map((x) => x[k] as string));
  const tBase = total("base");
  const tProp = total("proposal");
  return (
    <div className="table-wrap">
      <table className="table def-groups">
        <thead>
          <tr>
            <th>Módulo</th>
            <th className="right"><span className="def-sw actual" />{data.years.prev_label}</th>
            <th className="right"><span className="def-sw actual" />{data.years.ref_label}</th>
            <th className="right"><span className="def-sw rev" />{data.base.label}</th>
            <th className="right"><span className="def-sw budget" />{data.proposal.label}</th>
            <th className="right">Diferença</th>
            <th className="right">Pedir de volta</th>
          </tr>
        </thead>
        <tbody>
          {g.map((x) => (
            <tr key={x.group}>
              <td><strong>{x.label}</strong></td>
              <td className="right cell-actual">{fmtMoney(x.actual_prev)}</td>
              <td className="right cell-actual">{fmtMoney(x.actual_ref)}</td>
              <td className="right cell-rev">{fmtMoney(x.base)}</td>
              <td className="right cell-budget">{fmtMoney(x.proposal)}</td>
              <td className="right"><strong>{fmtSignedMoney(x.difference)}</strong><div className="muted small">{fmtPct(x.difference_pct)}</div></td>
              <td className="right">{Number(x.ask) ? fmtMoney(x.ask) : "—"}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <td><strong>Total</strong></td>
            <td className="right cell-actual">{fmtMoney(total("actual_prev"))}</td>
            <td className="right cell-actual">{fmtMoney(total("actual_ref"))}</td>
            <td className="right cell-rev">{fmtMoney(tBase)}</td>
            <td className="right cell-budget">{fmtMoney(tProp)}</td>
            <td className="right"><strong>{fmtSignedMoney(tProp - tBase)}</strong><div className="muted small">{fmtPct(data.difference_pct)}</div></td>
            <td className="right"><strong>{fmtMoney(data.ask.total)}</strong></td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

function AskBack({ data, byKey }: { data: DefData; byKey: Map<string, DefItem> }) {
  const items = data.ask.keys.map((k) => byKey.get(k)).filter(Boolean) as DefItem[];
  const accepted = data.ask.by_priority.find((p) => p.priority === "ACCEPT");
  return (
    <>
      <div className="def-priorities">
        {data.ask.by_priority.filter((p) => p.priority !== "ACCEPT").map((p) => (
          <div key={p.priority} className="def-priority">
            <Badge tone={PRIORITY_TONE[p.priority]}>{p.label}</Badge>
            <strong>{fmtMoney(p.ask)}</strong>
            <span className="muted small">{fmtInt(p.count)} item(ns) · corte de {fmtMoney(p.cut)}</span>
          </div>
        ))}
        {accepted && (
          <div className="def-priority">
            <Badge tone="good">Cortes aceitos</Badge>
            <strong>{fmtMoney(accepted.cut)}</strong>
            <span className="muted small">{fmtInt(accepted.count)} item(ns) fora do pedido</span>
          </div>
        )}
      </div>
      {items.length === 0 ? (
        <Empty>Nenhum corte para pedir de volta.</Empty>
      ) : (
        <div className="table-wrap">
          <table className="table def-ask">
            <thead>
              <tr>
                <th>Prioridade</th>
                <th>Conta · centro de custo</th>
                <th className="right">Corte</th>
                <th className="right">Pedir de volta</th>
                <th>Argumento</th>
              </tr>
            </thead>
            <tbody>
              {items.map((i) => (
                <tr key={i.key}>
                  <td><Badge tone={PRIORITY_TONE[i.priority!]}>{i.priority_label}</Badge></td>
                  <td>
                    <a href={`#def-${i.key}`} className="link">{i.account_name}</a>
                    <div className="muted small">{i.account} · {i.cost_center}</div>
                  </td>
                  <td className="right">{fmtMoney(-Number(i.difference))}</td>
                  <td className="right"><strong>{fmtMoney(i.request_amount)}</strong></td>
                  <td className="def-arg small">{i.argument || i.suggested_argument}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <td colSpan={2}><strong>Total do pedido</strong></td>
                <td className="right">{fmtMoney(sum(items.map((i) => String(-Number(i.difference)))))}</td>
                <td className="right"><strong>{fmtMoney(data.ask.total)}</strong></td>
                <td className="small muted">Proposta + pedido: {fmtMoney(data.ask.proposal_with_ask)}</td>
              </tr>
            </tfoot>
          </table>
        </div>
      )}
    </>
  );
}

function Editor({ item, data, onSaved }: { item: DefItem; data: DefData; onSaved: () => void }) {
  const cut = -Number(item.difference);
  const [priority, setPriority] = useState(item.priority ?? "LOW");
  const [amount, setAmount] = useState(item.request_custom ? fmtMoneyInput(item.request_amount) : "");
  const [argument, setArgument] = useState(item.argument ?? "");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "good" | "bad"; text: string } | null>(null);

  async function send(body: Record<string, unknown>, ok: string) {
    setBusy(true);
    setMsg(null);
    try {
      await api("/defense/items", {
        method: "PUT",
        body: JSON.stringify({
          base_version_id: data.base.id, version_id: data.proposal.id,
          cost_center_id: item.cost_center_id, module: item.module, account: item.account, ...body,
        }),
      });
      setMsg({ tone: "good", text: ok });
      onSaved();
    } catch (e) {
      setMsg({ tone: "bad", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }
  function save() {
    const parsed = amount.trim() ? parseMoney(amount) : null;
    if (parsed === undefined) {
      setMsg({ tone: "bad", text: "Valor a pedir inválido" });
      return;
    }
    send({ priority, request_amount: parsed, argument }, "Salvo e registrado na auditoria");
  }

  return (
    <div className="def-edit">
      <div className="def-edit-row">
        <label>
          <span className="small muted">Prioridade</span>
          <select value={priority} onChange={(e) => setPriority(e.target.value)} disabled={!data.can_edit}>
            {data.priorities.map((p) => (
              <option key={p.value} value={p.value}>{p.label}{p.value === item.suggested_priority ? " (sugerida)" : ""}</option>
            ))}
          </select>
        </label>
        <label>
          <span className="small muted">Pedir de volta (R$)</span>
          <input
            className="money"
            inputMode="decimal"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            placeholder={`${fmtMoneyInput(String(cut))} (o corte inteiro)`}
            disabled={!data.can_edit || priority === "ACCEPT"}
          />
        </label>
      </div>
      <label className="def-edit-arg">
        <span className="small muted">Argumento para o VP (vazio = o do sistema)</span>
        <textarea rows={3} value={argument} onChange={(e) => setArgument(e.target.value)} placeholder={item.suggested_argument} disabled={!data.can_edit} />
      </label>
      {data.can_edit ? (
        <div className="inline-controls">
          <button type="button" className="btn btn-sm btn-primary" disabled={busy} onClick={save}>{busy ? "Salvando…" : "Salvar"}</button>
          {item.edited && (
            <button type="button" className="btn btn-sm btn-ghost" disabled={busy} onClick={() => send({ reset: true }, "Voltou à sugestão do sistema")}>
              Voltar à sugestão
            </button>
          )}
          {msg && <span className={msg.tone === "good" ? "good-text" : "error-text"}>{msg.text}</span>}
        </div>
      ) : (
        <p className="muted small">A {data.proposal.label} está congelada: a defesa fica só para consulta.</p>
      )}
    </div>
  );
}

function ItemCard({ item, data, onSaved }: { item: DefItem; data: DefData; onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const cut = item.kind === "CUT";
  return (
    <li className={`def-item${cut ? "" : " def-item-up"}`} id={`def-${item.key}`}>
      <div className="def-item-head">
        <div className="def-item-title">
          <strong>{item.account_name}</strong>
          <div className="muted small">{item.account} · {item.group_label} · {item.cost_center}</div>
        </div>
        {cut && item.priority && (
          <div className="def-item-prio">
            <Badge tone={PRIORITY_TONE[item.priority]}>{item.priority_label}</Badge>
            <span className="muted small">{item.edited ? "ajustada" : "sugerida"}</span>
          </div>
        )}
      </div>
      <div className="def-values">
        <span><span className="muted">Real. {data.years.prev}</span> <span className="cell-actual">{fmtMoney(item.actual_prev)}</span></span>
        <span><span className="muted">Real. {data.years.ref}</span> <span className="cell-actual">{fmtMoney(item.actual_ref)}</span></span>
        <span><span className="muted">Base</span> <span className="cell-rev">{fmtMoney(item.base)}</span></span>
        <span><span className="muted">Proposta</span> <span className="cell-budget">{fmtMoney(item.proposal)}</span></span>
        <span><strong>{fmtSignedMoney(item.difference)}</strong> <span className="muted">{fmtPct(item.difference_pct)}</span></span>
        {cut && <span><span className="muted">Pedir</span> <strong>{fmtMoney(item.request_amount)}</strong></span>}
      </div>
      {item.tags.length > 0 && (
        <div className="def-tags">{item.tags.map((t) => <span key={t.key} className={`def-tag def-tag-${t.key}`}>{t.label}</span>)}</div>
      )}
      {cut && (
        <p className="def-arg">{item.argument || item.suggested_argument}</p>
      )}
      {cut && (
        <button type="button" className="btn btn-sm def-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? "Fechar" : "Ver detalhes e ajustar"}
        </button>
      )}
      {open && (
        <div className="def-detail">
          {item.reasons.length > 0 && (
            <div>
              <div className="small muted">Por que o sistema sugere {item.suggested_priority ? data.priorities.find((p) => p.value === item.suggested_priority)?.label.toLowerCase() : ""}</div>
              <ul className="def-reasons">{item.reasons.map((r, n) => <li key={n}>{r}</li>)}</ul>
            </div>
          )}
          {item.justifications.length > 1 && (
            <div>
              <div className="small muted">Todas as justificativas do gestor</div>
              <ul className="def-reasons">{item.justifications.map((j, n) => <li key={n}>{j}</li>)}</ul>
            </div>
          )}
          <Editor item={item} data={data} onSaved={onSaved} />
        </div>
      )}
    </li>
  );
}

function OpenPoints({ data }: { data: DefData }) {
  const { planning, questions } = data.open_points;
  if (!planning.length && !questions.length) return <Empty>Nada em aberto.</Empty>;
  return (
    <div className="def-open">
      {planning.length > 0 && (
        <div>
          <div className="card-head"><h3>Apontamentos da revisão do Planejamento</h3><Link to="/apontamentos" className="btn btn-sm btn-ghost">Apontamentos</Link></div>
          <ul className="def-open-list">
            {planning.map((p) => (
              <li key={p.key}>
                <div><Badge tone="warn">{p.kind_label}</Badge> <strong>{p.subject}</strong>{p.amount && <> · {fmtMoney(p.amount)}</>}</div>
                <div className="muted small">{p.cost_center}</div>
                <div className="small">{p.message}</div>
              </li>
            ))}
          </ul>
        </div>
      )}
      {questions.length > 0 && (
        <div>
          <div className="card-head"><h3>Perguntas em aberto</h3><Link to="/perguntas" className="btn btn-sm btn-ghost">Perguntas</Link></div>
          <ul className="def-open-list">
            {questions.map((q) => (
              <li key={q.id}>
                <div><Badge tone={q.status === "OPEN" ? "bad" : "info"}>{q.status_label}</Badge> <strong>{q.subject}</strong></div>
                <div className="muted small">{q.cost_center ?? "—"} · {q.version} · {fmtDate(q.asked_at)}</div>
                <div className="small">{q.question}</div>
                {q.answer && <div className="small muted">Resposta: {q.answer}</div>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export default function BudgetDefense() {
  const [baseId, setBaseId] = usePersistentState("defesa.base", "");
  const [propId, setPropId] = usePersistentState("defesa.proposta", "");
  const [group, setGroup] = usePersistentState("defesa.modulo", "");
  const [show, setShow] = usePersistentState("defesa.mostrar", "");
  const [prio, setPrio] = usePersistentState("defesa.prioridade", "");
  const [search, setSearch] = usePersistentState("defesa.busca", "");
  const [dlError, setDlError] = useState<string | null>(null);
  const qs = new URLSearchParams();
  if (baseId) qs.set("base_version_id", baseId);
  if (propId) qs.set("version_id", propId);
  const query = qs.toString() ? `?${qs}` : "";
  const { data, error, reload, loading } = useLoad(() => api<DefData>(`/defense${query}`), [query]);
  const byKey = useMemo(() => new Map((data?.items ?? []).map((i) => [i.key, i])), [data]);

  if (error) {
    return (
      <>
        <PageHeader title="Defesa do orçamento" />
        <Alert>{error}</Alert>
        {(baseId || propId) && <button type="button" className="btn" onClick={() => { setBaseId(""); setPropId(""); }}>Voltar às versões padrão</button>}
      </>
    );
  }
  if (!data) return <Loading />;

  const needle = fold(search.trim());
  const shown = data.items.filter((i) =>
    (show === "ALL" || (show === "INCREASE" ? i.kind === "INCREASE" : i.kind === "CUT")) &&
    (!group || i.group === group) &&
    (!prio || i.priority === prio) &&
    (!needle || fold(`${i.account} ${i.account_name} ${i.cost_center} ${i.justifications.join(" ")}`).includes(needle)));
  const versionOptions = data.versions.map((v) => ({ value: String(v.id), label: `${v.label}${v.status === "FROZEN" ? " (congelada)" : " (em elaboração)"}` }));
  const cutPct = data.difference_pct;

  return (
    <div className={`def-page${loading ? " is-loading" : ""}`}>
      <PageHeader
        title={`Defesa do orçamento ${data.target_year}`}
        subtitle={`${data.base.label} (base) × ${data.proposal.label} (proposta) · números do sistema: as duas versões, o realizado, as justificativas do gestor e as perguntas.`}
        actions={
          <>
            <button
              type="button"
              className="btn"
              onClick={() => { setDlError(null); download(`/defense/export.xlsx${query}`, `Defesa_Orcamento_${data.target_year}.xlsx`).catch((e) => setDlError((e as Error).message)); }}
            >
              Exportar Excel
            </button>
            <button type="button" className="btn" onClick={() => window.print()}>Imprimir / PDF</button>
          </>
        }
      />
      {dlError && <Alert>{dlError}</Alert>}
      <p className="def-print-note">Impresso em {new Date().toLocaleString("pt-BR")} · {data.base.label} × {data.proposal.label}</p>

      <div className="def-kpis">
        <Kpi tone="rev" label={data.base.label} value={fmtMoney(data.base.total)} hint="base: o que o VP aprovou" />
        <Kpi tone="budget" label={data.proposal.label} value={fmtMoney(data.proposal.total)} hint="proposta em análise" />
        <Kpi
          tone="cut"
          label="Diferença"
          value={fmtSignedMoney(data.difference)}
          hint={`${fmtPct(cutPct)} · ${fmtInt(data.totals.cuts_count)} corte(s), ${fmtInt(data.totals.increases_count)} aumento(s)`}
        />
        <Kpi tone="ask" label="Pedir de volta" value={fmtMoney(data.ask.total)} hint={`proposta + pedido: ${fmtMoney(data.ask.proposal_with_ask)}`} />
      </div>

      <div className="section-stack">
        <Card title="Resumo por módulo">
          <Groups data={data} />
          <p className="muted small">
            Azul = realizado ({data.years.ref_label.toLowerCase().includes("anualizado") ? "anualizado quando o ano não fechou" : "com a projeção do gestor"}),
            roxo = revisão base, verde = orçamento proposto.{data.groups.some((g) => g.group === "PJ") ? " Contratos PJ calculados dos contratos." : ""}
          </p>
        </Card>

        <Card title="O que pedir de volta">
          <AskBack data={data} byKey={byKey} />
          <p className="muted small">
            Prioridade sugerida: <strong>Alta</strong> para obrigação legal ou contratual ({data.terms.join(", ")}; contratos PJ) e conta zerada com
            realizado; <strong>Média</strong> quando a proposta fica abaixo do realizado {data.years.ref} ou o gestor justificou; <strong>Baixa</strong> no resto.
            Ajuste em cada corte abaixo.
          </p>
        </Card>

        <Card title="Cortes e aumentos">
          <div className="def-filters">
            <FilterBar
              onReset={() => { setBaseId(""); setPropId(""); setGroup(""); setShow(""); setPrio(""); setSearch(""); }}
              resetCount={[baseId, propId, group, show, prio, search].filter(Boolean).length}
              fields={[
                { key: "base", label: "Base", value: baseId, onChange: setBaseId, options: [{ value: "", label: `Base padrão (${data.base.label})` }, ...versionOptions] },
                { key: "prop", label: "Proposta", value: propId, onChange: setPropId, options: [{ value: "", label: `Em elaboração (${data.proposal.label})` }, ...versionOptions] },
                { key: "group", label: "Módulo", value: group, onChange: setGroup, options: [{ value: "", label: "Todos os módulos" }, ...data.groups.map((g) => ({ value: g.group, label: g.label }))] },
                { key: "show", label: "Mostrar", value: show, onChange: setShow, options: SHOW },
                { key: "prio", label: "Prioridade", value: prio, onChange: setPrio, options: [{ value: "", label: "Todas as prioridades" }, ...data.priorities] },
              ]}
              extra={<SearchBox value={search} onChange={setSearch} placeholder="Buscar conta, CC, justificativa…" />}
              chips={search ? [{ label: `Busca: “${search}”`, onRemove: () => setSearch("") }] : []}
            />
          </div>
          {shown.length === 0 ? (
            <Empty>Nada nos filtros escolhidos.</Empty>
          ) : (
            <ul className="def-list">
              {shown.map((i) => <ItemCard key={i.key} item={i} data={data} onSaved={reload} />)}
            </ul>
          )}
        </Card>

        <Card title="Em aberto">
          <OpenPoints data={data} />
        </Card>
      </div>
    </div>
  );
}
