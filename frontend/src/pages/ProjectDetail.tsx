import { useState, type ReactNode } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { PlotlyChart, type Figure } from "../components/PlotlyChart";
import { Alert, Badge, BackButton, Card, Empty, Loading, PageHeader, Stat, lines, useLoad } from "../components/ui";
import { fmtDateTime, fmtDay, fmtMoney } from "../labels";
import { LIGHT_TONE, ProjectForm, pct, type ProjectBase } from "./Projects";

/* Página de um projeto: cadastro, farol, números lidos do sistema (realizado, projeção, orçado de referência e CAPEX do
   ciclo), marcos, riscos, links, diário e lançamentos. Toda alteração fica na auditoria. */

interface Milestone { id: number; title: string; due_date: string | null; done_date: string | null; progress_pct: number; owner_name: string | null; notes: string | null; late: boolean }
interface Risk { id: number; description: string; impact: string; impact_label: string; probability: string; probability_label: string; mitigation: string | null; status: string; status_label: string }
interface Detail extends ProjectBase {
  objective: string | null; scope: string | null; benefits: string | null; notes: string | null;
  fact_keys: { id: number; cost_center_id: number; account_id: number; cost_center: string | null; account: string | null }[];
  capex_links: { id: number; cost_center_id: number; capex_code: string; cost_center: string | null; title?: string; total?: string; found: boolean }[];
  by_year: { year: number; actual: string; projection: string; budget: string }[];
  figure_years: number[];
  milestones: Milestone[]; risks: Risk[]; links: { id: number; title: string; url: string }[];
  updates: { id: number; text: string; author: string | null; created_at: string; can_delete: boolean }[];
  documents: { year: number; period: number; date: string | null; document: string | null; vendor: string | null; text: string | null; amount: string }[];
}

const LEVELS = [{ value: "LOW", label: "Baixo" }, { value: "MEDIUM", label: "Médio" }, { value: "HIGH", label: "Alto" }];
const RISK_STATUS = [{ value: "OPEN", label: "Aberto" }, { value: "MITIGATED", label: "Mitigado" }, { value: "CLOSED", label: "Encerrado" }];

export default function ProjectDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { data: loaded, error, reload } = useLoad(() => api<Detail>(`/projects/${id}`), [id]);
  const [local, setLocal] = useState<Detail | null>(null);
  const [year, setYear] = useState<number | null>(null);
  const [editing, setEditing] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const d = local && loaded && local.id === loaded.id ? local : loaded;
  const fy = year ?? d?.summary.ref_year ?? null;
  const fig = useLoad(() => (d ? api<Figure>(`/projects/${d.id}/figure?year=${fy}`) : Promise.resolve(null)), [d?.id, fy]);
  if (error) return <Alert>{error}</Alert>;
  if (!d) return <Loading />;
  const s = d.summary;

  async function child(kind: string, method: "POST" | "PATCH" | "DELETE", body?: object, childId?: number) {
    setErr(null);
    try {
      setLocal(await api<Detail>(`/projects/${d!.id}/${kind}${childId ? `/${childId}` : ""}`, { method, body: body ? JSON.stringify(body) : undefined }));
      return true;
    } catch (e) {
      setErr((e as Error).message);
      return false;
    }
  }
  async function remove() {
    if (!window.confirm(`Excluir o projeto ${d!.code}? O cadastro, marcos, riscos e diário saem; a exclusão fica na auditoria.`)) return;
    try {
      await api(`/projects/${d!.id}`, { method: "DELETE" });
      navigate("/projetos");
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  return (
    <>
      <PageHeader
        title={`${d.code} · ${d.name}`}
        subtitle={<p className="muted">{d.status_label}{d.cost_center ? ` · ${d.cost_center}` : ""}{d.owner ? ` · responsável: ${d.owner}` : ""}{d.sponsor ? ` · patrocinador: ${d.sponsor}` : ""}</p>}
        actions={<>
          <BackButton fallback="/projetos" />
          {d.can_edit && <button type="button" className="btn" onClick={() => setEditing(true)}>Editar</button>}
          {d.can_delete && <button type="button" className="btn btn-ghost" onClick={remove}>Excluir</button>}
        </>}
      />
      {err && <Alert>{err}</Alert>}
      <div className="stats">
        <Stat label="Farol" value={<Badge tone={LIGHT_TONE[s.light]}>{s.light_label}</Badge>} hint={lines(`${pct(s.spent_pct)} gasto · ${pct(s.time_pct)} do prazo`, s.progress_pct !== null ? `${s.progress_pct}% dos marcos` : null)} />
        <Stat label={`Realizado ${s.ref_year}`} value={fmtMoney(s.actual_year)} hint={lines(s.last_closed ? `até o mês ${s.last_closed}` : null, Number(s.projection_year) ? `+ projeção ${fmtMoney(s.projection_year)}` : null)} />
        <Stat label={`Orçado ${s.ref_year}`} tone="budget" value={Number(s.budget_year) ? fmtMoney(s.budget_year) : "—"} hint={s.available_year !== null ? `disponível ${fmtMoney(s.available_year)}` : "sem orçado no sistema"} />
        <Stat label={`Orçamento ${s.target_year}`} tone="budget" value={Number(s.cycle_budget) ? fmtMoney(s.cycle_budget) : "—"} hint={Number(s.cycle_unscheduled) ? `${fmtMoney(s.cycle_unscheduled)} sem cronograma` : "CAPEX do ciclo vinculado"} />
        <Stat label="Realizado acumulado" value={fmtMoney(s.actual_all)} hint={s.actual_years.length > 1 ? `${s.actual_years[0]}–${s.actual_years[s.actual_years.length - 1]}` : s.actual_years.length ? `em ${s.actual_years[0]}` : undefined} />
      </div>
      {s.warnings.length > 0 && (
        <Alert tone="warn">
          <strong>Apontamentos</strong>
          <ul className="plain-list">{s.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
        </Alert>
      )}

      <Card
        title="Comparativo mensal"
        actions={<div className="seg-tabs seg-mini" role="group" aria-label="Ano">{d.figure_years.map((y) => <button key={y} type="button" className={y === fy ? "active" : ""} onClick={() => setYear(y)}>{y}</button>)}</div>}
      >
        {fig.data && fig.data.data.length ? <PlotlyChart figure={fig.data} height={340} ariaLabel="Comparativo mensal do projeto" /> : <Empty>Sem valores em {fy}.</Empty>}
      </Card>

      <div className="grid-2">
        <Card title="Sobre o projeto">
          <dl className="kv">
            <Kv label="Objetivo">{d.objective}</Kv>
            <Kv label="Escopo">{d.scope}</Kv>
            <Kv label="Benefícios esperados">{d.benefits}</Kv>
            <Kv label="Prazo previsto">{fmtDay(d.start_planned)} a {fmtDay(d.end_planned)}</Kv>
            <Kv label="Prazo real">{fmtDay(d.start_actual)} a {fmtDay(d.end_actual)}</Kv>
          </dl>
        </Card>
        <Card title="De onde vêm os números">
          <dl className="kv">
            <Kv label="Realizado e orçado (CC × conta)">{d.fact_keys.length ? d.fact_keys.map((k) => <div key={k.id}>{k.cost_center} × {k.account}</div>) : null}</Kv>
            <Kv label={`CAPEX ${s.target_year}`}>{d.capex_links.length ? d.capex_links.map((c) => <div key={c.id}>{c.cost_center} · {c.capex_code}{c.found ? ` · ${c.title} · ${fmtMoney(c.total ?? 0)}` : " · não encontrada na versão vigente"}</div>) : null}</Kv>
          </dl>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Ano</th><th className="right">Realizado</th><th className="right">Projeção</th><th className="right">Orçado</th></tr></thead>
              <tbody>{d.by_year.map((r) => <tr key={r.year}><td>{r.year}</td><td className="right">{fmtMoney(r.actual)}</td><td className="right">{fmtMoney(r.projection)}</td><td className="right">{fmtMoney(r.budget)}</td></tr>)}</tbody>
            </table>
          </div>
        </Card>
      </div>

      <Milestones d={d} child={child} />
      <Risks d={d} child={child} />

      <div className="grid-2">
        <Card title="Diário do projeto">
          <AddText placeholder="Atualização de status, decisão, pendência…" onAdd={(text) => child("updates", "POST", { text })} />
          {d.updates.length === 0 ? <Empty>Nenhuma atualização ainda.</Empty> : (
            <ul className="just-list">
              {d.updates.map((u) => (
                <li key={u.id} className="just-item">
                  <div className="muted small">{u.author ?? "—"} · {fmtDateTime(u.created_at)}</div>
                  <div className="pre-wrap">{u.text}</div>
                  {u.can_delete && <button type="button" className="btn btn-sm btn-ghost" onClick={() => child("updates", "DELETE", undefined, u.id)}>Excluir</button>}
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Documentos e links">
          <LinkForm onAdd={(title, url) => child("links", "POST", { title, url })} />
          {d.links.length === 0 ? <Empty>Nenhum link ainda (contrato, cronograma, pasta do projeto…).</Empty> : (
            <ul className="plain-list">
              {d.links.map((l) => (
                <li key={l.id}>
                  <a href={l.url} target="_blank" rel="noopener noreferrer">{l.title}</a>{" "}
                  <button type="button" className="btn btn-sm btn-ghost" onClick={() => child("links", "DELETE", undefined, l.id)}>Remover</button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card title={`Lançamentos do realizado (${d.documents.length})`}>
        {d.documents.length === 0 ? <Empty>Sem lançamentos nos vínculos do projeto.</Empty> : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Mês</th><th>Data</th><th>Documento</th><th>Fornecedor</th><th>Texto</th><th className="right">Valor</th></tr></thead>
              <tbody>
                {d.documents.map((x, i) => (
                  <tr key={i}>
                    <td>{String(x.period).padStart(2, "0")}/{x.year}</td><td>{fmtDay(x.date)}</td><td>{x.document ?? "—"}</td>
                    <td>{x.vendor ?? "—"}</td><td className="small">{x.text ?? ""}</td><td className="right">{fmtMoney(x.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {editing && <ProjectForm project={d} onClose={() => setEditing(false)} onSaved={() => { setLocal(null); reload(); }} />}
    </>
  );
}

function Kv({ label, children }: { label: string; children: ReactNode }) {
  const empty = children === null || children === undefined || children === "";
  return (<><dt>{label}</dt><dd className={empty ? "muted" : "pre-wrap"}>{empty ? "Não informado" : children}</dd></>);
}

type ChildFn = (kind: string, method: "POST" | "PATCH" | "DELETE", body?: object, childId?: number) => Promise<boolean>;

function Milestones({ d, child }: { d: Detail; child: ChildFn }) {
  const [title, setTitle] = useState("");
  const [due, setDue] = useState("");
  return (
    <Card title="Cronograma e marcos">
      <div className="form-row">
        <label>Marco<input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Ex.: Go-live do módulo fiscal" /></label>
        <label>Data prevista<input type="date" value={due} onChange={(e) => setDue(e.target.value)} /></label>
        <div className="inline-controls form-add">
          <button type="button" className="btn btn-sm" disabled={!title.trim()} onClick={async () => { if (await child("milestones", "POST", { title, due_date: due || null })) { setTitle(""); setDue(""); } }}>Acrescentar</button>
        </div>
      </div>
      {d.milestones.length === 0 ? <Empty>Nenhum marco ainda.</Empty> : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Marco</th><th>Previsto</th><th>Concluído em</th><th className="right">% feito</th><th></th></tr></thead>
            <tbody>
              {d.milestones.map((m) => (
                <tr key={m.id}>
                  <td>{m.title}{m.late && <> <Badge tone="bad">atrasado</Badge></>}</td>
                  <td>{fmtDay(m.due_date)}</td>
                  <td><input type="date" aria-label="Concluído em" value={m.done_date ?? ""} onChange={(e) => child("milestones", "PATCH", { done_date: e.target.value || null, ...(e.target.value ? { progress_pct: 100 } : {}) }, m.id)} /></td>
                  <td className="right">
                    <select aria-label="% feito" value={m.progress_pct} onChange={(e) => child("milestones", "PATCH", { progress_pct: Number(e.target.value) }, m.id)}>
                      {[0, 10, 25, 50, 75, 90, 100].concat([m.progress_pct]).filter((v, i, a) => a.indexOf(v) === i).sort((a, b) => a - b).map((v) => <option key={v} value={v}>{v}%</option>)}
                    </select>
                  </td>
                  <td className="row-actions"><button type="button" className="btn btn-sm btn-ghost" onClick={() => child("milestones", "DELETE", undefined, m.id)}>Remover</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function Risks({ d, child }: { d: Detail; child: ChildFn }) {
  const [desc, setDesc] = useState("");
  const [impact, setImpact] = useState("MEDIUM");
  const [prob, setProb] = useState("MEDIUM");
  const [mit, setMit] = useState("");
  return (
    <Card title="Riscos">
      <div className="form-row">
        <label>Risco<input value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="Ex.: atraso do fornecedor" /></label>
        <label>Impacto<select value={impact} onChange={(e) => setImpact(e.target.value)}>{LEVELS.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}</select></label>
        <label>Probabilidade<select value={prob} onChange={(e) => setProb(e.target.value)}>{LEVELS.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}</select></label>
        <label>Mitigação<input value={mit} onChange={(e) => setMit(e.target.value)} /></label>
        <div className="inline-controls form-add">
          <button type="button" className="btn btn-sm" disabled={!desc.trim()} onClick={async () => { if (await child("risks", "POST", { description: desc, impact, probability: prob, mitigation: mit || null })) { setDesc(""); setMit(""); } }}>Acrescentar</button>
        </div>
      </div>
      {d.risks.length === 0 ? <Empty>Nenhum risco registrado.</Empty> : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Risco</th><th>Impacto</th><th>Probabilidade</th><th>Mitigação</th><th>Situação</th><th></th></tr></thead>
            <tbody>
              {d.risks.map((r) => (
                <tr key={r.id}>
                  <td>{r.description}</td>
                  <td><Badge tone={r.impact === "HIGH" ? "bad" : r.impact === "MEDIUM" ? "warn" : "neutral"}>{r.impact_label}</Badge></td>
                  <td>{r.probability_label}</td>
                  <td className="small">{r.mitigation ?? "—"}</td>
                  <td><select aria-label="Situação do risco" value={r.status} onChange={(e) => child("risks", "PATCH", { status: e.target.value }, r.id)}>{RISK_STATUS.map((x) => <option key={x.value} value={x.value}>{x.label}</option>)}</select></td>
                  <td className="row-actions"><button type="button" className="btn btn-sm btn-ghost" onClick={() => child("risks", "DELETE", undefined, r.id)}>Remover</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function AddText({ placeholder, onAdd }: { placeholder: string; onAdd: (text: string) => Promise<boolean> }) {
  const [text, setText] = useState("");
  return (
    <div className="question-form">
      <textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} placeholder={placeholder} aria-label={placeholder} />
      <div className="inline-controls">
        <button type="button" className="btn btn-sm btn-primary" disabled={!text.trim()} onClick={async () => { if (await onAdd(text)) setText(""); }}>Registrar</button>
      </div>
    </div>
  );
}

function LinkForm({ onAdd }: { onAdd: (title: string, url: string) => Promise<boolean> }) {
  const [title, setTitle] = useState("");
  const [url, setUrl] = useState("");
  return (
    <div className="form-row">
      <label>Título<input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Contrato, cronograma…" /></label>
      <label>Endereço<input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" /></label>
      <div className="inline-controls form-add">
        <button type="button" className="btn btn-sm" disabled={!title.trim() || !url.trim()} onClick={async () => { if (await onAdd(title, url)) { setTitle(""); setUrl(""); } }}>Acrescentar</button>
      </div>
    </div>
  );
}
