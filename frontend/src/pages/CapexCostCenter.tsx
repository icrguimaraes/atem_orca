import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, download, type CapexHeader, type CapexItem, type CapexIssue, type CapexOptions, type CapexProject, type CapexView, type OpexAction, type WorkflowEventItem } from "../api";
import { ItemForm, ProjectForm } from "../components/capex/CapexForms";
import { Legend, MonthlyBars, PairedBars, RankBars, SERIES } from "../components/charts";
import { Alert, BackButton, Badge, Card, Empty, Loading, Modal, PageHeader, Stat } from "../components/ui";
import { MONTHS, SUBMISSION_STATUS, fmtCompact, fmtDate, fmtDateTime, fmtInt, fmtMoney } from "../labels";

type Tab = "requests" | "summary" | "history";

function Issues({ issues }: { issues: CapexIssue[] }) {
  if (!issues.length) return null;
  return (
    <ul className="issue-list">
      {issues.map((i, k) => (
        <li key={k} className={i.severity === "CRITICAL" ? "crit" : "warn"}>
          <span aria-hidden>{i.severity === "CRITICAL" ? "●" : "▲"}</span>
          {i.message}
        </li>
      ))}
    </ul>
  );
}

function MiniSchedule({ values }: { values: Record<string, string> }) {
  const nums = MONTHS.map((_, i) => Number(values[String(i + 1)] ?? 0));
  const max = Math.max(...nums, 1);
  return (
    <div className="mini-schedule" title={MONTHS.map((m, i) => `${m}: ${fmtMoney(nums[i])}`).join("\n")}>
      {nums.map((v, i) => <i key={i} className={v ? "" : "zero"} style={{ height: v ? `${Math.max(12, (v / max) * 100)}%` : "2px" }} />)}
    </div>
  );
}

export default function CapexCostCenter() {
  const { ccId } = useParams();
  const [tab, setTab] = useState<Tab>("requests");
  const [head, setHead] = useState<CapexHeader | null>(null);
  const [view, setView] = useState<CapexView | null>(null);
  const [options, setOptions] = useState<CapexOptions | null>(null);
  const [events, setEvents] = useState<WorkflowEventItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<OpexAction | null>(null);
  const [comment, setComment] = useState("");
  const [projectForm, setProjectForm] = useState<{ project?: CapexProject } | null>(null);
  const [itemForm, setItemForm] = useState<{ projectId: number; item?: CapexItem } | null>(null);

  const refresh = useCallback(async (sub?: number) => {
    const id = sub ?? head?.submission_id;
    if (!id) return;
    setError(null);
    const [h, v, ev] = await Promise.all([
      api<CapexHeader>(`/capex/submissions/${id}`),
      api<CapexView>(`/capex/submissions/${id}/view`),
      api<WorkflowEventItem[]>(`/capex/submissions/${id}/events`),
    ]);
    setHead(h);
    setView(v);
    setEvents(ev);
  }, [head?.submission_id]);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const h = await api<CapexHeader>(`/capex/cost-centers/${ccId}`);
        const opts = await api<CapexOptions>(`/capex/options?company_id=${h.cost_center.company_id}`);
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
      await api(`/capex/submissions/${head!.submission_id}/actions/${action.action}`, { method: "POST", body: JSON.stringify({ comment: text || null }) });
      setPending(null);
      setComment("");
      await refresh();
    } catch (err) {
      setError((err as Error).message);
      setPending(null);
    }
  }

  async function removeProject(p: CapexProject) {
    if (!window.confirm(`Excluir ${p.code} · ${p.title} e seus ${p.items.length} item(ns)?`)) return;
    try {
      await api(`/capex/projects/${p.id}`, { method: "DELETE" });
      await refresh();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function removeItem(i: CapexItem) {
    if (!window.confirm(`Excluir o item ${i.item_name}?`)) return;
    try {
      await api(`/capex/items/${i.id}`, { method: "DELETE" });
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
  const lastAdjustment = head.status === "ADJUSTMENT_REQUESTED" ? events.find((e) => e.to_status === "ADJUSTMENT_REQUESTED") : null;
  const total = Number(view.totals.proposed);
  const lookupLabel = (code: string | null) => options.lookups.CAPEX_PROJECT_TYPE?.find((t) => t.code === code)?.label ?? code;
  const branchLabel = (id: number | null) => options.branches.find((b) => b.id === id)?.name;
  const hasHistory = view.accounts.some((a) => Number(a.prev_actual) || Number(a.ref_actual) || Number(a.ref_budget));

  return (
    <>
      <PageHeader
        title={`CAPEX · ${head.cost_center.name}`}
        subtitle={`Centro de custo ${head.cost_center.company_code} · ${head.cost_center.code} · gestor ${head.cost_center.manager_name ?? "—"} · ${head.cycle.name} (versão ${head.version}) · prazo ${fmtDate(head.cycle.deadline)}`}
        actions={
          <>
            <BackButton />
            <button
              className="btn btn-ghost"
              title="Planilha no layout do template, com os lançamentos deste CC; pode ser ajustada e reimportada"
              onClick={() =>
                download(`/capex/submissions/${head.submission_id}/template.xlsx`, `Template_CAPEX_${head.years.target}_${head.cost_center.code}.xlsx`).catch((e) => setError((e as Error).message))
              }
            >
              Baixar template (Excel)
            </button>
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
      </div>
      {head.permissions.frozen && <Alert tone="info">A versão {head.version} do orçamento está <strong>congelada</strong> (consolidada). Para alterar, a Controladoria abre uma revisão em Consolidação.</Alert>}
      {head.permissions.cycle_blocked && <Alert tone="warn">O ciclo ainda não foi aberto pela Controladoria. Você pode consultar, mas não editar.</Alert>}
      {lastAdjustment && (
        <Alert tone="bad">
          <strong>Ajuste solicitado</strong> por {lastAdjustment.user ?? "—"} em {fmtDateTime(lastAdjustment.created_at)}: {lastAdjustment.comment}
        </Alert>
      )}
      {error && <Alert>{error}</Alert>}

      <div className="stats">
        <Stat
          label={`CAPEX solicitado ${y.target}`}
          value={fmtCompact(total)}
          tone="warn"
          hint={
            Number(view.totals.scheduled) !== total
              ? `cronograma ${fmtCompact(view.totals.scheduled)} — a consolidação usa o cronograma; ajuste os meses`
              : `${fmtInt(view.totals.items)} itens em ${fmtInt(view.totals.requests)} solicitações`
          }
        />
        <Stat label="Em projetos" value={fmtCompact(view.totals.projects_total)} hint={`${fmtInt(view.totals.projects)} projeto(s)`} />
        <Stat label={`Realizado CAPEX ${y.ref}`} value={fmtCompact(view.totals.ref_actual)} hint={`${y.prev}: ${fmtCompact(view.totals.prev_actual)}`} />
        <Stat label="Pendências críticas" value={fmtInt(view.issues.critical)} tone={view.issues.critical ? "bad" : "good"} hint="bloqueiam envio e aprovação" />
        <Stat label="Avisos" value={fmtInt(view.issues.warning)} tone={view.issues.warning ? "warn" : undefined} hint="enquadramento (valor, vida útil)" />
      </div>

      <div className="tabs">
        <button className={tab === "requests" ? "active" : ""} onClick={() => setTab("requests")}>
          Solicitações {view.issues.critical > 0 && <span className="count count-bad">{view.issues.critical}</span>}
        </button>
        <button className={tab === "summary" ? "active" : ""} onClick={() => setTab("summary")}>Resumo e gráficos</button>
        <button className={tab === "history" ? "active" : ""} onClick={() => setTab("history")}>Histórico do fluxo</button>
      </div>

      {tab === "requests" && (
        <div className="stack-lg">
          {editable && (
            <div className="inline-controls">
              <button className="btn btn-primary" onClick={() => setProjectForm({})}>Nova solicitação</button>
              <span className="muted small">Uma solicitação pode ser um projeto (vários itens) ou uma aquisição avulsa.</span>
            </div>
          )}
          {view.projects.length === 0 && (
            <Card>
              <Empty>
                Nenhuma solicitação de CAPEX. {editable ? "Clique em Nova solicitação ou importe o template CAPEX preenchido." : ""}
              </Empty>
            </Card>
          )}
          {view.projects.map((p) => {
            const critical = p.issues.some((i) => i.severity === "CRITICAL") || p.items.some((i) => i.issues.some((x) => x.severity === "CRITICAL"));
            return (
              <section key={p.id} className={`capex-project ${critical ? "has-critical" : ""}`}>
                <div className="capex-project-head">
                  <div>
                    <h3><span className="muted mono">{p.code}</span> · {p.title}</h3>
                    <div className="meta">
                      {p.is_project ? <Badge tone="info">Projeto</Badge> : <Badge tone="neutral">Aquisição avulsa</Badge>}
                      {p.project_type_code && <span className="small">{lookupLabel(p.project_type_code)}</span>}
                      {p.branch_id && <span className="muted small">· {branchLabel(p.branch_id)}</span>}
                      {p.priority && <span className="muted small">· prioridade {p.priority}</span>}
                      {p.source === "TEMPLATE" && <Badge tone="neutral">do template</Badge>}
                    </div>
                  </div>
                  <div className="capex-total">
                    <strong>{fmtMoney(p.total)}</strong>
                    {editable && (
                      <div className="row-actions">
                        <button className="btn btn-sm" onClick={() => setItemForm({ projectId: p.id })}>Adicionar item</button>{" "}
                        <button className="btn btn-sm btn-ghost" onClick={() => setProjectForm({ project: p })}>Editar</button>{" "}
                        <button className="btn btn-sm btn-ghost" onClick={() => removeProject(p)}>Excluir</button>
                      </div>
                    )}
                  </div>
                </div>
                {(p.justification || p.expected_cost_reduction || p.expected_revenue) && (
                  <div className="small">
                    {p.justification && <div><span className="muted">Justificativa:</span> {p.justification}</div>}
                    {(p.expected_cost_reduction || p.expected_revenue) && (
                      <div className="muted">
                        {p.expected_cost_reduction && <>Redução esperada {fmtMoney(p.expected_cost_reduction)}/ano </>}
                        {p.expected_revenue && <>· Receita esperada {fmtMoney(p.expected_revenue)}/ano</>}
                      </div>
                    )}
                  </div>
                )}
                <Issues issues={p.issues} />
                {p.items.length > 0 && (
                  <div className="table-wrap">
                    <table className="table">
                      <thead>
                        <tr>
                          <th>Item</th><th>Conta</th><th className="right">Vlr unit.</th><th className="right">Qtd</th>
                          <th className="right">Total</th><th>Cronograma</th><th />
                        </tr>
                      </thead>
                      <tbody>
                        {p.items.map((i) => (
                          <tr key={i.id}>
                            <td>
                              <strong>{i.item_name}</strong>
                              {i.description && <div className="muted small">{i.description}</div>}
                              <Issues issues={i.issues} />
                            </td>
                            <td className="small">{i.account_name}<div className="muted mono">{i.account_code}</div></td>
                            <td className="right">{fmtMoney(i.unit_value)}</td>
                            <td className="right">{Number(i.quantity).toLocaleString("pt-BR")}</td>
                            <td className="right"><strong>{fmtMoney(i.total_value)}</strong></td>
                            <td>
                              <MiniSchedule values={i.values} />
                              {Number(i.difference) !== 0 && <div className="small nowrap" style={{ color: "var(--bad)" }}>dif. {fmtMoney(i.difference)}</div>}
                            </td>
                            <td className="row-actions">
                              {editable && (
                                <>
                                  <button className="btn btn-sm btn-ghost" onClick={() => setItemForm({ projectId: p.id, item: i })}>Editar</button>
                                  <button className="btn btn-sm btn-ghost" onClick={() => removeItem(i)} aria-label={`Excluir ${i.item_name}`}>✕</button>
                                </>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>
            );
          })}
        </div>
      )}

      {tab === "summary" && (
        <>
          <Card title={`Cronograma de desembolso ${y.target}`}>
            {total > 0 ? (
              <MonthlyBars height={220} series={[{ label: `CAPEX ${y.target}`, color: SERIES.ref, values: view.monthly.map(Number) }]} />
            ) : (
              <Empty>Sem valores lançados.</Empty>
            )}
          </Card>
          <div className="grid-2">
            <Card title="Por tipo de projeto">
              {view.by_type.length ? <RankBars rows={view.by_type.map((t) => ({ label: t.label, value: Number(t.total) }))} color={SERIES.budget} /> : <Empty>Sem itens.</Empty>}
            </Card>
            <Card title={hasHistory ? `Por conta · ${y.ref} realizado × ${y.target} solicitado` : "Por conta de ativo"}>
              {hasHistory ? (
                <>
                  <Legend items={[{ label: `${y.ref} realizado`, color: SERIES.past }, { label: `${y.target} solicitado`, color: SERIES.ref }]} />
                  <PairedBars
                    prevLabel={`${y.ref} realizado`}
                    refLabel={`${y.target} solicitado`}
                    rows={view.accounts.map((a) => ({ label: a.name, prev: Number(a.ref_actual), ref: Number(a.proposed) })).sort((a, b) => Math.max(b.prev, b.ref) - Math.max(a.prev, a.ref))}
                  />
                </>
              ) : view.accounts.length ? (
                <RankBars rows={view.accounts.filter((a) => Number(a.proposed)).map((a) => ({ label: a.name, sub: a.code, value: Number(a.proposed) })).sort((a, b) => b.value - a.value)} />
              ) : (
                <Empty>Sem itens.</Empty>
              )}
            </Card>
          </div>
          <Card title="Contas de ativo">
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr><th>Conta</th><th className="right">{y.prev} realizado</th><th className="right">{y.ref} realizado</th><th className="right">{y.ref} orçado</th><th className="right">{y.target} solicitado</th></tr>
                </thead>
                <tbody>
                  {view.accounts.map((a) => (
                    <tr key={a.account_id}>
                      <td>{a.name}<div className="muted small mono">{a.code}</div></td>
                      <td className="right">{fmtMoney(a.prev_actual)}</td>
                      <td className="right">{fmtMoney(a.ref_actual)}</td>
                      <td className="right">{fmtMoney(a.ref_budget)}</td>
                      <td className="right"><strong>{fmtMoney(a.proposed)}</strong></td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr>
                    <td>Total</td>
                    <td className="right">{fmtMoney(view.totals.prev_actual)}</td>
                    <td className="right">{fmtMoney(view.totals.ref_actual)}</td>
                    <td className="right">{fmtMoney(view.totals.ref_budget)}</td>
                    <td className="right"><strong>{fmtMoney(view.totals.proposed)}</strong></td>
                  </tr>
                </tfoot>
              </table>
            </div>
          </Card>
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

      {projectForm && (
        <ProjectForm
          submissionId={head.submission_id}
          project={projectForm.project}
          options={options}
          onClose={() => setProjectForm(null)}
          onSaved={async (p) => {
            const isNew = !projectForm.project;
            setProjectForm(null);
            await refresh();
            if (isNew) setItemForm({ projectId: p.id });
          }}
        />
      )}
      {itemForm && (
        <ItemForm
          projectId={itemForm.projectId}
          item={itemForm.item}
          options={options}
          onClose={() => setItemForm(null)}
          onSaved={async () => {
            setItemForm(null);
            await refresh();
          }}
        />
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
            {pending.action === "submit" && <p>Depois do envio, o CAPEX fica bloqueado para edição até a Controladoria devolver para ajuste ou você retirar o envio.</p>}
            {pending.action === "approve" && <p>Aprovar o CAPEX de {fmtMoney(total)} deste centro de custo.</p>}
            {pending.action === "consolidate" && <p>Consolidar congela o CAPEX deste centro de custo na versão {head.version}.</p>}
            <label>
              Comentário {pending.requires_comment ? "(obrigatório)" : "(opcional)"}
              <textarea rows={3} value={comment} onChange={(e) => setComment(e.target.value)} />
            </label>
          </div>
        </Modal>
      )}
    </>
  );
}
