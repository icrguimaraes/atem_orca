import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { Alert, Badge, Empty, Loading, Modal, PageHeader, Stat, useLoad } from "../components/ui";
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

export default function Projects() {
  const { data, error, reload } = useLoad(() => api<{ items: ProjectBase[]; can_create: boolean }>("/projects"));
  const [creating, setCreating] = useState(false);
  if (error) return <Alert>{error}</Alert>;
  if (!data) return <Loading />;
  const items = data.items;
  const y = items[0]?.summary.ref_year;
  const sum = (f: (p: ProjectBase) => string) => items.reduce((acc, p) => acc + Number(f(p)), 0);
  return (
    <>
      <PageHeader
        title="Projetos"
        subtitle="Cadastro, cronograma, riscos e números de cada projeto. Realizado e orçado vêm do sistema (centro de custo × conta e CAPEX do ciclo); o orçamento não é alterado aqui."
        actions={data.can_create && <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>Novo projeto</button>}
      />
      {items.length > 0 && (
        <div className="stats">
          <Stat label="Projetos" value={fmtInt(items.length)} hint={`${items.filter((p) => p.status === "IN_PROGRESS").length} em andamento`} />
          <Stat label={`Realizado ${y}`} value={fmtMoney(sum((p) => p.summary.actual_year))} hint="lançamentos do sistema" />
          <Stat label={`Orçado ${y}`} value={fmtMoney(sum((p) => p.summary.budget_year))} tone="budget" />
          <Stat label={`Orçamento ${items[0].summary.target_year}`} value={fmtMoney(sum((p) => p.summary.cycle_budget))} tone="budget" hint="CAPEX do ciclo vinculado" />
        </div>
      )}
      {items.length === 0 ? (
        <Empty>{data.can_create ? 'Nenhum projeto ainda. Use "Novo projeto" e vincule o centro de custo × conta do realizado e a solicitação de CAPEX do ciclo.' : "Nenhum projeto sob a sua responsabilidade."}</Empty>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Projeto</th><th>Situação</th><th>Farol</th><th className="right">Realizado {y}</th><th className="right">Orçado {y}</th>
                <th className="right">% gasto</th><th className="right">% prazo</th><th className="right">Orçamento {items[0].summary.target_year}</th><th>Prazo</th>
              </tr>
            </thead>
            <tbody>
              {items.map((p) => (
                <tr key={p.id}>
                  <td className="project-name"><Link to={`/projetos/${p.id}`}><strong>{p.code}</strong> · {p.name}</Link>{p.owner && <div className="muted small">{p.owner}</div>}</td>
                  <td>{p.status_label}</td>
                  <td><Badge tone={LIGHT_TONE[p.summary.light]}>{p.summary.light_label}</Badge>{p.summary.warnings.length > 0 && <div className="muted small">{p.summary.warnings.length} apontamento(s)</div>}</td>
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
