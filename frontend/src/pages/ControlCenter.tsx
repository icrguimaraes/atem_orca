import { Fragment, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Alert, Badge, Card, Empty, Loading, Modal, PageHeader, Stat, useLoad } from "../components/ui";
import { DATASET_LABELS, IMPORT_STATUS, LAYOUT_LABELS, MONTHS, PARAM_LABELS, fmtDateTime, fmtInt, fmtMoney } from "../labels";
import { usePersistentState } from "../persist";

/* Central de dados (10/10/2026, "no Power BI eu tinha o Power Query"): como cada número do sistema é montado — fontes,
   etapas do ETL, parâmetros e histórico. Acesso pessoal (users.can_control_data), não do perfil. */

type Modules = Record<string, string>;
interface FactSource { version_id: number; dataset_type: string; scope_key: string; company: string | null; total: string; modules: Modules }
interface FactLineage { key: string; title: string; year: number; total: string; modules: Modules; sources: FactSource[] }
interface BudgetComponent { module: string; label: string; total: string; origin: "gestor" | "calculado" }
interface BudgetVersionTotal { id: number; label: string; status: string; frozen_at: string | null; total: string; modules: Modules }
interface BudgetLineage { title: string; year: number; frozen: boolean; total: string; components: BudgetComponent[]; versions: BudgetVersionTotal[] }
interface Lineage { budget: BudgetLineage | null; actual: FactLineage[]; reference: FactLineage[] }

interface Source {
  id: number; dataset_type: string; scope_key: string; version_number: number; is_current: boolean; source: string;
  created_at: string; created_by: string | null; superseded_at: string | null; row_count: number; last_closed_period: number | null;
  first_period: number | null; last_period: number | null; total: string; projected: string;
  batch: { id: number; file_name: string; layout: string | null; mode: string | null } | null;
}

interface LayoutStep {
  layout: string; dataset: string; title: string; read: string; load: string; imports: number;
  columns: { field: string; names: string[] }[];
  last_import: { id: number; file_name: string; status: string; created_at: string; total_rows: number; valid_rows: number; warning_rows: number; error_rows: number; duplicate_rows: number } | null;
}
interface Steps { pipeline: { title: string; text: string }[]; layouts: LayoutStep[] }

type Kind = "bool" | "json" | "account" | "percent" | "number" | "month" | "text";
interface Param {
  key: string; group: string; kind: Kind; value: unknown; default: unknown; is_default: boolean; description: string;
  used_in: string | null; account_name: string | null; changes: number;
  last_change: { at: string; user: string | null; reason: string | null; before: unknown } | null;
}
interface Params { cycle: string; items: Param[]; groups: string[] }

interface HistoryItem {
  id: number; at: string; user: string | null; action: string; action_label: string; entity_type: string; entity_id: string | null;
  reason: string | null; before: Record<string, unknown> | null; after: Record<string, unknown> | null;
}

const TABS = [
  ["fluxo", "Fluxo"],
  ["fontes", "Fontes"],
  ["etapas", "Etapas do ETL"],
  ["parametros", "Parâmetros"],
  ["historico", "Histórico"],
] as const;
type Tab = (typeof TABS)[number][0];

const MODULE_ORDER = ["OPEX", "PERSONNEL", "CAPEX", "OUTROS"];
const MODULE_LABEL: Record<string, string> = { OPEX: "OPEX", PERSONNEL: "Pessoal", CAPEX: "CAPEX", OUTROS: "Outros" };
const VERSION_STATUS: Record<string, { label: string; tone: "good" | "info" | "neutral" }> = {
  WORKING: { label: "Aberta", tone: "info" },
  FROZEN: { label: "Congelada", tone: "neutral" },
  CONSOLIDATED: { label: "Congelada", tone: "neutral" },
};

const months = (a: number | null, b: number | null) => (a && b ? (a === b ? MONTHS[a - 1] : `${MONTHS[a - 1]}–${MONTHS[b - 1]}`) : "—");
const sourceLabel = (dtype: string) => (dtype === "PROJECTION" ? "Projeção" : DATASET_LABELS[dtype] ?? dtype);
const scopeLabel = (scope: string) => scope.split(":").slice(1).join(" · ");

// ------------------------------------------------------------------ Fluxo

function ModuleCells({ modules, cols }: { modules: Modules; cols: string[] }) {
  return <>{cols.map((m) => <td key={m} className="right">{modules[m] ? fmtMoney(modules[m]) : "—"}</td>)}</>;
}

function FactCard({ item, onOpenSources }: { item: FactLineage; onOpenSources: () => void }) {
  const cols = MODULE_ORDER.filter((m) => item.modules[m]);
  return (
    <Card title={item.title} actions={<button className="btn btn-ghost btn-sm" onClick={onOpenSources}>Ver fontes</button>}>
      <div className="stats">
        <Stat label="Total" value={fmtMoney(item.total)} />
        {cols.map((m) => <Stat key={m} label={MODULE_LABEL[m]} value={fmtMoney(item.modules[m])} />)}
      </div>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th>Fonte</th><th>Empresa</th>{cols.map((m) => <th key={m} className="right">{MODULE_LABEL[m]}</th>)}<th className="right">Total</th></tr>
          </thead>
          <tbody>
            {item.sources.map((s) => (
              <tr key={`${s.version_id}-${s.company}`}>
                <td>{sourceLabel(s.dataset_type)}</td>
                <td>{s.company ?? "—"}</td>
                <ModuleCells modules={s.modules} cols={cols} />
                <td className="right"><strong>{fmtMoney(s.total)}</strong></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function BudgetCard({ b }: { b: BudgetLineage }) {
  const byModule = MODULE_ORDER.filter((m) => b.components.some((c) => c.module === m));
  const cols = MODULE_ORDER.filter((m) => b.versions.some((v) => v.modules[m]));
  return (
    <Card title={b.title}>
      <div className="stats">
        <Stat label="Total da versão" tone="budget" value={fmtMoney(b.total)} hint={b.frozen ? "versão congelada (foto)" : "versão aberta: recalculada a cada mudança"} />
      </div>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Componente</th><th>Origem</th><th className="right">Valor</th></tr></thead>
          <tbody>
            {byModule.map((m) => {
              const items = b.components.filter((c) => c.module === m);
              const sum = items.reduce((acc, c) => acc + Number(c.total), 0);
              return (
                <Fragment key={m}>
                  <tr className="dc-group"><td><strong>{MODULE_LABEL[m]}</strong></td><td /><td className="right"><strong>{fmtMoney(sum)}</strong></td></tr>
                  {items.map((c) => (
                    <tr key={c.label}>
                      <td className="dc-indent">{c.label}</td>
                      <td><Badge tone={c.origin === "gestor" ? "info" : "neutral"}>{c.origin === "gestor" ? "Lançado" : "Calculado"}</Badge></td>
                      <td className="right">{fmtMoney(c.total)}</td>
                    </tr>
                  ))}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      <h3 className="section-title dc-sub">Versões</h3>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Versão</th><th>Situação</th>{cols.map((m) => <th key={m} className="right">{MODULE_LABEL[m]}</th>)}<th className="right">Total</th></tr></thead>
          <tbody>
            {b.versions.map((v) => {
              const st = VERSION_STATUS[v.status] ?? { label: v.status, tone: "neutral" as const };
              return (
                <tr key={v.id}>
                  <td>{v.label}</td>
                  <td><Badge tone={st.tone}>{st.label}</Badge>{v.frozen_at && <span className="muted small"> {fmtDateTime(v.frozen_at)}</span>}</td>
                  <ModuleCells modules={v.modules} cols={cols} />
                  <td className="right"><strong>{fmtMoney(v.total)}</strong></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function FlowTab({ go }: { go: (t: Tab) => void }) {
  const { data, error } = useLoad(() => api<Lineage>("/control/lineage"));
  if (error) return <Alert>{error}</Alert>;
  if (!data) return <Loading />;
  return (
    <div className="section-stack">
      <ol className="dc-flow" aria-label="Caminho dos dados">
        <li><strong>Arquivos</strong><span>KSB1, base de controle, templates, quadro</span></li>
        <li><strong>Leitura e de-para</strong><span>layout, colunas, CC e conta</span></li>
        <li><strong>Bases versionadas</strong><span>empresa × ano; a anterior fica no histórico</span></li>
        <li><strong>Cálculos</strong><span>pessoal, PJ, rateio — pelos parâmetros</span></li>
        <li><strong>Painel e consolidação</strong><span>os números que todos veem</span></li>
      </ol>
      {data.budget && <BudgetCard b={data.budget} />}
      {data.actual.map((a) => <FactCard key={a.key} item={a} onOpenSources={() => go("fontes")} />)}
      {data.reference.map((a) => <FactCard key={a.key} item={a} onOpenSources={() => go("fontes")} />)}
      {!data.actual.length && !data.reference.length && <Empty>Nenhuma base carregada.</Empty>}
    </div>
  );
}

// ------------------------------------------------------------------ Fontes

function SourcesTab() {
  const { data, error } = useLoad(() => api<Source[]>("/control/sources"));
  const [all, setAll] = usePersistentState("atem.central.sources.all", false);
  if (error) return <Alert>{error}</Alert>;
  if (!data) return <Loading />;
  const rows = all ? data : data.filter((s) => s.is_current);
  return (
    <Card
      title={`Bases ${all ? "(todas as versões)" : "vigentes"}`}
      actions={
        <label className="check small"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> Mostrar versões substituídas</label>
      }
    >
      <p className="muted small">Cada carga cria uma versão nova da base (empresa × ano). Só a vigente entra nos números; as anteriores ficam aqui para conferência.</p>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th>Base</th><th>Escopo</th><th>Versão</th><th>Arquivo</th><th>Meses</th><th className="right">Linhas</th><th className="right">Total</th><th>Carregada</th></tr>
          </thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s.id} className={s.is_current ? "" : "dc-old"}>
                <td>{sourceLabel(s.dataset_type)}{!s.is_current && <div><Badge tone="neutral">Substituída {fmtDateTime(s.superseded_at)}</Badge></div>}</td>
                <td className="mono small">{scopeLabel(s.scope_key)}</td>
                <td>v{s.version_number}</td>
                <td>
                  {s.batch ? <Link to={`/importacoes/${s.batch.id}`}>{s.batch.file_name}</Link> : <span className="muted">{s.source}</span>}
                  {s.batch?.layout && <div className="muted small">{LAYOUT_LABELS[s.batch.layout] ?? s.batch.layout}{s.batch.mode ? ` · ${s.batch.mode === "REPLACE" ? "Substituir" : "Mesclar"}` : ""}</div>}
                </td>
                <td>{months(s.first_period, s.last_period)}{s.last_closed_period ? <div className="muted small">fechado até {MONTHS[s.last_closed_period - 1]}</div> : null}</td>
                <td className="right">{fmtInt(s.row_count)}</td>
                <td className="right">
                  {fmtMoney(s.total)}
                  {Number(s.projected) !== 0 && <div className="muted small">projetado {fmtMoney(s.projected)}</div>}
                </td>
                <td>{fmtDateTime(s.created_at)}<div className="muted small">{s.created_by ?? "—"}</div></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

// ------------------------------------------------------------------ Etapas

function StepsTab() {
  const { data, error } = useLoad(() => api<Steps>("/control/steps"));
  if (error) return <Alert>{error}</Alert>;
  if (!data) return <Loading />;
  return (
    <div className="section-stack">
      <Card title="Etapas de toda importação">
        <ol className="dc-steps">
          {data.pipeline.map((p) => <li key={p.title}><strong>{p.title}</strong><span>{p.text}</span></li>)}
        </ol>
      </Card>
      <Card title="Layouts reconhecidos">
        <div className="dc-layouts">
          {data.layouts.map((l) => {
            const last = l.last_import;
            const st = last ? IMPORT_STATUS[last.status] : null;
            return (
              <details key={l.layout} className="dc-layout">
                <summary>
                  <span className="dc-layout-title">{l.title}</span>
                  <span className="muted small">{l.imports ? `${fmtInt(l.imports)} importação(ões)` : "nunca usado"}</span>
                </summary>
                <dl className="dc-dl">
                  <dt>Lê</dt><dd>{l.read}</dd>
                  <dt>Carrega</dt><dd>{l.load}</dd>
                  {last && (
                    <>
                      <dt>Última</dt>
                      <dd>
                        <Link to={`/importacoes/${last.id}`}>{last.file_name}</Link> · {fmtDateTime(last.created_at)}{" "}
                        {st && <Badge tone={st.tone}>{st.label}</Badge>}
                        <div className="muted small">
                          {fmtInt(last.total_rows)} linhas · {fmtInt(last.valid_rows)} válidas · {fmtInt(last.warning_rows)} com aviso · {fmtInt(last.error_rows)} inconsistentes · {fmtInt(last.duplicate_rows)} duplicadas
                        </div>
                      </dd>
                    </>
                  )}
                </dl>
                {l.columns.length > 0 && (
                  <div className="table-wrap">
                    <table className="table">
                      <thead><tr><th>Campo</th><th>Nomes de coluna aceitos (o primeiro encontrado vale)</th></tr></thead>
                      <tbody>
                        {l.columns.map((c) => (
                          <tr key={c.field}><td>{c.field}</td><td className="small">{c.names.join(" · ")}</td></tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </details>
            );
          })}
        </div>
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ Parâmetros

function show(p: Pick<Param, "kind" | "value" | "account_name">, value: unknown = p.value): string {
  if (value === null || value === undefined) return "—";
  switch (p.kind) {
    case "bool": return value ? "Sim" : "Não";
    case "percent": return `${(Number(value) * 100).toLocaleString("pt-BR", { maximumFractionDigits: 4 })}%`;
    case "month": return MONTHS[Number(value) - 1] ?? String(value);
    case "account": return String(value);
    case "number": return Number(value).toLocaleString("pt-BR");
    case "json": return Array.isArray(value) ? `${value.length} item(ns)` : `${Object.keys(value as object).length} item(ns)`;
    default: return String(value);
  }
}

function initialText(p: Param): string {
  if (p.kind === "json") return JSON.stringify(p.value ?? (Array.isArray(p.default) ? [] : {}), null, 2);
  if (p.kind === "percent") return String(Number(p.value) * 100).replace(".", ",");
  if (p.kind === "bool") return p.value ? "true" : "false";
  return p.value === null || p.value === undefined ? "" : String(p.value).replace(".", ",");
}

function parseValue(p: Param, text: string): { ok: true; value: unknown } | { ok: false; error: string } {
  if (p.kind === "bool") return { ok: true, value: text === "true" };
  if (p.kind === "json") {
    try {
      return { ok: true, value: JSON.parse(text) };
    } catch {
      return { ok: false, error: "JSON inválido" };
    }
  }
  if (p.kind === "text") return { ok: true, value: text };
  const n = Number(text.trim().replace(/\./g, p.kind === "account" ? "" : ".").replace(",", "."));
  if (!text.trim() || Number.isNaN(n)) return { ok: false, error: "Informe um número" };
  if (p.kind === "percent") return { ok: true, value: Math.round(n * 1e6) / 1e8 };
  if (p.kind === "month" && (n < 1 || n > 12)) return { ok: false, error: "Mês de 1 a 12" };
  return { ok: true, value: n };
}

function ParamEditor({ p, onClose, onSaved }: { p: Param; onClose: () => void; onSaved: () => void }) {
  const [text, setText] = useState(initialText(p));
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function save() {
    const parsed = parseValue(p, text);
    if (!parsed.ok) return setError(parsed.error);
    if (reason.trim().length < 5) return setError("Escreva o motivo da alteração (fica no histórico)");
    setBusy(true);
    try {
      await api(`/control/parameters/${encodeURIComponent(p.key)}`, { method: "PUT", body: JSON.stringify({ value: parsed.value, reason: reason.trim() }) });
      onSaved();
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title={PARAM_LABELS[p.key] ?? p.key}
      onClose={onClose}
      wide={p.kind === "json"}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>Cancelar</button>
          <button className="btn btn-primary" onClick={save} disabled={busy}>{busy ? "Salvando…" : "Salvar"}</button>
        </>
      }
    >
      <div className="stack">
      <p className="muted small">{p.description}{p.used_in && <><br />Usado em: {p.used_in}</>}</p>
      <label className="field">
        Valor {p.kind === "percent" ? "(%)" : ""}
        {p.kind === "bool" ? (
          <select value={text} onChange={(e) => setText(e.target.value)}><option value="true">Sim</option><option value="false">Não</option></select>
        ) : p.kind === "month" ? (
          <select value={text} onChange={(e) => setText(e.target.value)}>{MONTHS.map((m, i) => <option key={m} value={String(i + 1)}>{m}</option>)}</select>
        ) : p.kind === "json" ? (
          <textarea className="mono" rows={14} value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} />
        ) : (
          <input value={text} onChange={(e) => setText(e.target.value)} inputMode="decimal" />
        )}
      </label>
      <p className="muted small">Padrão do sistema: <span className="mono">{p.kind === "json" ? JSON.stringify(p.default) : show(p, p.default)}</span></p>
      <label className="field">
        Motivo (obrigatório)
        <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Ex.: IPCA confirmado pela gestora em 10/10" />
      </label>
      {error && <Alert>{error}</Alert>}
      </div>
    </Modal>
  );
}

function ParamsTab() {
  const { data, error, reload } = useLoad(() => api<Params>("/control/parameters"));
  const [editing, setEditing] = useState<Param | null>(null);
  const [q, setQ] = useState("");
  const groups = useMemo(() => {
    if (!data) return [];
    const term = q.trim().toLowerCase();
    const items = data.items.filter((p) => !term || [p.key, PARAM_LABELS[p.key], p.description, p.used_in].some((s) => s?.toLowerCase().includes(term)));
    const order = [...data.groups, "Outros"];
    return order.map((g) => [g, items.filter((p) => p.group === g)] as const).filter(([, list]) => list.length);
  }, [data, q]);
  if (error) return <Alert>{error}</Alert>;
  if (!data) return <Loading />;
  const changed = data.items.filter((p) => !p.is_default).length;
  return (
    <div className="section-stack">
      <div className="dc-toolbar">
        <input className="dc-search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Buscar parâmetro, conta ou uso…" aria-label="Buscar parâmetro" />
        <span className="muted small">{fmtInt(data.items.length)} parâmetros · {fmtInt(changed)} diferentes do padrão · ciclo {data.cycle}</span>
      </div>
      {groups.map(([group, list]) => (
        <Card key={group} title={group}>
          <div className="table-wrap">
            <table className="table dc-params">
              <thead><tr><th>Parâmetro</th><th>Valor</th><th>Última alteração</th><th /></tr></thead>
              <tbody>
                {list.map((p) => (
                  <tr key={p.key}>
                    <td>
                      <strong>{PARAM_LABELS[p.key] ?? p.description ?? p.key}</strong>
                      <div className="muted small">{p.used_in ?? p.description}</div>
                      <div className="mono small muted">{p.key}</div>
                    </td>
                    <td>
                      <span className="mono">{show(p)}</span>
                      {p.account_name && <div className="muted small">{p.account_name}</div>}
                      {p.kind === "json" && (
                        <details className="small"><summary>ver conteúdo</summary><pre className="dc-json">{JSON.stringify(p.value, null, 2)}</pre></details>
                      )}
                      <div>{p.is_default ? <Badge tone="neutral">Padrão</Badge> : <Badge tone="warn">Alterado</Badge>}</div>
                    </td>
                    <td className="small">
                      {p.last_change ? (
                        <>
                          {fmtDateTime(p.last_change.at)} · {p.last_change.user ?? "sistema"}
                          {p.last_change.reason && <div className="muted">“{p.last_change.reason}”</div>}
                          <div className="muted">{fmtInt(p.changes)} alteração(ões)</div>
                        </>
                      ) : <span className="muted">nunca alterado</span>}
                    </td>
                    <td className="right"><button className="btn btn-ghost btn-sm" onClick={() => setEditing(p)}>Editar</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ))}
      {editing && <ParamEditor p={editing} onClose={() => setEditing(null)} onSaved={reload} />}
    </div>
  );
}

// ------------------------------------------------------------------ Histórico

function detail(h: HistoryItem): string {
  if (h.action === "SET_PARAMETER") {
    const fmt = (v: unknown) => (typeof v === "object" ? JSON.stringify(v) : String(v));
    const before = h.before?.value;
    return `${h.entity_id?.split(":").slice(1).join(":")}: ${before === undefined ? "—" : fmt(before)} → ${fmt(h.after?.value)}`;
  }
  if (h.action === "LOAD" && h.after) return `${fmtInt(Number(h.after.entries ?? 0))} lançamentos`;
  if (h.entity_type === "import_batch") return `Importação ${h.entity_id}`;
  return [h.entity_type, h.entity_id].filter(Boolean).join(" ");
}

function HistoryTab() {
  const { data, error } = useLoad(() => api<HistoryItem[]>("/control/history"));
  if (error) return <Alert>{error}</Alert>;
  if (!data) return <Loading />;
  if (!data.length) return <Empty>Nada registrado ainda.</Empty>;
  return (
    <Card title="Últimas mudanças na base e nos parâmetros">
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Quando</th><th>Quem</th><th>O quê</th><th>Detalhe</th><th>Motivo</th></tr></thead>
          <tbody>
            {data.map((h) => (
              <tr key={h.id}>
                <td className="small">{fmtDateTime(h.at)}</td>
                <td className="small">{h.user ?? "sistema"}</td>
                <td>{h.action_label}</td>
                <td className="small dc-detail">
                  {h.entity_type === "import_batch" && h.entity_id ? <Link to={`/importacoes/${h.entity_id}`}>{detail(h)}</Link> : detail(h)}
                </td>
                <td className="small muted">{h.reason ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

// ------------------------------------------------------------------ página

export default function ControlCenter() {
  const [tab, setTab] = usePersistentState<Tab>("atem.central.tab", "fluxo");
  return (
    <>
      <PageHeader title="Central de dados" subtitle="Como o sistema é montado: fontes, etapas do ETL, parâmetros e histórico. Acesso só seu." />
      <div className="seg-tabs dc-tabs" role="tablist">
        {TABS.map(([key, label]) => (
          <button key={key} role="tab" aria-selected={tab === key} className={tab === key ? "active" : ""} onClick={() => setTab(key)}>{label}</button>
        ))}
      </div>
      {tab === "fluxo" && <FlowTab go={setTab} />}
      {tab === "fontes" && <SourcesTab />}
      {tab === "etapas" && <StepsTab />}
      {tab === "parametros" && <ParamsTab />}
      {tab === "historico" && <HistoryTab />}
    </>
  );
}
