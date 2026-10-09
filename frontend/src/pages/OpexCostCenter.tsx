import { useCallback, useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { api, download, type OpexAccounts, type OpexAction, type OpexHeader, type OpexLine, type OpexOptions, type WorkflowEventItem } from "../api";
import { AccountsTab } from "../components/opex/AccountsTab";
import { EventPanel } from "../components/opex/EventPanel";
import { LinesGrid } from "../components/opex/LinesGrid";
import { TravelPanel } from "../components/opex/TravelPanel";
import { Legend, MonthlyBars, PairedBars, SERIES } from "../components/charts";
import { Alert, BackButton, Badge, Card, Loading, Modal, PageHeader, Stat } from "../components/ui";
import { REVIEW_STATUS, SUBMISSION_STATUS, fmtCompact, fmtDate, fmtDateTime, fmtMoney, fmtPct } from "../labels";

type Tab = "accounts" | "fill" | "history";

export default function OpexCostCenter() {
  const { ccId } = useParams();
  const [params, setParams] = useSearchParams();
  const [tab, setTab] = useState<Tab>(params.get("pacote") ? "fill" : "accounts");
  const [packageId, setPackageId] = useState<number | null>(params.get("pacote") ? Number(params.get("pacote")) : null);
  const [head, setHead] = useState<OpexHeader | null>(null);
  const [accounts, setAccounts] = useState<OpexAccounts | null>(null);
  const [lines, setLines] = useState<OpexLine[]>([]);
  const [options, setOptions] = useState<OpexOptions | null>(null);
  const [events, setEvents] = useState<WorkflowEventItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<OpexAction | null>(null);
  const [comment, setComment] = useState("");
  const [review, setReview] = useState<{ packageId: number; status: "APPROVED" | "ADJUST_REQUESTED" } | null>(null);

  const refresh = useCallback(async (sub?: number) => {
    const id = sub ?? head?.submission_id;
    if (!id) return;
    const [h, a, l, ev] = await Promise.all([
      api<OpexHeader>(`/opex/submissions/${id}`),
      api<OpexAccounts>(`/opex/submissions/${id}/accounts`),
      api<OpexLine[]>(`/opex/submissions/${id}/lines`),
      api<WorkflowEventItem[]>(`/opex/submissions/${id}/events`),
    ]);
    setHead(h);
    setAccounts(a);
    setLines(l);
    setEvents(ev);
  }, [head?.submission_id]);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const h = await api<OpexHeader>(`/opex/cost-centers/${ccId}`);
        const opts = await api<OpexOptions>(`/opex/options?company_id=${h.cost_center.company_id}`);
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

  useEffect(() => {
    if (!packageId && options?.packages.length) setPackageId(options.packages[0].id);
  }, [options, packageId]);

  async function runAction(action: OpexAction, text?: string) {
    setError(null);
    try {
      await api(`/opex/submissions/${head!.submission_id}/actions/${action.action}`, { method: "POST", body: JSON.stringify({ comment: text || null }) });
      setPending(null);
      setComment("");
      await refresh();
    } catch (err) {
      setError((err as Error).message);
      setPending(null);
    }
  }

  async function sendReview() {
    if (!review) return;
    setError(null);
    try {
      await api(`/opex/submissions/${head!.submission_id}/package-reviews/${review.packageId}`, {
        method: "POST",
        body: JSON.stringify({ status: review.status, comment: comment || null }),
      });
      setReview(null);
      setComment("");
      await refresh();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  if (error && !head) return <Alert>{error}</Alert>;
  if (!head || !accounts || !options) return <Loading />;

  const y = head.years;
  const st = SUBMISSION_STATUS[head.status];
  const editable = head.permissions.edit;
  const lastAdjustment = head.status === "ADJUSTMENT_REQUESTED" ? events.find((e) => e.to_status === "ADJUSTMENT_REQUESTED") : null;
  const pkg = options.packages.find((p) => p.id === packageId) ?? options.packages[0];
  const pkgLines = lines.filter((l) => l.package_id === pkg?.id);
  const pkgTotal = (id: number) => lines.filter((l) => l.package_id === id).reduce((s, l) => s + Number(l.total), 0);
  const totalProposed = Number(accounts.totals.proposed ?? 0);
  const annualized = Number(accounts.totals.ref_annualized ?? 0);
  const goToPackage = async (id: number, accountId?: number) => {
    setPackageId(id);
    setTab("fill");
    setParams({ pacote: String(id) });
    if (accountId && editable) {
      try {
        await api(`/opex/submissions/${head.submission_id}/lines`, { method: "POST", body: JSON.stringify({ account_id: accountId, package_id: id, values: {} }) });
        await refresh();
      } catch (err) {
        setError((err as Error).message);
      }
    }
  };

  return (
    <>
      <PageHeader
        title={head.cost_center.name}
        subtitle={`Centro de custo ${head.cost_center.company_code} · ${head.cost_center.code} · gestor ${head.cost_center.manager_name ?? "—"} · ${head.cycle.name} (versão ${head.version}) · prazo ${fmtDate(head.cycle.deadline)}`}
        actions={
          <>
            <BackButton />
            <button
              className="btn btn-ghost"
              title="Planilha no layout do template, com os lançamentos deste CC; pode ser ajustada e reimportada"
              onClick={() =>
                download(`/opex/submissions/${head.submission_id}/template.xlsx`, `Template_OPEX_${head.years.target}_${head.cost_center.code}.xlsx`).catch((e) => setError((e as Error).message))
              }
            >
              Baixar template (Excel)
            </button>
            {head.actions.map((a) => (
              <button
                key={a.action}
                className={`btn ${a.action === "submit" || a.action === "approve" ? "btn-primary" : ""}`}
                onClick={() => (a.requires_comment || a.action === "submit" || a.action === "approve" || a.action === "consolidate" ? setPending(a) : runAction(a))}
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
        {editable ? <span className="muted small">alterações são salvas automaticamente</span> : <span className="muted small">somente leitura</span>}
      </div>
      {head.permissions.frozen && <Alert tone="info">A versão {head.version} do orçamento está <strong>congelada</strong> (consolidada). Para alterar, a Controladoria abre uma revisão em Consolidação.</Alert>}
      {head.permissions.cycle_blocked && <Alert tone="warn">O ciclo ainda não foi aberto pela Controladoria. Você pode consultar o histórico, mas não editar.</Alert>}
      {lastAdjustment && (
        <Alert tone="bad">
          <strong>Ajuste solicitado</strong> por {lastAdjustment.user ?? "—"} em {fmtDateTime(lastAdjustment.created_at)}: {lastAdjustment.comment}
        </Alert>
      )}
      {error && <Alert>{error}</Alert>}

      <div className="stats">
        <Stat label={`${y.prev} realizado`} value={fmtCompact(accounts.totals.prev_actual ?? 0)} />
        <Stat label={`${y.ref} anualizado`} value={fmtCompact(annualized)} hint={accounts.closed_period ? `realizado até o mês ${accounts.closed_period}` : "sem realizado"} />
        <Stat label={`${y.ref} orçado`} value={fmtCompact(accounts.totals.ref_budget ?? 0)} />
        <Stat label={`${y.target} proposto`} value={fmtCompact(totalProposed)} tone="warn" hint={annualized ? `${fmtPct(String(totalProposed / annualized - 1))} vs ${y.ref} anualizado` : undefined} />
        <Stat label="Justificativas pendentes" value={accounts.pending_justifications} tone={accounts.pending_justifications ? "bad" : "good"} hint="obrigatórias para enviar" />
      </div>

      {head.package_reviews.length > 0 && (
        <Card title="Validação GMD (pacotes Tipo 1)">
          <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Pacote</th><th className="right">Valor</th><th>Situação</th><th>Comentário</th><th /></tr></thead>
            <tbody>
              {head.package_reviews.map((r) => (
                <tr key={r.package_id}>
                  <td><button className="link btn-link" onClick={() => goToPackage(r.package_id)}>{r.package}</button></td>
                  <td className="right">{fmtMoney(pkgTotal(r.package_id))}</td>
                  <td><Badge tone={REVIEW_STATUS[r.status]?.tone ?? "neutral"}>{REVIEW_STATUS[r.status]?.label ?? r.status}</Badge>
                    {r.reviewer && <div className="muted small">{r.reviewer} · {fmtDateTime(r.updated_at)}</div>}</td>
                  <td className="small">{r.comment ?? "—"}</td>
                  <td className="row-actions">
                    {r.can_review && ["SUBMITTED", "UNDER_REVIEW"].includes(head.status) && (
                      <>
                        {r.status !== "APPROVED" && (
                          <button className="btn btn-sm btn-primary" onClick={() => setReview({ packageId: r.package_id, status: "APPROVED" })}>Validar</button>
                        )}
                        <button className="btn btn-sm" onClick={() => setReview({ packageId: r.package_id, status: "ADJUST_REQUESTED" })}>Pedir ajuste</button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </Card>
      )}

      <div className="tabs">
        <button className={tab === "accounts" ? "active" : ""} onClick={() => setTab("accounts")}>
          Visão por conta {accounts.pending_justifications > 0 && <span className="count count-bad">{accounts.pending_justifications}</span>}
        </button>
        <button className={tab === "fill" ? "active" : ""} onClick={() => setTab("fill")}>Preencher por pacote</button>
        <button className={tab === "history" ? "active" : ""} onClick={() => setTab("history")}>Histórico do fluxo</button>
      </div>

      {tab === "accounts" && (accounts.accounts.length > 0) && (
        <div className="grid-2">
          <Card title={`Evolução mensal · ${y.prev}, ${y.ref} e ${y.target} proposto`}>
            <Legend
              items={[
                { label: `${y.prev} realizado`, color: SERIES.past },
                { label: `${y.ref} realizado`, color: SERIES.ref },
                { label: `${y.target} proposto`, color: SERIES.budget },
                ...(Number(accounts.totals.ref_budget) ? [{ label: `${y.ref} orçado`, color: "var(--muted)", line: true }] : []),
              ]}
            />
            <MonthlyBars
              height={240}
              series={[
                { label: `${y.prev} realizado`, color: SERIES.past, values: accounts.monthly.prev.map(Number) },
                { label: `${y.ref} realizado`, color: SERIES.ref, values: accounts.monthly.ref.map(Number) },
                { label: `${y.target} proposto`, color: SERIES.budget, values: accounts.monthly.proposed.map(Number) },
              ]}
              line={Number(accounts.totals.ref_budget) ? { label: `${y.ref} orçado`, color: "var(--muted)", values: accounts.monthly.budget.map(Number) } : undefined}
            />
          </Card>
          <Card title={`Por pacote · ${y.ref} anualizado × ${y.target} proposto`}>
            <Legend items={[{ label: `${y.ref} anualizado`, color: SERIES.past }, { label: `${y.target} proposto`, color: SERIES.ref }]} />
            <PairedBars
              prevLabel={`${y.ref} anualizado`}
              refLabel={`${y.target} proposto`}
              rows={Object.values(
                accounts.accounts.reduce<Record<string, { label: string; prev: number; ref: number }>>((acc, r) => {
                  const k = r.package ?? "Sem pacote";
                  acc[k] ??= { label: k, prev: 0, ref: 0 };
                  acc[k].prev += Number(r.ref_annualized);
                  acc[k].ref += Number(r.proposed);
                  return acc;
                }, {}),
              )
                .sort((a, b) => Math.max(b.prev, b.ref) - Math.max(a.prev, a.ref))
                .map((r) => ({ ...r, note: r.prev && r.ref ? fmtPct(String(r.ref / r.prev - 1)) : undefined }))}
            />
          </Card>
        </div>
      )}

      {tab === "accounts" && (
        <Card>
          <AccountsTab submissionId={head.submission_id} data={accounts} years={y} editable={editable} onChanged={() => refresh()} onGoToPackage={goToPackage} />
        </Card>
      )}

      {tab === "fill" && pkg && (
        <div className="fill-layout">
          <nav className="package-nav" aria-label="Pacotes">
            {options.packages.map((p) => (
              <button key={p.id} className={p.id === pkg.id ? "active" : ""} onClick={() => goToPackage(p.id)}>
                <span>{p.roman ? `${p.roman} · ` : ""}{p.name}</span>
                <span className="muted small">{pkgTotal(p.id) ? fmtCompact(pkgTotal(p.id)) : "—"}{p.package_type === 1 ? " · T1" : ""}</span>
              </button>
            ))}
          </nav>
          <div className="stack">
            <Card title={`${pkg.roman ? `${pkg.roman} · ` : ""}${pkg.name}`} actions={<span className="muted">Total do pacote: <strong>{fmtMoney(pkgTotal(pkg.id))}</strong></span>}>
              {pkg.package_type === 1 && <p className="muted small">Pacote Tipo 1: após o envio, o gestor do pacote precisa validar estes valores.</p>}
              {pkg.form_type === "TRAVEL" && (
                <TravelPanel submissionId={head.submission_id} lines={pkgLines} options={options} editable={editable} onChanged={() => refresh()} />
              )}
              {pkg.form_type === "EVENT" && (
                <EventPanel submissionId={head.submission_id} packageId={pkg.id} lines={pkgLines} options={options} editable={editable} onChanged={() => refresh()} />
              )}
            </Card>
            <Card title={pkg.form_type === "GENERIC" ? "Lançamentos mensais" : "Outros lançamentos do pacote (valores mensais)"}>
              <LinesGrid
                submissionId={head.submission_id}
                packageId={pkg.id}
                lines={pkgLines}
                options={options}
                companyId={head.cost_center.company_id}
                editable={editable}
                onChanged={() => refresh()}
                costCenterId={head.cost_center.id}
                canMove={editable && head.permissions.global}
                canAsk={Boolean(head.permissions.ask)}
                costCenterLabel={`${head.cost_center.code} · ${head.cost_center.name}`}
                highlightLineId={params.get("linha") ? Number(params.get("linha")) : null}
              />
            </Card>
          </div>
        </div>
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
            {pending.action === "submit" && <p>Depois do envio, o orçamento fica bloqueado para edição até a Controladoria devolver para ajuste ou você retirar o envio.</p>}
            {pending.action === "approve" && <p>Aprovar o orçamento de {fmtMoney(totalProposed)} deste centro de custo.</p>}
            {pending.action === "consolidate" && <p>Consolidar congela o orçamento deste centro de custo na versão {head.version}.</p>}
            <label>
              Comentário {pending.requires_comment ? "(obrigatório)" : "(opcional)"}
              <textarea rows={3} value={comment} onChange={(e) => setComment(e.target.value)} />
            </label>
          </div>
        </Modal>
      )}
      {review && (
        <Modal
          title={review.status === "APPROVED" ? "Validar pacote" : "Pedir ajuste no pacote"}
          onClose={() => setReview(null)}
          footer={
            <>
              <button className="btn btn-ghost" onClick={() => setReview(null)}>Cancelar</button>
              <button className="btn btn-primary" disabled={review.status !== "APPROVED" && !comment.trim()} onClick={sendReview}>Confirmar</button>
            </>
          }
        >
          <label>
            Comentário {review.status !== "APPROVED" ? "(obrigatório — o orçamento volta para o gestor)" : "(opcional)"}
            <textarea rows={3} value={comment} onChange={(e) => setComment(e.target.value)} />
          </label>
        </Modal>
      )}
    </>
  );
}
