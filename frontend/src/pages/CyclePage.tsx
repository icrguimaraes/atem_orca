import { useState } from "react";
import { api, type Cycle, type Parameter, type Version } from "../api";
import { useAuth } from "../auth";
import { PackageManagersCard } from "../components/PackageManagersCard";
import { Alert, Badge, Card, Loading, PageHeader, Stat, useLoad } from "../components/ui";
import { CYCLE_STATUS, PARAM_LABELS, fmtDate, fmtDateTime } from "../labels";

const PERCENT_KEYS = new Set(["alert.growth_pct", "alert.reduction_pct", "alert.history_band_pct", "personnel.salary_adjustment_pct"]);

function display(key: string, value: unknown): string {
  if (key.endsWith("_account")) return String(value);  // código de conta: sem separador de milhar
  if (typeof value === "number" && PERCENT_KEYS.has(key)) return `${(value * 100).toLocaleString("pt-BR")}%`;
  return typeof value === "number" ? value.toLocaleString("pt-BR") : JSON.stringify(value);
}

function ParamRow({ cycleId, p, editable, onSaved }: { cycleId: number; p: Parameter; editable: boolean; onSaved: () => void }) {
  const isPct = PERCENT_KEYS.has(p.key);
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(String(isPct ? Number(p.value) * 100 : p.value));
  const [error, setError] = useState<string | null>(null);

  async function save() {
    const number = Number(value.replace(",", "."));
    if (Number.isNaN(number)) return setError("Informe um número");
    try {
      await api(`/cycles/${cycleId}/parameters/${p.key}`, {
        method: "PUT",
        body: JSON.stringify({ value: isPct ? number / 100 : number }),
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
        {editing ? (
          <span className="inline-edit">
            <input value={value} onChange={(e) => setValue(e.target.value)} autoFocus />
            {isPct && "%"}
            <button className="btn btn-primary btn-sm" onClick={save}>Salvar</button>
            <button className="btn btn-ghost btn-sm" onClick={() => setEditing(false)}>Cancelar</button>
          </span>
        ) : (
          <>
            <strong>{display(p.key, p.value)}</strong>
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
        </table>
      </Card>
    </>
  );
}
