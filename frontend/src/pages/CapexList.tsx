import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type CapexSummary } from "../api";
import { useAuth } from "../auth";
import { MonthlyBars, RankBars, SERIES, StatusBar } from "../components/charts";
import { Alert, Badge, Card, Empty, Loading, PageHeader, SearchBox, Stat, useLoad } from "../components/ui";
import { CYCLE_STATUS, SUBMISSION_STATUS, fmtCompact, fmtDate, fmtInt, fmtMoney } from "../labels";

const ORDER = ["DRAFT", "IN_PROGRESS", "ADJUSTMENT_REQUESTED", "SUBMITTED", "UNDER_REVIEW", "APPROVED", "CONSOLIDATED"];

export default function CapexList() {
  const { can } = useAuth();
  const navigate = useNavigate();
  const isController = can("CONTROLLER");
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const { data, error } = useLoad(() => api<CapexSummary>("/capex/summary"));

  const rows = useMemo(
    () =>
      (data?.rows ?? []).filter(
        (r) => (!status || r.status === status) && (!q || `${r.code} ${r.name} ${r.manager_name ?? ""}`.toLowerCase().includes(q.toLowerCase())),
      ),
    [data, status, q],
  );

  if (error) return <Alert>{error}</Alert>;
  if (!data) return <Loading />;
  const y = data.years;
  const total = data.rows.reduce((s, r) => s + Number(r.total), 0);
  const refActual = data.rows.reduce((s, r) => s + Number(r.ref_actual), 0);
  const requests = data.rows.reduce((s, r) => s + r.requests, 0);
  const projects = data.rows.reduce((s, r) => s + r.projects, 0);
  const critical = data.rows.reduce((s, r) => s + r.critical, 0);
  const started = data.rows.filter((r) => r.requests > 0).length;
  const done = ["SUBMITTED", "UNDER_REVIEW", "APPROVED", "CONSOLIDATED"].reduce((s, k) => s + (data.status_counts[k] ?? 0), 0);

  return (
    <>
      <PageHeader
        title={`Orçamento CAPEX ${y.target}`}
        subtitle={`Solicitações de investimento por centro de custo: projetos e aquisições de ativo (valor > R$ 1.200 e vida útil > 12 meses), com cronograma de desembolso. Prazo: ${fmtDate(data.cycle.deadline)}.`}
      />
      {data.cycle.status !== "OPEN" && (
        <Alert tone="warn">
          Ciclo <strong>{CYCLE_STATUS[data.cycle.status]?.label.toLowerCase()}</strong>: gestores ainda não conseguem editar.
          {can() && <> Abra o ciclo em <Link className="link" to="/ciclo">Ciclo e parâmetros</Link>.</>}
        </Alert>
      )}

      <div className="stats">
        <Stat label="Centros de custo" value={fmtInt(data.rows.length)} hint={`${fmtInt(started)} com solicitações`} />
        <Stat label="Enviados ou aprovados" value={`${fmtInt(done)} de ${fmtInt(data.rows.length)}`} tone={done === data.rows.length && done > 0 ? "good" : undefined} />
        <Stat label={`CAPEX solicitado ${y.target}`} value={fmtCompact(total)} tone="warn" hint={`${fmtInt(requests)} solicitações · ${fmtInt(projects)} projetos`} />
        <Stat label={`Realizado CAPEX ${y.ref}`} value={fmtCompact(refActual)} hint="contas de ativo no realizado importado" />
        <Stat label="Pendências críticas" value={fmtInt(critical)} tone={critical ? "bad" : "good"} hint="bloqueiam envio e aprovação" />
      </div>

      <Card title="Andamento">
        <StatusBar items={ORDER.map((k) => ({ key: k, label: SUBMISSION_STATUS[k].label, count: data.status_counts[k] ?? 0, tone: SUBMISSION_STATUS[k].tone }))} />
      </Card>

      {total > 0 && (
        <>
          <Card title={`Cronograma de desembolso ${y.target}`} actions={<span className="muted small">pico em {fmtMoney(Math.max(...data.monthly.map(Number)))}</span>}>
            <MonthlyBars height={220} series={[{ label: `CAPEX ${y.target}`, color: SERIES.ref, values: data.monthly.map(Number) }]} />
          </Card>
          <div className="grid-2">
            <Card title="Por tipo de projeto">
              <RankBars rows={data.by_type.map((t) => ({ label: t.label, value: Number(t.total) }))} color={SERIES.budget} />
            </Card>
            <Card title="Por conta de ativo">
              <RankBars rows={data.by_account.map((a) => ({ label: a.label, sub: a.code, value: Number(a.total) }))} limit={8} />
            </Card>
          </div>
        </>
      )}

      <Card
        actions={
          <div className="inline-controls">
            <SearchBox value={q} onChange={setQ} placeholder="Buscar CC ou gestor" />
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">Todos os status</option>
              {ORDER.map((s) => (
                <option key={s} value={s}>{SUBMISSION_STATUS[s].label} ({data.status_counts[s] ?? 0})</option>
              ))}
            </select>
          </div>
        }
      >
        {rows.length === 0 ? (
          <Empty>
            {data.rows.length === 0
              ? isController
                ? "Nenhum centro de custo cadastrado."
                : "Você ainda não está vinculado como gestor de nenhum centro de custo. Peça à Controladoria para vincular seu usuário em Cadastros."
              : "Nenhum centro de custo com esses filtros."}
          </Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Centro de custo</th>
                  {isController && <th>Gestor</th>}
                  <th>Situação</th>
                  <th className="right">Solicitações</th>
                  <th className="right">{y.ref} realizado</th>
                  <th className="right">{y.target} solicitado</th>
                  <th className="right">Pendências</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.cost_center_id} className="clickable" onClick={() => navigate(`/capex/${r.cost_center_id}`)}>
                    <td>
                      <Link className="link" to={`/capex/${r.cost_center_id}`} onClick={(e) => e.stopPropagation()}>{r.name}</Link>
                      <div className="muted small mono">{r.company_code} · {r.code}</div>
                    </td>
                    {isController && <td>{r.manager_name ?? "—"}</td>}
                    <td><Badge tone={SUBMISSION_STATUS[r.status]?.tone ?? "neutral"}>{SUBMISSION_STATUS[r.status]?.label ?? r.status}</Badge></td>
                    <td className="right">
                      {r.requests ? `${fmtInt(r.requests)}` : "—"}
                      {r.projects > 0 && <div className="muted small">{fmtInt(r.projects)} projeto(s)</div>}
                    </td>
                    <td className="right">{Number(r.ref_actual) ? fmtCompact(r.ref_actual) : "—"}</td>
                    <td className="right"><strong>{Number(r.total) ? fmtCompact(r.total) : "—"}</strong></td>
                    <td className="right">{r.critical ? <Badge tone="bad">{r.critical}</Badge> : r.requests ? <Badge tone="good">ok</Badge> : "—"}</td>
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
