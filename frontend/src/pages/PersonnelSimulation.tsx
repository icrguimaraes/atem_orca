import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type PersonnelOptions, type PersonnelScenario, type WhatIfResult } from "../api";
import { useAuth } from "../auth";
import { DivergingBars, Legend, MonthlyBars, SERIES } from "../components/charts";
import { Alert, Badge, Card, Empty, Loading, PageHeader, Stat, useLoad } from "../components/ui";
import { MONTHS, fmtCompact, fmtInt, fmtMoney, fmtPct } from "../labels";

/** What-if de pessoal: multiplicador por contrato, reajuste e data-base, comparados ao cenário base. */
export default function PersonnelSimulation() {
  const { can } = useAuth();
  const planner = can("CONTROLLER", "HR");
  const opts = useLoad(() => api<PersonnelOptions>("/personnel/options"));
  const scenarios = useLoad(() => api<PersonnelScenario[]>("/personnel/scenarios"));
  const [mults, setMults] = useState<Record<string, string>>({});
  const [adj, setAdj] = useState("");
  const [month, setMonth] = useState("1");
  const [ccs, setCcs] = useState<string[]>([]);
  const [result, setResult] = useState<WhatIfResult | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!opts.data) return;
    const s = opts.data.scenario;
    setMults(Object.fromEntries(Object.entries(s.multipliers).map(([k, v]) => [k, String(Number(v))])));
    setAdj(String(Number(s.salary_adjustment_pct) * 100));
    setMonth(String(s.adjustment_month));
  }, [opts.data]);

  const body = () => ({
    multipliers: Object.fromEntries(Object.entries(mults).map(([k, v]) => [k, Number(String(v).replace(",", "."))])),
    salary_adjustment_pct: Number(String(adj).replace(",", ".")) / 100,
    adjustment_month: Number(month),
    cost_center_ids: ccs.length ? ccs.map(Number) : null,
  });

  async function simulate() {
    setBusy(true);
    setError(null);
    setMsg(null);
    try {
      setResult(await api<WhatIfResult>("/personnel/what-if", { method: "POST", body: JSON.stringify(body()) }));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    setError(null);
    try {
      const b = body();
      await api("/personnel/scenarios", { method: "POST", body: JSON.stringify({ name, multipliers: b.multipliers, salary_adjustment_pct: b.salary_adjustment_pct, adjustment_month: b.adjustment_month }) });
      setMsg(`Cenário "${name}" salvo. Ele não altera o orçamento até ser definido como base.`);
      setName("");
      scenarios.reload();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function makeBaseline(s: PersonnelScenario) {
    if (!window.confirm(`Usar "${s.name}" como base? Todos os orçamentos de pessoal passam a ser calculados com ele.`)) return;
    setError(null);
    try {
      await api(`/personnel/scenarios/${s.id}/baseline`, { method: "POST" });
      setMsg(`"${s.name}" agora é o cenário base do orçamento de pessoal.`);
      scenarios.reload();
      opts.reload();
      setResult(null);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function remove(s: PersonnelScenario) {
    if (!window.confirm(`Excluir o cenário "${s.name}"?`)) return;
    try {
      await api(`/personnel/scenarios/${s.id}`, { method: "DELETE" });
      scenarios.reload();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  function load(s: PersonnelScenario) {
    setMults(Object.fromEntries(Object.entries(s.multipliers).map(([k, v]) => [k, String(Number(v))])));
    setAdj(String(Number(s.salary_adjustment_pct) * 100));
    setMonth(String(s.adjustment_month));
    setResult(null);
  }

  if (opts.error) return <Alert>{opts.error}</Alert>;
  if (!opts.data) return <Loading />;
  const o = opts.data;
  const diff = result ? Number(result.difference) : 0;

  return (
    <>
      <PageHeader
        title="Simulação de pessoal"
        subtitle={`Compare o cenário base (${o.scenario.name}) com outro multiplicador por tipo de contrato, reajuste ou data-base. PJ não recebe multiplicador. A simulação não altera nenhum orçamento.`}
        actions={<Link to="/pessoal" className="btn btn-ghost">Voltar</Link>}
      />
      {error && <Alert>{error}</Alert>}
      {msg && <Alert tone="good">{msg}</Alert>}

      <Card title="Premissas da simulação">
        <div className="stack">
          <div className="form-row">
            {o.contract_types.map((c) => (
              <label key={c.code}>
                Multiplicador {c.name}
                <input
                  inputMode="decimal"
                  value={c.apply_multiplier ? (mults[c.code] ?? "") : "não se aplica"}
                  disabled={!c.apply_multiplier}
                  onChange={(e) => setMults((m) => ({ ...m, [c.code]: e.target.value }))}
                />
                <span className="muted small">base: {c.apply_multiplier ? Number(o.scenario.multipliers[c.code] ?? 1).toLocaleString("pt-BR") : "sem multiplicador"}</span>
              </label>
            ))}
          </div>
          <div className="form-row">
            <label>Reajuste salarial (%)
              <input inputMode="decimal" value={adj} onChange={(e) => setAdj(e.target.value)} />
              <span className="muted small">base: {fmtPct(o.scenario.salary_adjustment_pct)}</span>
            </label>
            <label>Data-base (mês do reajuste)
              <select value={month} onChange={(e) => setMonth(e.target.value)}>
                {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
              </select>
            </label>
            <label>Centros de custo (vazio = todos os seus)
              <select multiple value={ccs} onChange={(e) => setCcs(Array.from(e.target.selectedOptions).map((x) => x.value))} style={{ minHeight: 96 }}>
                {o.cost_centers.map((c) => <option key={c.id} value={c.id}>{c.code} · {c.name}</option>)}
              </select>
            </label>
          </div>
          <div className="inline-controls">
            <button className="btn btn-primary" disabled={busy} onClick={simulate}>{busy ? "Calculando…" : "Simular"}</button>
            {planner && result && (
              <>
                <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Nome do cenário (ex.: CLT 2,0)" style={{ minHeight: 44 }} />
                <button className="btn" disabled={!name.trim()} onClick={save}>Salvar cenário</button>
              </>
            )}
          </div>
        </div>
      </Card>

      {result && (
        <>
          <div className="stats">
            <Stat label={result.baseline.name === "Base" ? "Cenário base" : `Base · ${result.baseline.name}`} value={fmtCompact(result.base.annual)} hint={`${fmtInt(result.base.headcount_start)} → ${fmtInt(result.base.headcount_end)} pessoas`} />
            <Stat label="Simulado" value={fmtCompact(result.simulation.annual)} tone="warn" />
            <Stat label="Impacto no ano" value={`${diff > 0 ? "+" : ""}${fmtCompact(diff)}`} tone={diff > 0 ? "bad" : diff < 0 ? "good" : undefined} hint={result.difference_pct ? `${fmtPct(result.difference_pct)} sobre a base` : undefined} />
            <Stat label="Sem multiplicador" value={result.simulated.ignored_multiplier_for.join(", ") || "—"} hint="contratos fora da simulação de encargos" />
          </div>
          <Card title="Custo mensal · base × simulado">
            <Legend items={[{ label: "Base", color: SERIES.past }, { label: "Simulado", color: SERIES.ref }]} />
            <MonthlyBars
              height={220}
              series={[
                { label: "Base", color: SERIES.past, values: result.base.monthly.map(Number) },
                { label: "Simulado", color: SERIES.ref, values: result.simulation.monthly.map(Number) },
              ]}
            />
          </Card>
          <div className="grid-2">
            <Card title="Impacto por centro de custo">
              {result.by_cost_center.some((r) => Number(r.difference)) ? (
                <DivergingBars rows={result.by_cost_center.filter((r) => Number(r.difference)).slice(0, 12).map((r) => ({ label: r.name, sub: r.code, delta: Number(r.difference), from: Number(r.base), to: Number(r.simulated) }))} />
              ) : (
                <Empty>Sem diferença entre os cenários.</Empty>
              )}
            </Card>
            <Card title="Por tipo de contrato">
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>Contrato</th><th className="right">Pessoas</th><th className="right">Base</th><th className="right">Simulado</th><th className="right">Diferença</th></tr></thead>
                  <tbody>
                    {result.by_contract.map((c) => (
                      <tr key={c.contract}>
                        <td>{c.contract}</td>
                        <td className="right">{fmtInt(c.people)}</td>
                        <td className="right">{fmtMoney(c.base)}</td>
                        <td className="right">{fmtMoney(c.simulated)}</td>
                        <td className="right"><strong>{fmtMoney(c.difference)}</strong></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>
        </>
      )}

      <Card title="Cenários salvos">
        {!scenarios.data ? (
          <Loading />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Cenário</th><th>Multiplicadores</th><th className="right">Reajuste</th><th>Data-base</th><th /></tr></thead>
              <tbody>
                {scenarios.data.map((s) => (
                  <tr key={s.id}>
                    <td>{s.name} {s.is_baseline && <Badge tone="good">base do orçamento</Badge>}</td>
                    <td className="small">{Object.entries(s.multipliers).filter(([k]) => !s.ignored_multiplier_for.includes(k)).map(([k, v]) => `${k} ${Number(v).toLocaleString("pt-BR")}`).join(" · ")}</td>
                    <td className="right">{fmtPct(s.salary_adjustment_pct)}</td>
                    <td>{MONTHS[s.adjustment_month - 1]}</td>
                    <td className="row-actions">
                      <button className="btn btn-sm btn-ghost" onClick={() => load(s)}>Carregar</button>
                      {planner && !s.is_baseline && (
                        <>
                          <button className="btn btn-sm" onClick={() => makeBaseline(s)}>Usar como base</button>
                          <button className="btn btn-sm btn-ghost" onClick={() => remove(s)} aria-label={`Excluir ${s.name}`}>✕</button>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
