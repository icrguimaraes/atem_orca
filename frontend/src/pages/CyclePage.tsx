import { useState } from "react";
import { api, type Cycle, type Parameter, type Version } from "../api";
import { useAuth } from "../auth";
import { PackageManagersCard } from "../components/PackageManagersCard";
import { Alert, Badge, Card, Loading, PageHeader, Stat, useLoad } from "../components/ui";
import { CYCLE_STATUS, PARAM_LABELS, fmtDate, fmtDateTime } from "../labels";

const PERCENT_KEYS = new Set(["alert.growth_pct", "alert.reduction_pct", "alert.history_band_pct", "personnel.salary_adjustment_pct"]);

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

/** Parâmetro dicionário {conta: peso} (ex.: rateio de encargos): uma linha por conta, com o peso normalizado em %. */
function SplitView({ value }: { value: Record<string, unknown> }) {
  const entries = Object.entries(value).map(([k, v]) => [k, Number(v)] as const).filter(([, v]) => v > 0);
  const total = entries.reduce((acc, [, v]) => acc + v, 0);
  if (!entries.length || !total) return <strong>— (sem rateio: conta única)</strong>;
  return (
    <div className="small mono">
      {entries
        .sort((a, b) => b[1] - a[1])
        .map(([k, v]) => (
          <div key={k}>
            {k}: {((v / total) * 100).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%
          </div>
        ))}
    </div>
  );
}

function display(key: string, value: unknown): string {
  if (key.endsWith("_account")) return String(value);  // código de conta: sem separador de milhar
  if (typeof value === "number" && PERCENT_KEYS.has(key)) return `${(value * 100).toLocaleString("pt-BR")}%`;
  return typeof value === "number" ? value.toLocaleString("pt-BR") : JSON.stringify(value);
}

function ParamRow({ cycleId, p, editable, onSaved }: { cycleId: number; p: Parameter; editable: boolean; onSaved: () => void }) {
  const isPct = PERCENT_KEYS.has(p.key);
  const isJson = isObject(p.value);
  const initial = isJson ? JSON.stringify(p.value, null, 2) : String(isPct ? Number(p.value) * 100 : p.value);
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(initial);
  const [error, setError] = useState<string | null>(null);

  function parse(): { ok: true; value: unknown } | { ok: false; error: string } {
    if (!isJson) {
      const number = Number(value.replace(",", "."));
      return Number.isNaN(number) ? { ok: false, error: "Informe um número" } : { ok: true, value: isPct ? number / 100 : number };
    }
    let parsed: unknown;
    try {
      parsed = value.trim() ? JSON.parse(value) : {};
    } catch {
      return { ok: false, error: 'JSON inválido: use {"conta": peso, ...}' };
    }
    if (!isObject(parsed)) return { ok: false, error: 'Informe um objeto JSON {"conta": peso, ...}' };
    const bad = Object.entries(parsed).find(([, v]) => typeof v !== "number" || v < 0);
    if (bad) return { ok: false, error: `Peso inválido para ${bad[0]}: use um número maior ou igual a zero` };
    return { ok: true, value: parsed };
  }

  async function save() {
    const parsed = parse();
    if (!parsed.ok) return setError(parsed.error);
    try {
      await api(`/cycles/${cycleId}/parameters/${p.key}`, {
        method: "PUT",
        body: JSON.stringify({ value: parsed.value }),
      });
      setEditing(false);
      setError(null);
      onSaved();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <tr>
      <td>
        {PARAM_LABELS[p.key] ?? p.key}
        <div className="muted small mono">{p.key}</div>
      </td>
      <td className="muted small">{p.description}</td>
      <td className="right nowrap">
        {editing && isJson ? (
          <div className="stack">
            <textarea
              className="mono small"
              style={{ minWidth: 240 }}
              rows={Math.min(18, value.split("\n").length + 1)}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              aria-label={PARAM_LABELS[p.key] ?? p.key}
              autoFocus
            />
            <div className="muted small">Pesos relativos (normalizados no cálculo). {"{}"} = tudo na conta de encargos.</div>
            <span className="inline-edit">
              <button className="btn btn-primary btn-sm" onClick={save}>Salvar</button>
              <button className="btn btn-ghost btn-sm" onClick={() => { setValue(initial); setEditing(false); }}>Cancelar</button>
            </span>
          </div>
        ) : editing ? (
          <span className="inline-edit">
            <input value={value} onChange={(e) => setValue(e.target.value)} autoFocus />
            {isPct && "%"}
            <button className="btn btn-primary btn-sm" onClick={save}>Salvar</button>
            <button className="btn btn-ghost btn-sm" onClick={() => setEditing(false)}>Cancelar</button>
          </span>
        ) : (
          <>
            {isJson ? <SplitView value={p.value as Record<string, unknown>} /> : <strong>{display(p.key, p.value)}</strong>}
            {editable && (
              <button className="btn btn-ghost btn-sm" onClick={() => setEditing(true)}>Alterar</button>
            )}
          </>
        )}
        {error && <div className="error-text">{error}</div>}
      </td>
    </tr>
  );
}

export default function CyclePage() {
  const { can } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const { data, error: loadError, reload } = useLoad(async () => {
    const cycles = await api<Cycle[]>("/cycles");
    const cycle = cycles[0];
    if (!cycle) throw new Error("Nenhum ciclo orçamentário cadastrado");
    const [params, versions] = await Promise.all([
      api<Parameter[]>(`/cycles/${cycle.id}/parameters`),
      api<Version[]>(`/cycles/${cycle.id}/versions`),
    ]);
    return { cycle, params, versions };
  });

  if (loadError) return <Alert>{loadError}</Alert>;
  if (!data) return <Loading />;
  const { cycle, params, versions } = data;
  const status = CYCLE_STATUS[cycle.status];

  async function change(action: "open" | "close") {
    const msg = action === "open" ? "Abrir o ciclo para preenchimento pelos gestores?" : "Fechar o ciclo? Os gestores não poderão mais editar.";
    if (!window.confirm(msg)) return;
    try {
      await api(`/cycles/${cycle.id}/${action}`, { method: "POST" });
      reload();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <>
      <PageHeader
        title={cycle.name}
        subtitle="Prazos, status e premissas que controlam alertas e cálculos. Toda alteração fica registrada na auditoria."
        actions={
          can() && (
            <>
              {cycle.status !== "OPEN" && <button className="btn btn-primary" onClick={() => change("open")}>Abrir ciclo</button>}
              {cycle.status === "OPEN" && <button className="btn" onClick={() => change("close")}>Fechar ciclo</button>}
            </>
          )
        }
      />
      {error && <Alert>{error}</Alert>}
      <div className="stats">
        <Stat label="Status" value={<Badge tone={status?.tone ?? "neutral"}>{status?.label ?? cycle.status}</Badge>} />
        <Stat label="Ano orçado" value={cycle.fiscal_year} />
        <Stat label="Realizado de referência" value={cycle.actual_reference_year} />
        <Stat label="Prazo OPEX" value={fmtDate(cycle.opex_deadline)} />
        <Stat label="Prazo CAPEX" value={fmtDate(cycle.capex_deadline)} />
        <Stat label="Prazo Pessoal" value={fmtDate(cycle.personnel_deadline)} />
      </div>
      <Card title="Parâmetros do ciclo">
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Parâmetro</th>
                <th>Descrição</th>
                <th className="right">Valor</th>
              </tr>
            </thead>
            <tbody>
              {params.map((p) => (
                <ParamRow key={p.key} cycleId={cycle.id} p={p} editable={can("CONTROLLER")} onSaved={reload} />
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <PackageManagersCard cycleId={cycle.id} editable={can()} />
      <Card title="Versões do orçamento">
        <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Versão</th>
              <th>Situação</th>
              <th>Motivo</th>
              <th>Criada em</th>
            </tr>
          </thead>
          <tbody>
            {versions.map((v) => (
              <tr key={v.id}>
                <td><strong>{v.label}</strong></td>
                <td>{v.status === "FROZEN" ? <Badge tone="neutral">Congelada</Badge> : <Badge tone="good">Em elaboração</Badge>}</td>
                <td>{v.reason ?? "—"}</td>
                <td>{fmtDateTime(v.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      </Card>
    </>
  );
}
