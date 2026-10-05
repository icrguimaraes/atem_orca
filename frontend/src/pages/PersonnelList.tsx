import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type PersonnelSummary } from "../api";
import { useAuth } from "../auth";
import { Legend, MonthlyBars, RankBars, SERIES, StatusBar } from "../components/charts";
import { Alert, Badge, Card, Empty, Loading, PageHeader, SearchBox, Stat, useLoad } from "../components/ui";
import { CYCLE_STATUS, MONTHS, SUBMISSION_STATUS, fmtCompact, fmtDate, fmtInt, fmtPct } from "../labels";

const ORDER = ["DRAFT", "IN_PROGRESS", "ADJUSTMENT_REQUESTED", "SUBMITTED", "UNDER_REVIEW", "APPROVED", "CONSOLIDATED"];
const count = (n: number) => (Number.isInteger(n) ? fmtInt(n) : "");

export default function PersonnelList() {
  const { can } = useAuth();
  const navigate = useNavigate();
  const isPlanner = can("CONTROLLER", "HR");
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const { data, error } = useLoad(() => api<PersonnelSummary>("/personnel/summary"));

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
  const t = data.totals;
  const annual = Number(t.annual);
  const ref = Number(data.ref_annualized);
  const sc = data.scenario;

  return (
    <>
      <PageHeader
        title={`Orçamento de Pessoal ${y.target}`}
        subtitle={`Quadro por centro de custo com promoções, desligamentos, transferências e vagas. Custo = salário × (1 + ${fmtPct(sc.salary_adjustment_pct)} de reajuste a partir de ${MONTHS[sc.adjustment_month - 1]}) × multiplicador do contrato (CLT ${Number(sc.multipliers.CLT ?? 1).toLocaleString("pt-BR")}; PJ sem multiplicador).${data.cycle.deadline ? ` Prazo: ${fmtDate(data.cycle.deadline)}.` : ""}`}
        actions={isPlanner && <Link to="/pessoal/simulacao" className="btn btn-primary">Simular cenário</Link>}
      />
      {data.cycle.status !== "OPEN" && (
        <Alert tone="warn">
          Ciclo <strong>{CYCLE_STATUS[data.cycle.status]?.label.toLowerCase()}</strong>: gestores ainda não conseguem editar.
          {can() && <> Abra o ciclo em <Link className="link" to="/ciclo">Ciclo e parâmetros</Link>.</>}
        </Alert>
      )}

      <div className="stats">
        <Stat label="Headcount hoje" value={fmtInt(t.headcount_start)} hint={`dezembro/${y.target}: ${fmtInt(t.headcount_end)}`} />
        <Stat label="Contratações" value={fmtInt(t.hires)} tone={t.hires ? "good" : undefined} hint={`${fmtInt(t.transfers_in)} transferência(s) recebida(s)`} />
        <Stat label="Desligamentos" value={fmtInt(t.terminations)} tone={t.terminations ? "bad" : undefined} hint={`${fmtInt(t.promotions)} promoção(ões) e reajustes`} />
        <Stat label={`Custo de pessoal ${y.target}`} value={fmtCompact(annual)} tone="warn" hint={ref ? `${fmtPct(String(annual / ref - 1))} vs ${y.ref} anualizado` : "sem realizado de pessoal"} />
        <Stat label={`${y.ref} anualizado`} value={ref ? fmtCompact(ref) : "—"} hint="contas de pessoal no realizado" />
      </div>

      <Card title="Andamento">
        <StatusBar items={ORDER.map((k) => ({ key: k, label: SUBMISSION_STATUS[k].label, count: data.status_counts[k] ?? 0, tone: SUBMISSION_STATUS[k].tone }))} />
      </Card>

      {annual > 0 && (
        <>
          <Card title={`Custo mensal ${y.target}`} actions={<span className="muted small">salário {fmtCompact(t.salary_total)} · encargos e benefícios (multiplicador) {fmtCompact(t.charges_total)}{Number(t.severance_total) ? ` · rescisões ${fmtCompact(t.severance_total)}` : ""}</span>}>
            <Legend items={[{ label: `Custo ${y.target}`, color: SERIES.ref }, ...(ref ? [{ label: `${y.ref} média mensal`, color: "var(--muted)", line: true }] : [])]} />
            <MonthlyBars
              height={220}
              series={[{ label: `Custo ${y.target}`, color: SERIES.ref, values: t.monthly.map(Number) }]}
              line={ref ? { label: `${y.ref} média mensal`, color: "var(--muted)", values: Array(12).fill(ref / 12) } : undefined}
            />
          </Card>
          <div className="grid-2">
            <Card title="Headcount, admissões e desligamentos por mês">
              <Legend items={[{ label: "Admissões", color: SERIES.budget }, { label: "Desligamentos", color: SERIES.prev }, { label: "Headcount", color: "var(--muted)", line: true }]} />
              <MonthlyBars
                height={220}
                format={(n) => fmtInt(Math.round(n))}
                axisFormat={count}
                series={[
                  { label: "Admissões", color: SERIES.budget, values: data.hires_by_month },
                  { label: "Desligamentos", color: SERIES.prev, values: data.terminations_by_month },
                ]}
                line={{ label: "Headcount", color: "var(--muted)", values: t.headcount }}
              />
            </Card>
            <Card title="Maiores custos por centro de custo">
              {rows.length ? (
                <RankBars rows={[...data.rows].sort((a, b) => Number(b.annual) - Number(a.annual)).map((r) => ({ label: r.name, sub: r.code, value: Number(r.annual) }))} limit={8} />
              ) : (
                <Empty>Sem dados.</Empty>
              )}
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
              ? "Nenhum centro de custo com quadro de pessoal. Importe o quadro de funcionários em Importação de dados."
              : "Nenhum centro de custo com esses filtros."}
          </Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Centro de custo</th>
                  <th>Situação</th>
                  <th className="right">Headcount</th>
                  <th className="right">Admissões</th>
                  <th className="right">Desligamentos</th>
                  <th className="right">{y.ref} anualizado</th>
                  <th className="right">{y.target} projetado</th>
                  <th className="right">Var.</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.cost_center_id} className="clickable" onClick={() => navigate(`/pessoal/${r.cost_center_id}`)}>
                    <td>
                      <Link className="link" to={`/pessoal/${r.cost_center_id}`} onClick={(e) => e.stopPropagation()}>{r.name}</Link>
                      <div className="muted small mono">{r.company_code} · {r.code}</div>
                    </td>
                    <td><Badge tone={SUBMISSION_STATUS[r.status]?.tone ?? "neutral"}>{SUBMISSION_STATUS[r.status]?.label ?? r.status}</Badge></td>
                    <td className="right">{fmtInt(r.headcount_start)} → {fmtInt(r.headcount_end)}</td>
                    <td className="right">{r.hires || "—"}</td>
                    <td className="right">{r.terminations || "—"}</td>
                    <td className="right">{Number(r.ref_annualized) ? fmtCompact(r.ref_annualized) : "—"}</td>
                    <td className="right"><strong>{Number(r.annual) ? fmtCompact(r.annual) : "—"}</strong></td>
                    <td className="right">{fmtPct(r.variation_pct)}</td>
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
