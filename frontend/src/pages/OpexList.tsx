import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type OpexSummary, type ReviewQueueItem } from "../api";
import { useAuth } from "../auth";
import { BudgetProgressCard } from "../components/BudgetProgressCard";
import { Alert, Badge, Card, Empty, Loading, PageHeader, SearchBox, Stat, useLoad } from "../components/ui";
import { CYCLE_STATUS, REVIEW_STATUS, SUBMISSION_STATUS, fmtCompact, fmtDate, fmtDateTime, fmtInt, fmtMoney, fmtPct } from "../labels";

const ORDER = ["DRAFT", "IN_PROGRESS", "ADJUSTMENT_REQUESTED", "SUBMITTED", "UNDER_REVIEW", "APPROVED", "CONSOLIDATED"];

export default function OpexList() {
  const { can } = useAuth();
  const navigate = useNavigate();
  const isController = can("CONTROLLER");
  const [tab, setTab] = useState<"ccs" | "reviews">("ccs");
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const { data, error } = useLoad(() => api<OpexSummary>("/opex/summary"));
  const queue = useLoad(() => api<ReviewQueueItem[]>("/opex/review-queue"));

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
  const total = (k: "prev_actual" | "ref_annualized" | "proposed") => data.rows.reduce((s, r) => s + Number(r[k]), 0);
  const done = (data.status_counts.SUBMITTED ?? 0) + (data.status_counts.UNDER_REVIEW ?? 0) + (data.status_counts.APPROVED ?? 0) + (data.status_counts.CONSOLIDATED ?? 0);
  // compara só os CCs que já têm proposta (evita "-90%" enquanto a maioria não preencheu)
  const startedBase = data.rows.filter((r) => Number(r.proposed) > 0).reduce((s, r) => s + Number(r.ref_annualized), 0);
  const pendingReviews = (queue.data ?? []).filter((r) => r.review_status === "PENDING").length;

  return (
    <>
      <PageHeader
        title={`Orçamento OPEX ${y.target}`}
        subtitle={`Preencha o ${y.target} de cada centro de custo comparando com ${y.prev} realizado, ${y.ref} realizado e orçado. Prazo: ${fmtDate(data.cycle.deadline)}.`}
      />
      {data.cycle.status !== "OPEN" && (
        <Alert tone="warn">
          Ciclo <strong>{CYCLE_STATUS[data.cycle.status]?.label.toLowerCase()}</strong>: gestores ainda não conseguem editar.
          {can() && <> Abra o ciclo em <Link className="link" to="/ciclo">Ciclo e parâmetros</Link>.</>}
        </Alert>
      )}

      <div className="stats">
        <Stat label="Centros de custo" value={fmtInt(data.rows.length)} />
        <Stat label="Enviados ou aprovados" value={`${fmtInt(done)} de ${fmtInt(data.rows.length)}`} tone={done === data.rows.length && done > 0 ? "good" : undefined} />
        <Stat label={`Realizado ${y.prev}`} value={fmtCompact(total("prev_actual"))} />
        <Stat label={`${y.ref} anualizado`} value={fmtCompact(total("ref_annualized"))} />
        <Stat label={`Proposto ${y.target}`} value={fmtCompact(total("proposed"))} tone="warn"
              hint={startedBase ? `${fmtPct(String(total("proposed") / startedBase - 1))} vs ${y.ref} anualizado dos CCs já preenchidos` : undefined} />
      </div>

      {data.progress && data.rows.length > 0 && <BudgetProgressCard progress={data.progress} />}

      {(queue.data?.length ?? 0) > 0 && (
        <div className="tabs">
          <button className={tab === "ccs" ? "active" : ""} onClick={() => setTab("ccs")}>Centros de custo</button>
          <button className={tab === "reviews" ? "active" : ""} onClick={() => setTab("reviews")}>
            Validações GMD <span className="count">{pendingReviews}</span>
          </button>
        </div>
      )}

      {tab === "reviews" ? (
        <Card title="Pacotes aguardando sua validação">
          {!queue.data?.length ? (
            <Empty>Nada pendente.</Empty>
          ) : (
            <table className="table">
              <thead>
                <tr><th>Centro de custo</th><th>Pacote</th><th className="right">Valor do pacote</th><th>Enviado em</th><th>Situação</th></tr>
              </thead>
              <tbody>
                {queue.data.map((r) => (
                  <tr key={`${r.submission_id}-${r.package_id}`} className="clickable" onClick={() => navigate(`/orcamento/${r.cost_center_id}?pacote=${r.package_id}`)}>
                    <td>{r.cost_center}</td>
                    <td>{r.package}</td>
                    <td className="right">{fmtMoney(r.package_total)}</td>
                    <td>{fmtDateTime(r.submitted_at)}</td>
                    <td><Badge tone={REVIEW_STATUS[r.review_status]?.tone ?? "neutral"}>{REVIEW_STATUS[r.review_status]?.label}</Badge></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      ) : (
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
                    <th className="right">{y.prev} realizado</th>
                    <th className="right">{y.ref} anualizado</th>
                    <th className="right">{y.target} proposto</th>
                    <th className="right">Var.</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.cost_center_id} className="clickable" onClick={() => navigate(`/orcamento/${r.cost_center_id}`)}>
                      <td>
                        <Link className="link" to={`/orcamento/${r.cost_center_id}`} onClick={(e) => e.stopPropagation()}>{r.name}</Link>
                        <div className="muted small mono">{r.company_code} · {r.code}</div>
                      </td>
                      {isController && (
                        <td>
                          {r.manager_name ?? "—"}
                          {!r.has_manager_user && <div><Badge tone="warn">sem usuário</Badge></div>}
                        </td>
                      )}
                      <td><Badge tone={SUBMISSION_STATUS[r.status]?.tone ?? "neutral"}>{SUBMISSION_STATUS[r.status]?.label ?? r.status}</Badge></td>
                      <td className="right">{fmtCompact(r.prev_actual)}</td>
                      <td className="right">{fmtCompact(r.ref_annualized)}</td>
                      <td className="right"><strong>{Number(r.proposed) ? fmtCompact(r.proposed) : "—"}</strong></td>
                      <td className="right">{r.variation_pct !== null ? fmtPct(r.variation_pct) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}
    </>
  );
}
