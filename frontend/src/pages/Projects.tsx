import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { PlotlyChart, type Figure } from "../components/PlotlyChart";
import { Alert, Badge, Card, Empty, Loading, Modal, PageHeader, Stat, lines, useLoad } from "../components/ui";
import { fmtDay, fmtInt, fmtMoney, type Tone } from "../labels";

/* Projetos (14/10/2026): cadastro completo de cada projeto (TDC, Reforma Tributária…) com os números lidos do sistema
   pelos vínculos centro de custo × conta e pelas solicitações de CAPEX do ciclo. O orçamento não é alterado aqui. */

export interface ProjectSummary {
  ref_year: number; target_year: number; last_closed: number | null;
  actual_year: string; projection_year: string; forecast_year: string; budget_year: string; available_year: string | null;
  spent_pct: number | null; time_pct: number | null; actual_all: string; actual_years: number[];
  cycle_budget: string; cycle_unscheduled: string; capex: { capex_code: string; cost_center: string | null; title: string; total: string }[];
  progress_pct: number | null; light: "good" | "warn" | "bad" | "none" | "closed"; light_label: string; warnings: string[];
}

export interface ProjectBase {
  id: number; code: string; name: string; status: string; status_label: string;
  cost_center_id: number | null; cost_center: string | null; owner_user_id: number | null; owner: string | null; sponsor: string | null;
  start_planned: string | null; end_planned: string | null; start_actual: string | null; end_actual: string | null;
  can_edit: boolean; can_delete: boolean; summary: ProjectSummary;
}

export const LIGHT_TONE: Record<ProjectSummary["light"], Tone> = { good: "good", warn: "warn", bad: "bad", none: "neutral", closed: "info" };

export const pct = (v: number | null) => (v === null ? "—" : `${(v * 100).toLocaleString("pt-BR", { maximumFractionDigits: 0 })}%`);

interface PortfolioItem extends ProjectBase {
  next_milestone: { title: string; due_date: string | null; progress_pct: number } | null;
  milestones: number; late_milestones: number; open_high_risks: number;
  monthly: number[]; monthly_projection: number[];
}

interface Portfolio {
  items: PortfolioItem[]; can_create: boolean; months?: string[];
  kpis: null | {
    ref_year: number; target_year: number; active: number; count: number; actual_all: string; actual_year: string;
    projection_year: string; budget_year: string; cycle_budget: string; late_milestones: number; open_high_risks: number; warnings: number;
  };
  figures: null | { gantt: Figure; compare: Figure; scurve: Figure };
}

/** Barrinha de 0–100% (gasto, prazo, marcos); o gasto fica vermelho quando passa do prazo ou do orçado. */
function Meter({ label, value, tone }: { label: string; value: number | null; tone: "bad" | "budget" | "time" }) {
  const w = value === null ? 0 : Math.max(0, Math.min(100, value * 100));
  return (
    <div className="meter">
      <div className="meter-head"><span>{label}</span><strong>{pct(value)}</strong></div>
      <div className="meter-track"><div className={`meter-fill meter-${tone}`} style={{ width: `${w}%` }} /></div>
    </div>
  );
}

/** Mini-gráfico do realizado mês a mês (azul) e da projeção (vermelho) no ano de referência. */
function Spark({ actual, projection, months }: { actual: number[]; projection: number[]; months: string[] }) {
  const max = Math.max(1, ...actual.map((a, i) => a + (projection[i] ?? 0)));
  const w = 12, gap = 4, h = 40;
  return (
    <svg className="spark" viewBox={`0 0 ${12 * (w + gap)} ${h}`} preserveAspectRatio="none" role="img" aria-label="Realizado mês a mês">
      {actual.map((a, i) => {
        const p = projection[i] ?? 0;
        const ha = (a / max) * (h - 2);
        const hp = (p / max) * (h - 2);
        return (
          <g key={i}>
            <title>{`${months[i] ?? i + 1}: ${fmtMoney(a)}${p ? ` + projeção ${fmtMoney(p)}` : ""}`}</title>
            {a > 0 && <rect x={i * (w + gap)} y={h - ha} width={w} height={ha} rx={2} className="spark-actual" />}
            {p > 0 && <rect x={i * (w + gap)} y={h - ha - hp} width={w} height={hp} rx={2} className="spark-proj" />}
            {a <= 0 && p <= 0 && <rect x={i * (w + gap)} y={h - 2} width={w} height={2} className="spark-empty" />}
          </g>
        );
      })}
    </svg>
  );
}

/** Realizado × orçado em colunas, por mês de um ano ou por ano, com o filtro de projeto acima (todos = soma). */
function BudgetChart({ items }: { items: ProjectBase[] }) {
  const [project, setProject] = useState("");
  const [year, setYear] = useState<number | null>(null);
  const [view, setView] = useState<"month" | "year">("month");
  const q = new URLSearchParams({ view });
  if (project) q.set("project_id", project);
  if (year) q.set("year", String(year));
  const { data, loading } = useLoad(
    () => api<{ figure: Figure | null; years: number[]; year: number }>(`/projects/chart?${q.toString()}`),
    [project, year, view],
  );
  const shown = data?.year ?? year;
  return (
    <Card
      title="Realizado × orçado"
      actions={
        <div className="chart-filters">
          <label className="sr-only" htmlFor="chart-project">Projeto</label>
          <select id="chart-project" value={project} onChange={(e) => setProject(e.target.value)}>
            <option value="">Todos os projetos</option>
            {items.map((p) => <option key={p.id} value={p.id}>{p.code} · {p.name}</option>)}
          </select>
          <div className="seg-tabs seg-mini" role="group" aria-label="Período">
            <button type="button" className={view === "year" ? "active" : ""} onClick={() => setView("year")}>Por ano</button>
            {(data?.years ?? []).map((y) => (
              <button key={y} type="button" className={view === "month" && y === shown ? "active" : ""} onClick={() => { setView("month"); setYear(y); }}>{y}</button>
            ))}
          </div>
        </div>
      }
    >
      <div style={{ opacity: loading ? 0.6 : 1 }}>
        {data?.figure && data.figure.data.length
          ? <PlotlyChart figure={data.figure} height={340} ariaLabel="Realizado e orçado do projeto" />
          : data && <Empty>Sem valores neste recorte.</Empty>}
      </div>
      <p className="muted small">Azul: realizado · vermelho: projeção do gestor · verde: orçado (ano carregado) ou orçamento do ciclo (CAPEX vinculado).</p>
    </Card>
  );
}

export default function Projects() {
  const navigate = useNavigate();
  const { data, error, reload } = useLoad(() => api<Portfolio>("/projects"));
  const [creating, setCreating] = useState(false);
  if (error) return <Alert>{error}</Alert>;
  if (!data) return <Loading />;
  const { items, kpis: k, figures: f } = data;
  const y = k?.ref_year;
  return (
    <>
      <PageHeader
        title="Projetos"
        subtitle="Carteira de projetos: cronograma, realizado × orçado e situação de cada um. Os números vêm do sistema (centro de custo × conta e CAPEX do ciclo); o orçamento não é alterado aqui."
        actions={data.can_create && <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>Novo projeto</button>}
      />
      {!k || !f ? (
        <Empty>{data.can_create ? 'Nenhum projeto ainda. Use "Novo projeto" e vincule o centro de custo × conta do realizado e a solicitação de CAPEX do ciclo.' : "Nenhum projeto sob a sua responsabilidade."}</Empty>
      ) : (
        <>
          <div className="stats">
            <Stat label={`Orçamento ${k.target_year}`} tone="budget" value={fmtMoney(k.cycle_budget)} hint="CAPEX do ciclo vinculado" />
            <Stat label={`Orçado ${y}`} tone="budget" value={Number(k.budget_year) ? fmtMoney(k.budget_year) : "—"} hint={Number(k.budget_year) ? undefined : "sem orçado no sistema"} />
            <Stat label="Realizado acumulado" value={fmtMoney(k.actual_all)} hint={lines(`${y}: ${fmtMoney(k.actual_year)}`, Number(k.projection_year) ? `+ projeção ${fmtMoney(k.projection_year)}` : null)} />
            <Stat label="Projetos" value={fmtInt(k.count)} hint={`${k.active} em andamento`} />
            <Stat label="Pontos de atenção" tone={k.late_milestones || k.open_high_risks ? "warn" : undefined} value={fmtInt(k.warnings)} hint={lines(`${k.late_milestones} marco(s) atrasado(s)`, `${k.open_high_risks} risco(s) alto(s) em aberto`)} />
          </div>

          <BudgetChart items={items} />

          <div className="grid-2">
            <Card title={`Realizado × orçado por projeto`}>
              <PlotlyChart figure={f.compare} height={320} ariaLabel="Realizado e orçado por projeto" />
            </Card>
            <Card title="Curva S · realizado acumulado">
              <PlotlyChart figure={f.scurve} height={320} ariaLabel="Realizado acumulado por projeto" />
              <p className="muted small">Linha cheia: realizado. Tracejada: com a projeção do gestor até dezembro.</p>
            </Card>
          </div>

          <div className="project-cards">
            {items.map((p) => {
              const s = p.summary;
              const ahead = s.spent_pct !== null && s.time_pct !== null && s.spent_pct > s.time_pct + 0.1;
              return (
                <article key={p.id} className="project-card" onClick={() => navigate(`/projetos/${p.id}`)}>
                  <header>
                    <div>
                      <Link to={`/projetos/${p.id}`} onClick={(e) => e.stopPropagation()}><strong>{p.code}</strong> · {p.name}</Link>
                      <div className="muted small">{p.status_label}{p.owner ? ` · ${p.owner}` : ""}{p.cost_center ? ` · ${p.cost_center}` : ""}</div>
                    </div>
                    <Badge tone={LIGHT_TONE[s.light]}>{s.light_label}</Badge>
                  </header>
                  <div className="project-card-numbers">
                    <div><span className="muted small">Realizado {y}</span><strong>{fmtMoney(s.actual_year)}</strong></div>
                    <div><span className="muted small">Orçado {y}</span><strong>{Number(s.budget_year) ? fmtMoney(s.budget_year) : "—"}</strong></div>
                    <div><span className="muted small">Orçamento {s.target_year}</span><strong>{Number(s.cycle_budget) ? fmtMoney(s.cycle_budget) : "—"}</strong></div>
                  </div>
                  <Spark actual={p.monthly} projection={p.monthly_projection} months={data.months ?? []} />
                  <Meter label="Gasto do orçado" value={s.spent_pct} tone={ahead || (s.spent_pct ?? 0) > 1 ? "bad" : "budget"} />
                  <footer className="small project-card-meters">
                    <Meter label="Prazo decorrido" value={s.time_pct} tone="time" />
                    <Meter label="Marcos concluídos" value={s.progress_pct === null ? null : s.progress_pct / 100} tone="time" />
                    {p.next_milestone
                      ? <>Próximo marco: <strong>{p.next_milestone.title}</strong> · {fmtDay(p.next_milestone.due_date)}</>
                      : <span className="muted">{p.milestones ? "Todos os marcos concluídos" : "Sem marcos cadastrados"}</span>}
                    {s.warnings.length > 0 && <ul className="plain-list project-card-warn">{s.warnings.map((w) => <li key={w}>{w}</li>)}</ul>}
                  </footer>
                </article>
              );
            })}
          </div>

          <Card title="Todos os projetos">
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Projeto</th><th>Situação</th><th>Farol</th><th className="right">Realizado {y}</th><th className="right">Orçado {y}</th>
                    <th className="right">% gasto</th><th className="right">% prazo</th><th className="right">Orçamento {k.target_year}</th><th>Prazo</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((p) => (
                    <tr key={p.id}>
                      <td className="project-name"><Link to={`/projetos/${p.id}`}><strong>{p.code}</strong> · {p.name}</Link></td>
                      <td>{p.status_label}</td>
                      <td><Badge tone={LIGHT_TONE[p.summary.light]}>{p.summary.light_label}</Badge></td>
                      <td className="right">{fmtMoney(p.summary.actual_year)}</td>
                      <td className="right">{Number(p.summary.budget_year) ? fmtMoney(p.summary.budget_year) : "—"}</td>
                      <td className="right">{pct(p.summary.spent_pct)}</td>
                      <td className="right">{pct(p.summary.time_pct)}</td>
                      <td className="right">{Number(p.summary.cycle_budget) ? fmtMoney(p.summary.cycle_budget) : "—"}</td>
                      <td className="small">{fmtDay(p.start_actual ?? p.start_planned)} a {fmtDay(p.end_actual ?? p.end_planned)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <Card title="Prazo · cronograma">
            <PlotlyChart figure={f.gantt} height={Math.max(200, 70 * items.length + 100)} ariaLabel="Cronograma dos projetos" />
            {items.some((p) => !p.end_planned && !p.end_actual) && <p className="muted small">Sem término previsto, a barra vai só até hoje, em tom claro. Preencha as datas e os marcos em cada projeto.</p>}
          </Card>
        </>
      )}
      {creating && <ProjectForm onClose={() => setCreating(false)} onSaved={reload} />}
    </>
  );
}

// ---------------------------------------------------------------- formulário (novo e edição)

interface Options {
  cost_centers: { id: number; label: string }[];
  accounts: { id: number; label: string }[];
  capex_requests: { cost_center_id: number; capex_code: string; label: string; is_project: boolean; total: string }[];
  users: { id: number; label: string }[];
  status: { value: string; label: string }[];
}

export interface ProjectEditable extends Partial<ProjectBase> {
  owner_name?: string | null; objective?: string | null; scope?: string | null; benefits?: string | null; notes?: string | null;
  fact_keys?: { cost_center_id: number; account_id: number }[];
  capex_links?: { cost_center_id: number; capex_code: string }[];
}

export function ProjectForm({ project, onClose, onSaved }: { project?: ProjectEditable; onClose: () => void; onSaved: () => void }) {
  const navigate = useNavigate();
  const opts = useLoad(() => api<Options>("/projects/options"));
  const [f, setF] = useState<ProjectEditable>(project ?? { status: "IN_PROGRESS", fact_keys: [], capex_links: [] });
  const [keyCc, setKeyCc] = useState("");
  const [keyAcc, setKeyAcc] = useState("");
  const [capex, setCapex] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const o = opts.data;
  const set = (k: keyof ProjectEditable) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value || null });
  const ccLabel = (id: number) => o?.cost_centers.find((c) => c.id === id)?.label ?? `CC ${id}`;
  const accLabel = (id: number) => o?.accounts.find((a) => a.id === id)?.label ?? `Conta ${id}`;
  async function save() {
    setErr(null);
    setBusy(true);
    const body = {
      code: f.code, name: f.name, status: f.status, cost_center_id: f.cost_center_id ? Number(f.cost_center_id) : null,
      owner_user_id: f.owner_user_id ? Number(f.owner_user_id) : null, owner_name: f.owner_name ?? null, sponsor: f.sponsor ?? null,
      start_planned: f.start_planned || null, end_planned: f.end_planned || null, start_actual: f.start_actual || null, end_actual: f.end_actual || null,
      objective: f.objective ?? null, scope: f.scope ?? null, benefits: f.benefits ?? null, notes: f.notes ?? null,
      fact_keys: f.fact_keys ?? [], capex_links: f.capex_links ?? [],
    };
    try {
      const saved = await api<{ id: number }>(project?.id ? `/projects/${project.id}` : "/projects", { method: project?.id ? "PATCH" : "POST", body: JSON.stringify(body) });
      onSaved();
      onClose();
      if (!project?.id) navigate(`/projetos/${saved.id}`);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      wide
      title={project?.id ? `Editar ${project.code}` : "Novo projeto"}
      onClose={onClose}
      footer={<>
        <button type="button" className="btn btn-ghost" onClick={onClose}>Cancelar</button>
        <button type="button" className="btn btn-primary" disabled={busy || !f.code || !f.name} onClick={save}>Salvar</button>
      </>}
    >
      {opts.error && <Alert>{opts.error}</Alert>}
      <div className="form-row">
        <label>Código (ordem/PEP do SAP)<input value={f.code ?? ""} onChange={set("code")} placeholder="600561" /></label>
        <label>Nome<input value={f.name ?? ""} onChange={set("name")} placeholder="Transformação Digital da Controladoria" /></label>
        <label>Situação
          <select value={f.status ?? "IN_PROGRESS"} onChange={set("status")}>{o?.status.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}</select>
        </label>
      </div>
      <div className="form-row">
        <label>Centro de custo responsável
          <select value={f.cost_center_id ?? ""} onChange={set("cost_center_id")}><option value="">—</option>{o?.cost_centers.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}</select>
        </label>
        {o && o.users.length > 0 && (
          <label>Responsável (usuário: vê e edita)
            <select value={f.owner_user_id ?? ""} onChange={set("owner_user_id")}><option value="">—</option>{o.users.map((u) => <option key={u.id} value={u.id}>{u.label}</option>)}</select>
          </label>
        )}
        <label>Responsável (nome, se não for usuário)<input value={f.owner_name ?? ""} onChange={set("owner_name")} /></label>
        <label>Patrocinador<input value={f.sponsor ?? ""} onChange={set("sponsor")} /></label>
      </div>
      <div className="form-row">
        <label>Início previsto<input type="date" value={f.start_planned ?? ""} onChange={set("start_planned")} /></label>
        <label>Término previsto<input type="date" value={f.end_planned ?? ""} onChange={set("end_planned")} /></label>
        <label>Início real<input type="date" value={f.start_actual ?? ""} onChange={set("start_actual")} /></label>
        <label>Término real<input type="date" value={f.end_actual ?? ""} onChange={set("end_actual")} /></label>
      </div>
      <label>Objetivo<textarea rows={2} value={f.objective ?? ""} onChange={set("objective")} /></label>
      <label>Escopo<textarea rows={2} value={f.scope ?? ""} onChange={set("scope")} /></label>
      <label>Benefícios esperados<textarea rows={2} value={f.benefits ?? ""} onChange={set("benefits")} /></label>

      <h3 className="form-section">Realizado e orçado do sistema (centro de custo × conta)</h3>
      <p className="muted small">Os lançamentos destas combinações (realizado, projeção e orçado de referência) são do projeto. Ex.: TDC = VP de Controladoria × 6090201002 Intangível em andamento.</p>
      <ul className="chip-list">
        {(f.fact_keys ?? []).map((k) => (
          <li key={`${k.cost_center_id}-${k.account_id}`} className="filter-chip">
            {ccLabel(k.cost_center_id)} × {accLabel(k.account_id)}
            <button type="button" aria-label="Remover vínculo" onClick={() => setF({ ...f, fact_keys: f.fact_keys!.filter((x) => x !== k) })}>✕</button>
          </li>
        ))}
      </ul>
      <div className="form-row">
        <label>Centro de custo<select value={keyCc} onChange={(e) => setKeyCc(e.target.value)}><option value="">—</option>{o?.cost_centers.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}</select></label>
        <label>Conta<select value={keyAcc} onChange={(e) => setKeyAcc(e.target.value)}><option value="">—</option>{o?.accounts.map((a) => <option key={a.id} value={a.id}>{a.label}</option>)}</select></label>
        <div className="inline-controls form-add">
          <button type="button" className="btn btn-sm" disabled={!keyCc || !keyAcc} onClick={() => {
            const k = { cost_center_id: Number(keyCc), account_id: Number(keyAcc) };
            if (!(f.fact_keys ?? []).some((x) => x.cost_center_id === k.cost_center_id && x.account_id === k.account_id)) setF({ ...f, fact_keys: [...(f.fact_keys ?? []), k] });
            setKeyCc(""); setKeyAcc("");
          }}>Vincular</button>
        </div>
      </div>

      <h3 className="form-section">Orçamento do ciclo (solicitações de CAPEX)</h3>
      <ul className="chip-list">
        {(f.capex_links ?? []).map((c) => (
          <li key={`${c.cost_center_id}-${c.capex_code}`} className="filter-chip">
            {o?.capex_requests.find((r) => r.cost_center_id === c.cost_center_id && r.capex_code === c.capex_code)?.label ?? `${ccLabel(c.cost_center_id)} · ${c.capex_code}`}
            <button type="button" aria-label="Remover vínculo" onClick={() => setF({ ...f, capex_links: f.capex_links!.filter((x) => x !== c) })}>✕</button>
          </li>
        ))}
      </ul>
      <div className="form-row">
        <label>Solicitação de CAPEX
          <select value={capex} onChange={(e) => setCapex(e.target.value)}>
            <option value="">—</option>
            {o?.capex_requests.map((r) => <option key={`${r.cost_center_id}|${r.capex_code}`} value={`${r.cost_center_id}|${r.capex_code}`}>{r.label} · {fmtMoney(r.total)}{r.is_project ? " · projeto" : ""}</option>)}
          </select>
        </label>
        <div className="inline-controls form-add">
          <button type="button" className="btn btn-sm" disabled={!capex} onClick={() => {
            const [cc, code] = capex.split("|");
            const c = { cost_center_id: Number(cc), capex_code: code };
            if (!(f.capex_links ?? []).some((x) => x.cost_center_id === c.cost_center_id && x.capex_code === c.capex_code)) setF({ ...f, capex_links: [...(f.capex_links ?? []), c] });
            setCapex("");
          }}>Vincular</button>
        </div>
      </div>
      {err && <div className="error-text">{err}</div>}
    </Modal>
  );
}
