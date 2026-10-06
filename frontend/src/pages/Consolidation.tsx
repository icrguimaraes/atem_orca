import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, download, type AttentionPoint, type Company, type ConsolidationOverview } from "../api";
import { useAuth } from "../auth";
import { DivergingBars, Legend, MonthlyBars, PairedBars, SERIES, StatusBar, Waterfall } from "../components/charts";
import { Alert, Badge, Card, Empty, Loading, Modal, PageHeader, SearchBox, Stat, useLoad } from "../components/ui";
import { FLAG_LABELS, SUBMISSION_STATUS, fmtCompact, fmtDateTime, fmtInt, fmtMoney, fmtPct } from "../labels";

const MODULES = ["OPEX", "CAPEX", "PERSONNEL"] as const;
const MODULE_ROUTE: Record<string, string> = { OPEX: "/orcamento", CAPEX: "/capex", PERSONNEL: "/pessoal" };
const MODULE_COLOR: Record<string, string> = { OPEX: SERIES.ref, CAPEX: SERIES.prev, PERSONNEL: SERIES.budget };
const ORDER = ["DRAFT", "IN_PROGRESS", "ADJUSTMENT_REQUESTED", "SUBMITTED", "UNDER_REVIEW", "APPROVED", "CONSOLIDATED"];
const SEVERITY: Record<string, { label: string; tone: "bad" | "warn" | "neutral" | "info" }> = {
  high: { label: "Alta", tone: "bad" },
  medium: { label: "Média", tone: "warn" },
  low: { label: "Baixa", tone: "neutral" },
  info: { label: "Info", tone: "info" },
};

const pct = (a: number, b: number) => (b ? fmtPct(String(a / b - 1)) : "—");

export default function Consolidation() {
  const { can } = useAuth();
  const isController = can("CONTROLLER");
  const [versionId, setVersionId] = useState<number | null>(null);
  const [companyId, setCompanyId] = useState("");
  const [q, setQ] = useState("");
  const [flag, setFlag] = useState("");
  const [moduleFilter, setModuleFilter] = useState("");
  const [action, setAction] = useState<"freeze" | "revise" | null>(null);
  const [reason, setReason] = useState("");
  const [major, setMajor] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "good" | "bad"; text: string } | null>(null);
  const params = new URLSearchParams();
  if (versionId) params.set("version_id", String(versionId));
  if (companyId) params.set("company_id", companyId);
  const qs = params.toString();
  const { data, error, reload } = useLoad(() => api<ConsolidationOverview>(`/consolidation/overview?${qs}`), [qs]);
  const points = useLoad(() => api<{ points: AttentionPoint[]; counts: Record<string, number> }>(`/consolidation/attention-points${companyId ? `?company_id=${companyId}` : ""}`), [companyId]);
  const companies = useLoad(() => api<Company[]>("/companies"));

  const matrix = useMemo(
    () => (data?.matrix ?? []).filter((r) => !q || `${r.code} ${r.name} ${r.manager_name ?? ""}`.toLowerCase().includes(q.toLowerCase())),
    [data, q],
  );
  const variations = useMemo(
    () => (data?.variations ?? []).filter((v) => (!flag || v.flags.includes(flag)) && (!moduleFilter || v.module === moduleFilter)),
    [data, flag, moduleFilter],
  );

  async function runVersionAction() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api<{ version: string; lines?: number }>(`/consolidation/${action}`, { method: "POST", body: JSON.stringify({ reason: reason || null, major }) });
      setMsg({
        tone: "good",
        text: action === "freeze"
          ? `Versão ${r.version} congelada (${fmtInt(r.lines ?? 0)} linhas na fotografia). Os orçamentos ficam só para leitura.`
          : `Revisão ${r.version} aberta: orçamentos copiados e liberados para os ajustes. A versão anterior continua disponível para consulta.`,
      });
      setAction(null);
      setReason("");
      setVersionId(null);
      reload();
      points.reload();
    } catch (err) {
      setMsg({ tone: "bad", text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  }

  async function exportXlsx() {
    setMsg(null);
    try {
      await download(`/consolidation/export.xlsx?${qs}`, `Orcamento_${data?.years.target}_v${data?.version.label}.xlsx`);
    } catch (err) {
      setMsg({ tone: "bad", text: (err as Error).message });
    }
  }

  if (error) return <Alert>{error}</Alert>;
  if (!data) return <Loading />;
  const y = data.years;
  const total = Number(data.total);
  const refTotal = Number(data.ref_total);
  const frozen = data.version.status === "FROZEN";
  const current = data.versions.find((v) => v.current);
  const viewingCurrent = data.version.id === current?.id;

  return (
    <>
      <PageHeader
        title="Consolidação e exportação"
        subtitle={`Orçamento ${y.target} completo (OPEX + CAPEX + Pessoal) por empresa, centro de custo, conta e mês, comparado a ${y.ref} anualizado e ${y.prev} realizado.`}
        actions={
          <>
            <button className="btn" onClick={exportXlsx}>Exportar Excel</button>
            {isController && viewingCurrent && !frozen && <button className="btn btn-primary" onClick={() => setAction("freeze")}>Congelar versão {data.version.label}</button>}
            {isController && viewingCurrent && frozen && <button className="btn btn-primary" onClick={() => setAction("revise")}>Abrir revisão</button>}
          </>
        }
      />
      <div className="filters">
        <select value={versionId ?? ""} onChange={(e) => setVersionId(e.target.value ? Number(e.target.value) : null)} aria-label="Versão">
          {data.versions.map((v) => (
            <option key={v.id} value={v.current ? "" : v.id}>
              Versão {v.label} · {v.status === "FROZEN" ? "congelada" : "em elaboração"}{v.current ? " (atual)" : ""}
            </option>
          ))}
        </select>
        <select value={companyId} onChange={(e) => setCompanyId(e.target.value)} aria-label="Empresa">
          <option value="">Todas as empresas</option>
          {(companies.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.code} · {c.short_name ?? c.name}</option>)}
        </select>
        <Badge tone={frozen ? "info" : "warn"}>{frozen ? `Versão ${data.version.label} congelada` : `Versão ${data.version.label} em elaboração`}</Badge>
      </div>
      {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}
      {frozen && viewingCurrent && <Alert tone="info">Esta versão está congelada: os números vêm da fotografia gravada no congelamento e nenhum orçamento aceita alterações. Para ajustar, abra uma revisão.</Alert>}

      <div className="stats">
        <Stat label={`Orçamento ${y.target}`} value={fmtCompact(total)} tone="warn" hint={refTotal ? `${pct(total, refTotal)} vs ${y.ref} anualizado (${fmtCompact(refTotal)})` : "sem realizado de referência"} />
        {MODULES.map((m) => {
          const mod = data.modules[m];
          const p = Number(mod.proposed);
          const r = Number(mod.ref_annualized);
          return <Stat key={m} label={mod.label} value={fmtCompact(p)} hint={r ? `${pct(p, r)} vs ${y.ref} anualizado` : `${y.ref} sem realizado`} />;
        })}
      </div>

      {total > 0 && (
        <>
          <div className="grid-2">
            <Card title={`Ponte ${y.ref} anualizado → ${y.target} por módulo`}>
              <Waterfall
                start={{ label: `${y.ref} anualizado`, value: refTotal }}
                end={{ label: `${y.target} orçado`, value: total }}
                steps={MODULES.map((m) => ({ label: data.modules[m].label, delta: Number(data.modules[m].proposed) - Number(data.modules[m].ref_annualized) }))}
              />
            </Card>
            <Card title={`${y.target} por mês e módulo`}>
              <Legend items={MODULES.map((m) => ({ label: data.modules[m].label, color: MODULE_COLOR[m] }))} />
              <MonthlyBars height={220} series={MODULES.map((m) => ({ label: data.modules[m].label, color: MODULE_COLOR[m], values: data.modules[m].monthly.map(Number) }))} />
            </Card>
          </div>
          <div className="grid-2">
            <Card title={`Por pacote · ${y.ref} anualizado × ${y.target}`}>
              <Legend items={[{ label: `${y.ref} anualizado`, color: SERIES.prev }, { label: `${y.target} orçado`, color: SERIES.ref }]} />
              <PairedBars
                prevLabel={`${y.ref} anualizado`}
                refLabel={`${y.target} orçado`}
                rows={data.by_package.slice(0, 14).map((p) => {
                  const a = Number(p.ref_annualized), b = Number(p.proposed);
                  return { label: p.label, prev: a, ref: b, note: a && b ? fmtPct(String(b / a - 1)) : undefined };
                })}
              />
            </Card>
            <Card title="Maiores variações por conta">
              {data.variations.some((v) => Number(v.variation)) ? (
                <DivergingBars rows={data.variations.filter((v) => Number(v.variation)).slice(0, 12).map((v) => ({ label: v.name ?? v.account, sub: v.account, delta: Number(v.variation), from: Number(v.ref_annualized), to: Number(v.proposed) }))} />
              ) : (
                <Empty>Sem variações.</Empty>
              )}
            </Card>
          </div>
        </>
      )}

      <Card title="Pontos de atenção" actions={points.data && <span className="muted small">{fmtInt(points.data.counts.high ?? 0)} alta · {fmtInt(points.data.counts.medium ?? 0)} média · {fmtInt(points.data.counts.low ?? 0)} baixa</span>}>
        {!points.data ? (
          <Loading />
        ) : points.data.points.length === 0 ? (
          <Empty>Nenhuma pendência. O orçamento está pronto para consolidar.</Empty>
        ) : (
          <div className="table-wrap scroll-y">
            <table className="table">
              <thead><tr><th>Prioridade</th><th>Módulo</th><th>Centro de custo</th><th>O que fazer</th></tr></thead>
              <tbody>
                {points.data.points.map((p, i) => (
                  <tr key={i}>
                    <td><Badge tone={SEVERITY[p.severity].tone}>{SEVERITY[p.severity].label}</Badge></td>
                    <td>{p.module_label}</td>
                    <td>{p.link ? <Link className="link" to={p.link}>{p.cost_center}</Link> : <span className="muted">—</span>}</td>
                    <td>{p.message}{p.kind === "ACCOUNT_CONFIG" && <> · <Link className="link" to="/ciclo">Ciclo e parâmetros</Link></>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Andamento por módulo">
        <div className="stack">
          {MODULES.map((m) => (
            <div key={m}>
              <div className="small" style={{ marginBottom: 6 }}><strong>{data.modules[m].label}</strong></div>
              <StatusBar items={ORDER.map((k) => ({ key: k, label: SUBMISSION_STATUS[k].label, count: data.status_counts[m]?.[k] ?? 0, tone: SUBMISSION_STATUS[k].tone }))} />
            </div>
          ))}
        </div>
      </Card>

      <Card title="Centros de custo × módulos" actions={<SearchBox value={q} onChange={setQ} placeholder="Buscar CC ou gestor" />}>
        {matrix.length === 0 ? (
          <Empty>Nenhum centro de custo.</Empty>
        ) : (
          <div className="table-wrap scroll-y">
            <table className="table">
              <thead>
                <tr>
                  <th>Centro de custo</th>
                  {MODULES.map((m) => <th key={m}>{data.modules[m].label}</th>)}
                  <th className="right">Total {y.target}</th>
                </tr>
              </thead>
              <tbody>
                {matrix.map((r) => (
                  <tr key={r.cost_center_id}>
                    <td>{r.name}<div className="muted small mono">{r.company_code} · {r.code}{r.manager_name ? ` · ${r.manager_name}` : ""}</div></td>
                    {MODULES.map((m) => (
                      <td key={m}>
                        <Link to={`${MODULE_ROUTE[m]}/${r.cost_center_id}`} className="nowrap" style={{ textDecoration: "none" }}>
                          <Badge tone={SUBMISSION_STATUS[r.status[m]]?.tone ?? "neutral"}>{SUBMISSION_STATUS[r.status[m]]?.label ?? r.status[m]}</Badge>
                        </Link>
                        {Number(r.totals[m]) ? <div className="small">{fmtCompact(r.totals[m])}</div> : null}
                      </td>
                    ))}
                    <td className="right"><strong>{Number(r.total) ? fmtMoney(r.total) : "—"}</strong></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card
        title="Variações por conta"
        actions={
          <div className="inline-controls">
            <select value={moduleFilter} onChange={(e) => setModuleFilter(e.target.value)} aria-label="Módulo">
              <option value="">Todos os módulos</option>
              {MODULES.map((m) => <option key={m} value={m}>{data.modules[m].label}</option>)}
            </select>
            <select value={flag} onChange={(e) => setFlag(e.target.value)} aria-label="Alerta">
              <option value="">Todos os alertas</option>
              {Object.entries(data.flag_counts).map(([f, n]) => <option key={f} value={f}>{FLAG_LABELS[f]?.label ?? f} ({n})</option>)}
            </select>
          </div>
        }
      >
        {variations.length === 0 ? (
          <Empty>Nenhuma conta com esses filtros.</Empty>
        ) : (
          <div className="table-wrap scroll-y">
            <table className="table">
              <thead>
                <tr>
                  <th>Conta</th>
                  <th>Módulo</th>
                  <th className="right">{y.prev} realizado</th>
                  <th className="right">{y.ref} anualizado</th>
                  <th className="right">{y.target} orçado</th>
                  <th className="right">Variação</th>
                  <th>Alertas</th>
                </tr>
              </thead>
              <tbody>
                {variations.map((v) => (
                  <tr key={v.account}>
                    <td>{v.name ?? "—"}<div className="muted small mono">{v.account}{v.package ? ` · ${v.package}` : ""}</div></td>
                    <td>{data.modules[v.module as "OPEX"]?.label ?? v.module}</td>
                    <td className="right">{fmtMoney(v.prev_actual)}</td>
                    <td className="right">{fmtMoney(v.ref_annualized)}</td>
                    <td className="right"><strong>{fmtMoney(v.proposed)}</strong></td>
                    <td className="right nowrap">{fmtMoney(v.variation)}<div className="muted small">{fmtPct(v.variation_pct)}</div></td>
                    <td>{v.flags.map((f) => <Badge key={f} tone={FLAG_LABELS[f]?.tone ?? "neutral"}>{FLAG_LABELS[f]?.label ?? f}</Badge>)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="muted small">
          Pessoal entra nas contas {data.personnel_accounts.salary?.code} ({data.personnel_accounts.salary?.name}), {data.personnel_accounts.charges?.code} ({data.personnel_accounts.charges?.name}) e{" "}
          {data.personnel_accounts.severance?.code} ({data.personnel_accounts.severance?.name}) — configure em <Link className="link" to="/ciclo">Ciclo e parâmetros</Link>.
        </p>
      </Card>

      <Card title="Versões do orçamento">
        <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Versão</th><th>Situação</th><th>Motivo</th><th>Criada</th><th>Congelada</th></tr></thead>
          <tbody>
            {data.versions.map((v) => (
              <tr key={v.id}>
                <td><strong>{v.label}</strong>{v.current && <span className="muted small"> · atual</span>}</td>
                <td>{v.status === "FROZEN" ? <Badge tone="info">Congelada</Badge> : <Badge tone="warn">Em elaboração</Badge>}</td>
                <td className="small">{v.reason ?? "—"}</td>
                <td className="small">{fmtDateTime(v.created_at)}</td>
                <td className="small">{v.frozen_at ? fmtDateTime(v.frozen_at) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      </Card>

      {action && (
        <Modal
          title={action === "freeze" ? `Congelar versão ${data.version.label}` : "Abrir revisão do orçamento"}
          onClose={() => setAction(null)}
          footer={
            <>
              <button className="btn btn-ghost" onClick={() => setAction(null)}>Cancelar</button>
              <button className="btn btn-primary" disabled={busy || (action === "revise" && !reason.trim())} onClick={runVersionAction}>
                {busy ? "Processando…" : action === "freeze" ? "Congelar" : "Abrir revisão"}
              </button>
            </>
          }
        >
          <div className="stack">
            {action === "freeze" ? (
              <p>
                Grava uma fotografia do orçamento consolidado ({fmtMoney(total)}) e bloqueia alterações em OPEX, CAPEX e Pessoal.
                Os relatórios desta versão passam a ler a fotografia, mesmo que o quadro ou os cadastros mudem depois.
              </p>
            ) : (
              <>
                <p>Cria a próxima versão a partir da {data.version.label}, copiando linhas, itens e movimentações. Orçamentos consolidados voltam para "Aprovado"; reabra para ajuste só os CCs que vão mudar.</p>
                <label className="check"><input type="checkbox" checked={major} onChange={(e) => setMajor(e.target.checked)} />Revisão geral (nova versão principal, ex.: 2.0)</label>
              </>
            )}
            <label>
              Motivo {action === "revise" ? "(obrigatório)" : "(opcional)"}
              <textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder={action === "freeze" ? "Ex.: fechamento aprovado pela diretoria" : "Ex.: revisão de pessoal após reestruturação"} />
            </label>
          </div>
        </Modal>
      )}
    </>
  );
}
