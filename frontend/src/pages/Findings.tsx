import { useMemo, useState } from "react";
import { usePersistentState } from "../persist";
import { Link, useSearchParams } from "react-router-dom";
import { api, download, type Finding, type Findings as FindingsData } from "../api";
import { FilterBar } from "../components/FilterBar";
import { BulkFix, FixModal, InlineFix, KeepModal, canBulkFix } from "../components/FindingForms";
import { Alert, Badge, Card, Empty, Loading, PageHeader, SearchBox, Stat, useLoad } from "../components/ui";
import { SUBMISSION_STATUS, fmtDateTime, fmtInt, fmtMoney, type Tone } from "../labels";

const SEVERITY: Record<Finding["severity"], { label: string; tone: Tone }> = {
  CRITICAL: { label: "Crítico", tone: "bad" },
  WARNING: { label: "Aviso", tone: "warn" },
};
const MODULE_OPTIONS = [
  { value: "", label: "Todos os módulos" },
  { value: "OPEX", label: "OPEX" },
  { value: "CAPEX", label: "CAPEX" },
  { value: "PERSONNEL", label: "Pessoal" },
];
// o que fazer em cada tipo, em uma linha (cabeçalho do grupo)
const KIND_HINTS: Record<string, string> = {
  OPEX_JUSTIFICATION: "Toda conta orçada (ou zerada com histórico relevante) precisa de justificativa: escreva aqui ou na tela Justificativas.",
  TRAVEL_NO_FARE: "A planilha não trouxe a passagem: informe o valor (vai para o mês de ida) ou mantenha, se não houver.",
  TRAVEL_RATE: "Diária ou hospedagem sem tarifa no ciclo: confira no orçamento do CC.",
  CAPEX_SCHEDULE_MISMATCH: "A soma dos meses não fecha com o valor do item: distribua o valor nos meses.",
  CAPEX_ACCOUNT_MISMATCH: "A conta do item difere da indicada no catálogo de ativos: use a do catálogo ou mantenha com o motivo.",
  CAPEX_NO_PROJECT_TYPE: "Projeto sem tipo: escolha o tipo.",
  CAPEX_NO_JUSTIFICATION: "Toda solicitação de CAPEX precisa de justificativa: escreva a justificativa.",
  PERSONNEL_JUSTIFICATION: "Toda movimentação (contratação, desligamento, transferência, promoção, reajuste) precisa de justificativa.",
  PERSONNEL_NO_SALARY: "A planilha não trouxe o novo salário: informe o valor mensal.",
  PERSONNEL_NO_MONTH: "A planilha não trouxe o mês da ação: escolha o mês (até lá, não mexe no custo).",
  PERSONNEL_CC_GUESSED: "O CC veio vazio e o sistema definiu pelo cargo: confirme; se estiver errado, corrija na planilha e importe de novo.",
  STRUCTURE_NO_SECTOR: "O CC não tem área e setor e aparece em \"Sem setor\" no Painel: escolha o setor.",
  PERSONNEL_NO_CC: "Colaborador ativo sem centro de custo fica fora do orçamento: escolha o CC. Se a planilha trazia promoção, desligamento ou vaga, reimporte o quadro com o CC padrão.",
  PERSONNEL_SPLIT_OVERLAP: "O rateio de encargos inclui contas que já recebem o abono ou o bônus como linha própria: revise em Premissas de pessoal (\"Retirar do rateio\" redistribui o valor; o total não muda).",
};
type Tab = "open" | "done" | "files";

interface CcRow { id: number; label: string; sector: string | null; critical: number; warning: number; resolved: number }

/** Apontamentos como fila de trabalho: escolha o CC, resolva por tipo (na própria linha ou em lote) e acompanhe o
 *  progresso; o registro do que foi corrigido/mantido e os templates corrigidos ficam nas outras abas. */
export default function Findings() {
  const { data, error, reload } = useLoad(() => api<FindingsData>("/findings"));
  const [tab, setTab] = useState<Tab>("open");
  const [module, setModule] = usePersistentState("apontamentos.module", "");
  const [severity, setSeverity] = usePersistentState("apontamentos.severity", "");
  const [params] = useSearchParams();
  // ?cc= vem da Validação ("Corrigir em Apontamentos") e vale sobre o filtro guardado
  const [cc, setCc] = usePersistentState("apontamentos.cc", params.get("cc") ?? "", params.has("cc"));
  const [search, setSearch] = usePersistentState("apontamentos.search", "");
  const [schedule, setSchedule] = useState<Finding | null>(null);
  const [keeping, setKeeping] = useState<Finding[] | null>(null);
  const [msg, setMsg] = useState<{ tone: "good" | "bad"; text: string } | null>(null);
  const items = data?.items ?? [];
  const reviews = data?.reviews ?? [];

  // filtros sem o CC (a fila de CCs mostra as contagens com módulo/gravidade/busca)
  const base = useMemo(() => {
    const q = search.trim().toLowerCase();
    return items.filter(
      (i) =>
        (!module || i.module === module) &&
        (!severity || i.severity === severity) &&
        (!q || `${i.subject} ${i.detail ?? ""} ${i.cost_center} ${i.kind_label}`.toLowerCase().includes(q)),
    );
  }, [items, module, severity, search]);
  const shown = useMemo(() => base.filter((i) => !cc || String(i.cost_center_id) === cc), [base, cc]);

  const queue = useMemo(() => {
    const rows = new Map<number, CcRow>();
    for (const i of base) {
      const row = rows.get(i.cost_center_id) ?? { id: i.cost_center_id, label: i.cost_center, sector: i.sector ?? null, critical: 0, warning: 0, resolved: 0 };
      row[i.severity === "CRITICAL" ? "critical" : "warning"] += 1;
      rows.set(i.cost_center_id, row);
    }
    for (const r of reviews) {
      const row = rows.get(r.cost_center_id);
      if (row) row.resolved += 1;
    }
    return [...rows.values()].sort((a, b) => b.critical - a.critical || b.critical + b.warning - (a.critical + a.warning) || a.label.localeCompare(b.label));
  }, [base, reviews]);

  const groups = useMemo(() => {
    const out = new Map<string, { kind: string; label: string; severity: Finding["severity"]; items: Finding[] }>();
    for (const i of shown) {
      const g = out.get(i.kind) ?? { kind: i.kind, label: i.kind_label, severity: i.severity, items: [] };
      g.items.push(i);
      if (i.severity === "CRITICAL") g.severity = "CRITICAL";
      out.set(i.kind, g);
    }
    return [...out.values()].sort((a, b) => (a.severity === b.severity ? b.items.length - a.items.length : a.severity === "CRITICAL" ? -1 : 1));
  }, [shown]);

  if (error) return <Alert>{error}</Alert>;
  if (!data) return <Loading />;
  const critical = shown.filter((i) => i.severity === "CRITICAL").length;
  const filtered = Boolean(module || severity || cc || search);
  const clear = () => { setModule(""); setSeverity(""); setCc(""); setSearch(""); };
  const ccOptions = [{ value: "", label: "Todos os centros de custo" }, ...queue.map((q) => ({ value: String(q.id), label: q.label }))];
  const selected = queue.find((q) => String(q.id) === cc);
  const resolvedHere = reviews.filter((r) => !cc || String(r.cost_center_id) === cc);
  const progress = (pending: number, resolved: number) => (pending + resolved ? Math.round((resolved / (pending + resolved)) * 100) : 100);
  const done = (text: string) => {
    setSchedule(null);
    setKeeping(null);
    setMsg({ tone: text.includes("não aplicado") ? "bad" : "good", text });
    reload();
  };
  async function save(path: string, name: string) {
    setMsg(null);
    try {
      await download(path, name);
    } catch (err) {
      setMsg({ tone: "bad", text: (err as Error).message });
    }
  }
  async function reopen(id: number) {
    setMsg(null);
    try {
      await api(`/findings/reviews/${id}`, { method: "DELETE" });
      setMsg({ tone: "good", text: "Aviso reaberto: voltou para a lista de pendentes." });
      reload();
    } catch (err) {
      setMsg({ tone: "bad", text: (err as Error).message });
    }
  }
  const tag = `${data.target_year}_v${data.version}`;
  const files = data.cost_centers.filter((c) => !cc || String(c.id) === cc);

  return (
    <>
      <PageHeader
        title="Apontamentos"
        subtitle={`Versão ${data.version} · fila de correção dos orçamentos em aberto, por centro de custo e tipo; críticos bloqueiam o envio, avisos podem ser mantidos com o motivo.`}
        actions={
          <>
            <button type="button" className="btn" onClick={() => save("/findings/export.xlsx", `Apontamentos_${tag}.xlsx`)}>Relatório (Excel)</button>
          </>
        }
      />

      <div className="stats">
        <Stat label="Pendentes" value={fmtInt(shown.length)} hint={filtered ? `de ${fmtInt(items.length)} no total` : `versão ${data.version}`} />
        <Stat label="Críticos" value={fmtInt(critical)} tone={critical ? "bad" : "good"} hint="bloqueiam o envio" />
        <Stat label="Avisos" value={fmtInt(shown.length - critical)} hint="não bloqueiam; para a análise" />
        <Stat label="Centros de custo" value={fmtInt(new Set(shown.map((i) => i.cost_center_id)).size)} hint="com pendência" />
        <Stat
          label="Resolvidos"
          value={fmtInt(resolvedHere.length)}
          tone={resolvedHere.length ? "good" : undefined}
          hint={`${progress(shown.length, resolvedHere.length)}% do que foi apontado`}
        />
      </div>

      <div className="tabs">
        <button type="button" className={tab === "open" ? "active" : ""} onClick={() => setTab("open")}>
          Pendentes <span className={`count${critical ? " count-bad" : ""}`}>{fmtInt(shown.length)}</span>
        </button>
        <button type="button" className={tab === "done" ? "active" : ""} onClick={() => setTab("done")}>
          Corrigidos e mantidos <span className="count">{fmtInt(resolvedHere.length)}</span>
        </button>
        <button type="button" className={tab === "files" ? "active" : ""} onClick={() => setTab("files")}>
          Templates corrigidos <span className="count">{fmtInt(files.length)}</span>
        </button>
      </div>

      <FilterBar
        onReset={clear}
        resetCount={[module, severity, cc, search].filter(Boolean).length}
        fields={[
          { key: "module", label: "Módulo", value: module, onChange: setModule, options: MODULE_OPTIONS },
          {
            key: "severity", label: "Gravidade", value: severity, onChange: setSeverity,
            options: [{ value: "", label: "Críticos e avisos" }, { value: "CRITICAL", label: "Só críticos" }, { value: "WARNING", label: "Só avisos" }],
          },
          { key: "cc", label: "Centro de custo", value: cc, onChange: setCc, options: ccOptions, wide: true },
        ]}
        extra={<SearchBox value={search} onChange={setSearch} placeholder="Buscar item, pessoa, conta…" />}
        chips={search ? [{ label: `Busca: “${search}”`, onRemove: () => setSearch("") }] : []}
      />
      {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}
      {tab === "open" && (
        <details className="finding-rules">
          <summary>O que bloqueia o envio e o que é só aviso</summary>
          <div className="finding-rules-grid">
            <div>
              <Badge tone="bad">Crítico · bloqueia o envio</Badge>
              <ul>
                <li><strong>Justificativas (todas):</strong> conta do OPEX orçada ou zerada com histórico, toda movimentação de pessoal e toda solicitação de CAPEX — o gestor da área defende o número. Veja a tela <Link to="/justificativas" className="link">Justificativas</Link>.</li>
                <li><strong>Pessoal:</strong> novo salário e mês da ação que não vieram na planilha.</li>
                <li><strong>CAPEX:</strong> cronograma diferente do valor do item; projeto sem tipo.</li>
              </ul>
            </div>
            <div>
              <Badge tone="warn">Aviso · não bloqueia</Badge>
              <ul>
                <li><strong>OPEX:</strong> passagem zerada; diária ou hospedagem sem tarifa.</li>
                <li><strong>CAPEX:</strong> conta diferente do catálogo de ativos, software no CAPEX, valor baixo, vida útil curta.</li>
                <li><strong>Pessoal:</strong> centro de custo definido pelo cargo (confirmar).</li>
                <li><strong>Estrutura:</strong> CC sem área e setor (Controladoria).</li>
                <li><strong>Pessoal:</strong> colaborador sem centro de custo — fica fora do orçamento até escolher o CC (Controladoria).</li>
              </ul>
              <p className="muted small">Avisos podem ser corrigidos ou mantidos pela Controladoria com o motivo (um a um ou em lote).</p>
            </div>
          </div>
        </details>
      )}

      {tab === "open" && (items.length === 0 ? (
        <Empty>Nenhum apontamento pendente nos orçamentos em aberto da versão {data.version}.</Empty>
      ) : (
        <div className="findings-layout">
          <nav className="cc-queue" aria-label="Centros de custo">
            <button type="button" className={!cc ? "active" : ""} onClick={() => setCc("")}>
              <span className="cc-name">Todos os centros de custo</span>
              <span className="cc-meta">
                {critical > 0 && <Badge tone="bad">{fmtInt(critical)} crít.</Badge>}
                <span className="muted">{fmtInt(base.length)} pendente(s)</span>
              </span>
            </button>
            {queue.map((q) => (
              <button type="button" key={q.id} className={String(q.id) === cc ? "active" : ""} onClick={() => setCc(String(q.id) === cc ? "" : String(q.id))}>
                <span className="cc-name">{q.label}</span>
                {q.sector && <span className="muted small">{q.sector}</span>}
                <span className="cc-meta">
                  {q.critical > 0 && <Badge tone="bad">{fmtInt(q.critical)} crít.</Badge>}
                  {q.warning > 0 && <Badge tone="warn">{fmtInt(q.warning)} aviso{q.warning > 1 ? "s" : ""}</Badge>}
                </span>
                <span className="progress" title={`${q.resolved} resolvido(s)`}><span style={{ width: `${progress(q.critical + q.warning, q.resolved)}%` }} /></span>
              </button>
            ))}
          </nav>

          <div className="findings-main">
            {selected && (
              <div className="findings-cc-head">
                <div>
                  <h2>{selected.label}</h2>
                  <span className="muted small">{selected.sector ?? "Sem área e setor"} · {fmtInt(selected.critical + selected.warning)} pendente(s) · {fmtInt(selected.resolved)} resolvido(s)</span>
                </div>
                <div className="finding-links">
                  {[...new Map(shown.filter((i) => i.link).map((i) => [i.module, i])).values()].map((i) => (
                    <Link key={i.module} className="btn btn-sm" to={i.link!}>Abrir {i.module_label}</Link>
                  ))}
                </div>
              </div>
            )}
            {groups.length === 0 && <Empty>Nenhum apontamento com esses filtros.</Empty>}
            {groups.map((g) => {
              const fixable = g.items.filter((i) => i.fix && i.editable);
              const keepable = g.items.filter((i) => i.can_keep);
              return (
                <Card
                  key={g.kind}
                  title={`${g.label} · ${fmtInt(g.items.length)}`}
                  actions={<Badge tone={SEVERITY[g.severity].tone}>{SEVERITY[g.severity].label}</Badge>}
                >
                  {KIND_HINTS[g.kind] && <p className="muted small finding-hint">{KIND_HINTS[g.kind]}</p>}
                  {(canBulkFix(fixable) || (data.can_review && keepable.length > 1)) && (
                    <div className="bulk-bar">
                      <span className="small muted">Em lote:</span>
                      <BulkFix items={fixable} projectTypes={data.project_types} sectors={data.sectors} onDone={done} />
                      {data.can_review && keepable.length > 1 && (
                        <button type="button" className="btn btn-sm" onClick={() => setKeeping(keepable)}>Manter todos ({keepable.length})…</button>
                      )}
                    </div>
                  )}
                  <div className="finding-rows">
                    {g.items.map((i) => (
                      <div className="finding-row" key={i.key}>
                        <div className="finding-what">
                          {!cc && (
                            <div className="small">
                              {i.link ? <Link className="link" to={i.link}>{i.cost_center}</Link> : i.cost_center}
                              <span className="muted"> · {i.module_label} · {SUBMISSION_STATUS[i.status]?.label ?? i.status}</span>
                            </div>
                          )}
                          <strong>{i.subject}</strong>
                          {i.amount !== null && <span className="nowrap"> · {fmtMoney(i.amount)}</span>}
                          {i.detail && <div className="muted small">{i.detail}</div>}
                          <div className="muted small">{i.message}</div>
                        </div>
                        <div className="finding-do">
                          {i.fix && i.editable && (
                            <InlineFix finding={i} projectTypes={data.project_types} sectors={data.sectors} onDone={(note) => done(`Corrigido: ${note}`)} onSchedule={() => setSchedule(i)} />
                          )}
                          {i.fix && !i.editable && <span className="muted small">Fora de edição: solicite ajuste para corrigir</span>}
                          {!i.fix && i.link && <Link className="btn btn-sm" to={i.link}>{i.link_label ?? "Abrir o orçamento"}</Link>}
                          {i.source && data.can_review && (
                            <Link className="btn btn-ghost btn-sm" to={`/validacao/${i.source.batch_id}?sheet=${encodeURIComponent(i.source.sheet)}&row=${i.source.row}`} title={`${i.source.sheet}, linha ${i.source.row}`}>
                              Ver no Excel
                            </Link>
                          )}
                          {i.can_keep && data.can_review && (
                            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setKeeping([i])}>Manter</button>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </Card>
              );
            })}
          </div>
        </div>
      ))}

      {tab === "done" && (resolvedHere.length === 0 ? (
        <Empty>Nada corrigido ou mantido ainda{cc ? " neste centro de custo" : ""}.</Empty>
      ) : (
        <Card title="Corrigidos e mantidos">
          <p className="muted small">Registro da análise nesta versão: o que foi corrigido aqui e os avisos mantidos, com o motivo. Vai no relatório.</p>
          <div className="table-wrap">
            <table className="table findings-table">
              <thead><tr><th>Quando</th><th>Centro de custo</th><th>Item</th><th>Apontamento</th><th>Ação</th><th>Correção ou motivo</th><th /></tr></thead>
              <tbody>
                {resolvedHere.map((r) => (
                  <tr key={r.id}>
                    <td className="nowrap">{fmtDateTime(r.created_at)}<div className="muted small">{r.user ?? "—"}</div></td>
                    <td className="col-cc">{r.link ? <Link className="link" to={r.link}>{r.cost_center}</Link> : r.cost_center}<div className="muted small">{r.module_label}</div></td>
                    <td className="col-item">{r.subject}</td>
                    <td className="col-msg"><strong>{r.kind_label}</strong><div className="muted">{r.message}</div></td>
                    <td><Badge tone={r.action === "CORRECTED" ? "good" : "info"}>{r.action_label}</Badge></td>
                    <td className="col-msg">{r.note}</td>
                    <td>{r.action === "KEPT" && data.can_review && <button type="button" className="btn btn-ghost btn-sm" onClick={() => reopen(r.id)}>Reabrir</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ))}

      {tab === "files" && (files.length === 0 ? (
        <Empty>Nenhum template para baixar{cc ? " neste centro de custo" : ""}.</Empty>
      ) : (
        <Card title="Templates corrigidos por centro de custo">
          <p className="muted small">O mesmo modelo de Excel que a área preencheu, já com as correções, para devolver ao gestor (reimportável).</p>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Centro de custo</th><th>OPEX</th><th>CAPEX</th></tr></thead>
              <tbody>
                {files.map((c) => (
                  <tr key={c.id}>
                    <td>{c.label}</td>
                    <td>
                      {c.opex_submission_id
                        ? <button type="button" className="btn btn-sm" onClick={() => save(`/opex/submissions/${c.opex_submission_id}/template.xlsx`, `Template_OPEX_${data.target_year}_${c.code}_v${data.version}.xlsx`)}>Baixar</button>
                        : <span className="muted">—</span>}
                    </td>
                    <td>
                      {c.capex_submission_id
                        ? <button type="button" className="btn btn-sm" onClick={() => save(`/capex/submissions/${c.capex_submission_id}/template.xlsx`, `Template_CAPEX_${data.target_year}_${c.code}_v${data.version}.xlsx`)}>Baixar</button>
                        : <span className="muted">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ))}

      {schedule && <FixModal finding={schedule} projectTypes={data.project_types} onClose={() => setSchedule(null)} onDone={(note) => done(`Corrigido: ${note}`)} />}
      {keeping && <KeepModal findings={keeping} onClose={() => setKeeping(null)} onDone={(note) => done(`Aviso mantido: ${note}`)} />}
    </>
  );
}
