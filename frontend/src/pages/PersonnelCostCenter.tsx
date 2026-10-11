import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, type OpexAction, type PersonnelHeader, type PersonnelOptions, type PersonnelPosition, type PersonnelView, type WorkflowEventItem } from "../api";
import { HireForm, MovementForm } from "../components/personnel/PersonnelForms";
import { Legend, MonthlyBars, RankBars, SERIES, Sparkline } from "../components/charts";
import { Alert, BackButton, Badge, Card, Empty, Loading, Modal, PageHeader, SearchBox, Stat } from "../components/ui";
import { MONTHS, MOVEMENT_LABELS, REVIEW_STATUS, SUBMISSION_STATUS, fmtDate, fmtDateTime, fmtInt, fmtMoney, fmtPct, fmtSignedMoney } from "../labels";

type Tab = "roster" | "summary" | "history";

function MovementCell({ p }: { p: PersonnelPosition }) {
  if (p.kind === "HIRE") return <Badge tone="good">Vaga · {MONTHS[(p.movement?.month ?? 1) - 1]}{(p.movement?.quantity ?? 1) > 1 ? ` · ${p.movement?.quantity} pessoas` : ""}</Badge>;
  if (p.kind === "TRANSFER_IN") return <Badge tone="warn">Chega em {MONTHS[(p.movement?.month ?? 1) - 1]}</Badge>;
  const mv = p.movement;
  if (!mv) return <span className="muted small">Manter</span>;
  const meta = MOVEMENT_LABELS[mv.type];
  return (
    <div>
      <Badge tone={meta?.tone ?? "neutral"}>{meta?.label ?? mv.type} · {mv.month ? MONTHS[mv.month - 1] : "mês pendente"}</Badge>
      {!mv.month && <div className="error-text">sem mês: não entra no custo até informar (Apontamentos)</div>}
      {mv.pending?.includes("new_salary") && <div className="error-text">novo salário pendente</div>}
      <div className="muted small">
        {mv.new_salary && mv.type !== "TERMINATION" && <>novo salário {fmtMoney(mv.new_salary)} </>}
        {mv.new_position && <>· {mv.new_position} </>}
        {mv.target_cost_center && <>→ {mv.target_cost_center}</>}
        {mv.severance_cost && <>rescisão {fmtMoney(mv.severance_cost)}</>}
      </div>
      {["TERMINATION", "TRANSFER", "HIRE"].includes(mv.type) && !mv.reason && <div className="error-text">sem justificativa</div>}
    </div>
  );
}

export default function PersonnelCostCenter() {
  const { ccId } = useParams();
  const [tab, setTab] = useState<Tab>("roster");
  const [head, setHead] = useState<PersonnelHeader | null>(null);
  const [view, setView] = useState<PersonnelView | null>(null);
  const [options, setOptions] = useState<PersonnelOptions | null>(null);
  const [events, setEvents] = useState<WorkflowEventItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<OpexAction | null>(null);
  const [comment, setComment] = useState("");
  const [editing, setEditing] = useState<PersonnelPosition | null>(null);
  const [hire, setHire] = useState<{ position?: PersonnelPosition } | null>(null);
  const [review, setReview] = useState<"APPROVED" | "ADJUST_REQUESTED" | null>(null);
  const [q, setQ] = useState("");

  const refresh = useCallback(async (sub?: number) => {
    const id = sub ?? head?.submission_id;
    if (!id) return;
    const [h, v, ev] = await Promise.all([
      api<PersonnelHeader>(`/personnel/submissions/${id}`),
      api<PersonnelView>(`/personnel/submissions/${id}/view`),
      api<WorkflowEventItem[]>(`/personnel/submissions/${id}/events`),
    ]);
    setHead(h);
    setView(v);
    setEvents(ev);
  }, [head?.submission_id]);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [h, opts] = await Promise.all([api<PersonnelHeader>(`/personnel/cost-centers/${ccId}`), api<PersonnelOptions>("/personnel/options")]);
        if (!alive) return;
        setOptions(opts);
        setHead(h);
        await refresh(h.submission_id);
      } catch (err) {
        alive && setError((err as Error).message);
      }
    })();
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ccId]);

  async function runAction(action: OpexAction, text?: string) {
    setError(null);
    try {
      await api(`/personnel/submissions/${head!.submission_id}/actions/${action.action}`, { method: "POST", body: JSON.stringify({ comment: text || null }) });
      setPending(null);
      setComment("");
      await refresh();
    } catch (err) {
      setError((err as Error).message);
      setPending(null);
    }
  }

  async function sendReview() {
    setError(null);
    try {
      await api(`/personnel/submissions/${head!.submission_id}/package-review`, { method: "POST", body: JSON.stringify({ status: review, comment: comment || null }) });
      setReview(null);
      setComment("");
      await refresh();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function removeHire(p: PersonnelPosition) {
    if (!p.movement || !window.confirm(`Excluir a vaga ${p.name}?`)) return;
    try {
      setView(await api<PersonnelView>(`/personnel/movements/${p.movement.id}`, { method: "DELETE" }));
      await refresh();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  if (error && !head) return <Alert>{error}</Alert>;
  if (!head || !view || !options) return <Loading />;

  const y = head.years;
  const st = SUBMISSION_STATUS[head.status];
  const editable = head.permissions.edit;
  const t = view.totals;
  const annual = Number(t.annual);
  const ref = Number(view.actual.ref_annualized);
  const lastAdjustment = head.status === "ADJUSTMENT_REQUESTED" ? events.find((e) => e.to_status === "ADJUSTMENT_REQUESTED") : null;
  const pr = head.package_review;
  const rows = view.positions.filter((p) => !q || `${p.name} ${p.position ?? ""} ${p.registration ?? ""}`.toLowerCase().includes(q.toLowerCase()));
  const missingReasons = view.positions.filter((p) => p.movement && ["TERMINATION", "TRANSFER", "HIRE"].includes(p.movement.type) && !p.movement.reason && p.kind !== "TRANSFER_IN").length;
  const onSaved = async (v: PersonnelView) => {
    setView(v);
    setEditing(null);
    setHire(null);
    await refresh();
  };

  return (
    <>
      <PageHeader
        title={`Pessoal · ${head.cost_center.name}`}
        subtitle={`Orçamento de pessoal ${y.target} · CC ${head.cost_center.code} · empresa ${head.cost_center.company_code} · gestor ${head.cost_center.manager_name ?? "—"} · versão ${head.version}${head.cycle.deadline ? ` · prazo ${fmtDate(head.cycle.deadline)}` : ""}`}
        actions={
          <>
            <BackButton />
            {head.actions.map((a) => (
              <button
                key={a.action}
                className={`btn ${a.action === "submit" || a.action === "approve" ? "btn-primary" : ""}`}
                onClick={() => (a.requires_comment || ["submit", "approve", "consolidate"].includes(a.action) ? setPending(a) : runAction(a))}
              >
                {a.label}
              </button>
            ))}
          </>
        }
      />
      <div className="status-line">
        <Badge tone={st?.tone ?? "neutral"}>{st?.label ?? head.status}</Badge>
        {head.submitted_at && <span className="muted small">enviado em {fmtDateTime(head.submitted_at)}</span>}
        <span className="muted small">{editable ? "edição liberada" : "somente leitura"}</span>
        <span className="muted small">· cenário {view.scenario.name}: reajuste {fmtPct(view.scenario.salary_adjustment_pct)} em {MONTHS[view.scenario.adjustment_month - 1]}, CLT × {Number(view.scenario.multipliers.CLT ?? 1).toLocaleString("pt-BR")}</span>
      </div>
      {head.permissions.frozen && <Alert tone="info">A versão {head.version} do orçamento está <strong>congelada</strong> (consolidada). Para alterar, a Controladoria abre uma revisão em Consolidação.</Alert>}
      {head.permissions.cycle_blocked && <Alert tone="info">O ciclo ainda não foi aberto pela Controladoria. Você pode consultar, mas não editar.</Alert>}
      {lastAdjustment && (
        <Alert tone="bad"><strong>Ajuste solicitado</strong> por {lastAdjustment.user ?? "—"} em {fmtDateTime(lastAdjustment.created_at)}: {lastAdjustment.comment}</Alert>
      )}
      {error && <Alert>{error}</Alert>}

      <div className="stats">
        <Stat label="Headcount" value={`${fmtInt(t.headcount_start)} → ${fmtInt(t.headcount_end)}`} hint={`janeiro → dezembro/${y.target}`} />
        <Stat label="Admissões" value={fmtInt(t.hires + t.transfers_in)} tone={t.hires ? "good" : undefined} hint={`${fmtInt(t.hires)} vaga(s) · ${fmtInt(t.transfers_in)} transferência(s)`} />
        <Stat label="Saídas" value={fmtInt(t.terminations + t.transfers_out)} tone={t.terminations ? "bad" : undefined} hint={`${fmtInt(t.terminations)} desligamento(s) · ${fmtInt(t.transfers_out)} transferência(s)`} />
        <Stat label={`Custo ${y.target}`} value={fmtMoney(annual)} tone="budget" hint={ref ? `${fmtSignedMoney(annual - ref)} (${fmtPct(String(annual / ref - 1))}) vs ${y.ref} anualizado (${fmtMoney(ref)})` : "sem realizado de pessoal"} />
        <Stat label="Justificativas pendentes" value={fmtInt(missingReasons)} tone={missingReasons ? "bad" : "good"} hint="admissões, desligamentos e transferências" />
      </div>

      {pr && (
        <Card title={`Validação GMD · pacote ${pr.package}`}>
          <div className="status-line">
            <Badge tone={REVIEW_STATUS[pr.status]?.tone ?? "neutral"}>{REVIEW_STATUS[pr.status]?.label ?? pr.status}</Badge>
            {pr.reviewer && <span className="muted small">{pr.reviewer} · {fmtDateTime(pr.updated_at)}</span>}
            {pr.comment && <span className="small">{pr.comment}</span>}
            {pr.can_review && ["SUBMITTED", "UNDER_REVIEW"].includes(head.status) && (
              <span className="row-actions">
                {pr.status !== "APPROVED" && <button className="btn btn-sm btn-primary" onClick={() => setReview("APPROVED")}>Validar</button>}{" "}
                <button className="btn btn-sm" onClick={() => setReview("ADJUST_REQUESTED")}>Pedir ajuste</button>
              </span>
            )}
          </div>
        </Card>
      )}

      <div className="tabs">
        <button className={tab === "roster" ? "active" : ""} onClick={() => setTab("roster")}>
          Quadro e vagas {missingReasons > 0 && <span className="count count-bad">{missingReasons}</span>}
        </button>
        <button className={tab === "summary" ? "active" : ""} onClick={() => setTab("summary")}>Resumo e gráficos</button>
        <button className={tab === "history" ? "active" : ""} onClick={() => setTab("history")}>Histórico do fluxo</button>
      </div>

      {tab === "roster" && (
        <Card
          actions={
            <div className="inline-controls">
              <SearchBox value={q} onChange={setQ} placeholder="Buscar nome, cargo ou matrícula" />
              {editable && <button className="btn btn-primary" onClick={() => setHire({})}>Nova vaga</button>}
            </div>
          }
        >
          {view.positions.length === 0 ? (
            <Empty>Nenhum colaborador neste centro de custo. Importe o quadro de funcionários ou cadastre uma vaga.</Empty>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Colaborador / vaga</th>
                    <th>Contrato</th>
                    <th className="right">Salário atual</th>
                    <th>Ação no ano</th>
                    <th>Mensal</th>
                    <th className="right">Custo {y.target}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((p) => (
                    <tr key={p.key}>
                      <td>
                        <strong>{p.name}</strong>
                        <div className="muted small">
                          {p.position ?? "—"}
                          {p.registration && <span className="mono"> · {p.registration}</span>}
                          {p.from_cost_center && <> · vindo de {p.from_cost_center}</>}
                        </div>
                      </td>
                      <td>{p.contract_type_code}</td>
                      <td className="right">{p.base_salary ? fmtMoney(p.base_salary) : p.movement?.new_salary ? fmtMoney(p.movement.new_salary) : "—"}</td>
                      <td><MovementCell p={p} /></td>
                      <td><Sparkline values={p.monthly.map(Number)} /></td>
                      <td className="right"><strong>{fmtMoney(p.annual)}</strong></td>
                      <td className="row-actions">
                        {editable && p.kind === "EMPLOYEE" && <button className="btn btn-sm" onClick={() => setEditing(p)}>Ação</button>}
                        {editable && p.kind === "HIRE" && (
                          <>
                            <button className="btn btn-sm btn-ghost" onClick={() => setHire({ position: p })}>Editar</button>
                            <button className="btn btn-sm btn-ghost" onClick={() => removeHire(p)} aria-label={`Excluir vaga ${p.name}`}>✕</button>
                          </>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr>
                    <td colSpan={5}>Total ({fmtInt(t.headcount_start)} em janeiro · {fmtInt(t.headcount_end)} em dezembro)</td>
                    <td className="right"><strong>{fmtMoney(t.annual)}</strong></td>
                    <td />
                  </tr>
                </tfoot>
              </table>
            </div>
          )}
        </Card>
      )}

      {tab === "summary" && (
        <>
          <Card title={`Custo mensal ${y.target}`} actions={<span className="muted small">salário {fmtMoney(t.salary_total)} · encargos e benefícios {fmtMoney(t.charges_total)}{Number(t.severance_total) ? ` · rescisões ${fmtMoney(t.severance_total)}` : ""}</span>}>
            <Legend items={[{ label: "Salário (com reajuste)", color: SERIES.budget }, { label: "Encargos e benefícios (multiplicador)", color: SERIES.prev }, ...(ref ? [{ label: `${y.ref} média mensal`, color: "var(--muted)", line: true }] : [])]} />
            <MonthlyBars
              height={220}
              series={[
                { label: "Salário", color: SERIES.budget, values: t.salary_monthly.map(Number) },
                { label: "Encargos e benefícios", color: SERIES.prev, values: t.charges_monthly.map(Number) },
              ]}
              line={ref ? { label: `${y.ref} média mensal`, color: "var(--muted)", values: Array(12).fill(ref / 12) } : undefined}
            />
          </Card>
          <div className="grid-2">
            <Card title="Headcount por mês">
              <MonthlyBars height={200} format={(n) => fmtInt(Math.round(n))} axisFormat={(n) => (Number.isInteger(n) ? fmtInt(n) : "")} series={[{ label: "Headcount", color: SERIES.budget, values: t.headcount }]} />
            </Card>
            <Card title="Custo por tipo de contrato">
              {view.by_contract.length ? <RankBars rows={view.by_contract.map((c) => ({ label: c.label, value: Number(c.total) }))} color={SERIES.budget} /> : <Empty>Sem dados.</Empty>}
              {view.benefits.length > 0 && (
                <>
                  <h3 className="section-title" style={{ marginTop: 16 }}>Benefícios informados pelo líder</h3>
                  <p className="muted small">Informativo: o custo de benefícios já está no multiplicador do contrato.</p>
                  <div className="checks">{view.benefits.map((b) => <span key={b.code} className="badge badge-neutral">{b.name}: {b.employees}</span>)}</div>
                </>
              )}
            </Card>
          </div>
        </>
      )}

      {tab === "history" && (
        <Card>
          {events.length === 0 ? (
            <p className="muted">Nenhuma movimentação ainda.</p>
          ) : (
            <ol className="timeline">
              {events.map((e, i) => (
                <li key={i}>
                  <Badge tone={SUBMISSION_STATUS[e.to_status]?.tone ?? "neutral"}>{e.to_label}</Badge>
                  <div>
                    <div><strong>{e.user ?? "Sistema"}</strong> <span className="muted small">· {fmtDateTime(e.created_at)} · versão {e.version}</span></div>
                    {e.comment && <div className="small">{e.comment}</div>}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </Card>
      )}

      {editing && (
        <MovementForm submissionId={head.submission_id} position={editing} options={options} currentCcId={head.cost_center.id} year={y.target} onClose={() => setEditing(null)} onSaved={onSaved} />
      )}
      {hire && <HireForm submissionId={head.submission_id} position={hire.position} options={options} onClose={() => setHire(null)} onSaved={onSaved} />}
      {pending && (
        <Modal
          title={pending.label}
          onClose={() => setPending(null)}
          footer={
            <>
              <button className="btn btn-ghost" onClick={() => setPending(null)}>Cancelar</button>
              <button className="btn btn-primary" disabled={pending.requires_comment && !comment.trim()} onClick={() => runAction(pending, comment)}>Confirmar</button>
            </>
          }
        >
          <div className="stack">
            {pending.action === "submit" && <p>Depois do envio, o orçamento de pessoal fica bloqueado até a Controladoria devolver para ajuste ou você retirar o envio. O gestor do pacote Pessoas valida antes da aprovação.</p>}
            {pending.action === "approve" && <p>Aprovar o custo de pessoal de {fmtMoney(annual)} deste centro de custo.</p>}
            {pending.action === "consolidate" && <p>Consolidar congela o orçamento de pessoal deste CC na versão {head.version}.</p>}
            <label>
              Comentário {pending.requires_comment ? "(obrigatório)" : "(opcional)"}
              <textarea rows={3} value={comment} onChange={(e) => setComment(e.target.value)} />
            </label>
          </div>
        </Modal>
      )}
      {review && (
        <Modal
          title={review === "APPROVED" ? "Validar pacote Pessoas" : "Pedir ajuste no pacote Pessoas"}
          onClose={() => setReview(null)}
          footer={
            <>
              <button className="btn btn-ghost" onClick={() => setReview(null)}>Cancelar</button>
              <button className="btn btn-primary" disabled={review !== "APPROVED" && !comment.trim()} onClick={sendReview}>Confirmar</button>
            </>
          }
        >
          <label>
            Comentário {review !== "APPROVED" ? "(obrigatório — o orçamento volta para o gestor)" : "(opcional)"}
            <textarea rows={3} value={comment} onChange={(e) => setComment(e.target.value)} />
          </label>
        </Modal>
      )}
    </>
  );
}
