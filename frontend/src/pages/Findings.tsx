import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, download, type Finding, type Findings as FindingsData } from "../api";
import { FilterBar } from "../components/FilterBar";
import { FixModal, KeepModal } from "../components/FindingForms";
import { Alert, Badge, Card, Empty, Loading, PageHeader, Stat, useLoad } from "../components/ui";
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

/** Divergências e erros de preenchimento dos orçamentos em aberto, item a item: corrigir ali, manter avisos com
 *  justificativa e gerar os arquivos (relatório dos apontamentos e template corrigido de cada CC). */
export default function Findings() {
  const { data, error, reload } = useLoad(() => api<FindingsData>("/findings"));
  const [module, setModule] = useState("");
  const [severity, setSeverity] = useState("");
  const [kind, setKind] = useState("");
  const [cc, setCc] = useState("");
  const [fixing, setFixing] = useState<Finding | null>(null);
  const [keeping, setKeeping] = useState<Finding | null>(null);
  const [msg, setMsg] = useState<{ tone: "good" | "bad"; text: string } | null>(null);
  const items = data?.items ?? [];

  // o quadro "por tipo" respeita os outros filtros, mas não o próprio tipo (para dar para trocar de tipo)
  const base = useMemo(
    () => items.filter((i) => (!module || i.module === module) && (!severity || i.severity === severity) && (!cc || String(i.cost_center_id) === cc)),
    [items, module, severity, cc],
  );
  const shown = useMemo(() => base.filter((i) => !kind || i.kind === kind), [base, kind]);
  const byKind = useMemo(() => {
    const out = new Map<string, { kind: string; label: string; severity: Finding["severity"]; count: number; ccs: Set<number> }>();
    for (const i of base) {
      const row = out.get(i.kind) ?? { kind: i.kind, label: i.kind_label, severity: i.severity, count: 0, ccs: new Set<number>() };
      row.count += 1;
      row.ccs.add(i.cost_center_id);
      if (i.severity === "CRITICAL") row.severity = "CRITICAL";
      out.set(i.kind, row);
    }
    return [...out.values()].sort((a, b) => (a.severity === b.severity ? b.count - a.count : a.severity === "CRITICAL" ? -1 : 1));
  }, [base]);
  const ccOptions = useMemo(() => {
    const seen = new Map<number, string>();
    for (const i of items) seen.set(i.cost_center_id, i.cost_center);
    return [{ value: "", label: "Todos os centros de custo" }, ...[...seen].sort((a, b) => a[1].localeCompare(b[1])).map(([id, label]) => ({ value: String(id), label }))];
  }, [items]);
  const kindOptions = useMemo(() => {
    const seen = new Map<string, string>();
    for (const i of items) seen.set(i.kind, i.kind_label);
    return [{ value: "", label: "Todos os tipos" }, ...[...seen].sort((a, b) => a[1].localeCompare(b[1])).map(([value, label]) => ({ value, label }))];
  }, [items]);

  if (error) return <Alert>{error}</Alert>;
  if (!data) return <Loading />;
  const critical = shown.filter((i) => i.severity === "CRITICAL").length;
  const filtered = Boolean(module || severity || kind || cc);
  const clear = () => { setModule(""); setSeverity(""); setKind(""); setCc(""); };
  const done = (text: string) => {
    setFixing(null);
    setKeeping(null);
    setMsg({ tone: "good", text });
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

  return (
    <>
      <PageHeader
        title="Apontamentos"
        subtitle="Divergências e erros de preenchimento nos orçamentos em aberto, item a item. Corrija aqui mesmo; críticos bloqueiam o envio, avisos podem ser mantidos com justificativa."
        actions={
          <>
            {filtered && <button type="button" className="btn" onClick={clear}>Limpar filtros</button>}
            <button type="button" className="btn" onClick={() => save("/findings/export.xlsx", `Apontamentos_${tag}.xlsx`)}>Relatório (Excel)</button>
          </>
        }
      />
      <FilterBar
        fields={[
          { key: "module", label: "Módulo", value: module, onChange: setModule, options: MODULE_OPTIONS },
          {
            key: "severity", label: "Gravidade", value: severity, onChange: setSeverity,
            options: [{ value: "", label: "Críticos e avisos" }, { value: "CRITICAL", label: "Só críticos" }, { value: "WARNING", label: "Só avisos" }],
          },
          { key: "kind", label: "Tipo", value: kind, onChange: setKind, options: kindOptions },
          { key: "cc", label: "Centro de custo", value: cc, onChange: setCc, options: ccOptions, wide: true },
        ]}
      />
      {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}
      {items.length === 0 ? (
        <Empty>Nenhum apontamento pendente nos orçamentos em aberto da versão {data.version}.</Empty>
      ) : (
        <>
          <div className="stats">
            <Stat label="Pendentes" value={fmtInt(shown.length)} hint={filtered ? `de ${fmtInt(items.length)} no total` : `versão ${data.version}`} />
            <Stat label="Críticos" value={fmtInt(critical)} tone={critical ? "bad" : "good"} hint="bloqueiam o envio" />
            <Stat label="Avisos" value={fmtInt(shown.length - critical)} tone={shown.length - critical ? "warn" : undefined} hint="para a análise" />
            <Stat label="Corrigidos e mantidos" value={fmtInt(data.reviews.length)} hint="registro da análise" />
          </div>

          <Card title="Por tipo" actions={kind ? <button type="button" className="btn btn-ghost btn-sm" onClick={() => setKind("")}>Desmarcar</button> : undefined}>
            <p className="muted small">Clique num tipo para filtrar a lista; de novo para desmarcar.</p>
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Tipo</th><th>Gravidade</th><th className="right">Apontamentos</th><th className="right">CCs</th></tr></thead>
                <tbody>
                  {byKind.map((k) => (
                    <tr key={k.kind} style={kind && kind !== k.kind ? { opacity: 0.45 } : undefined}>
                      <td><button type="button" className="row-filter" onClick={() => setKind(kind === k.kind ? "" : k.kind)}>{k.label}</button></td>
                      <td><Badge tone={SEVERITY[k.severity].tone}>{SEVERITY[k.severity].label}</Badge></td>
                      <td className="right">{fmtInt(k.count)}</td>
                      <td className="right">{fmtInt(k.ccs.size)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <Card title="Lista de apontamentos">
            {shown.length === 0 ? (
              <Empty>Nenhum apontamento com esses filtros.</Empty>
            ) : (
              <div className="table-wrap">
                <table className="table findings-table">
                  <thead>
                    <tr><th>Gravidade</th><th>Centro de custo</th><th>Item</th><th>Apontamento</th></tr>
                  </thead>
                  <tbody>
                    {shown.map((i) => (
                      <tr key={i.key}>
                        <td><Badge tone={SEVERITY[i.severity].tone}>{SEVERITY[i.severity].label}</Badge></td>
                        <td className="col-cc">
                          {i.link ? <Link className="link" to={i.link}>{i.cost_center}</Link> : i.cost_center}
                          <div className="muted small">{i.module_label} · {SUBMISSION_STATUS[i.status]?.label ?? i.status}</div>
                        </td>
                        <td className="col-item">
                          {i.subject}
                          {i.detail && <div className="muted small">{i.detail}</div>}
                          {i.amount !== null && <div className="small nowrap"><strong>{fmtMoney(i.amount)}</strong></div>}
                        </td>
                        <td className="col-msg">
                          <strong>{i.kind_label}</strong>
                          <div>{i.message}</div>
                          {(i.fix || (i.can_keep && data.can_review)) && (
                            <div className="finding-actions">
                              {i.fix && i.editable && <button type="button" className="btn btn-sm" onClick={() => setFixing(i)}>Corrigir</button>}
                              {i.can_keep && data.can_review && <button type="button" className="btn btn-ghost btn-sm" onClick={() => setKeeping(i)}>Manter</button>}
                              {i.fix && !i.editable && <span className="muted small">Fora de edição: solicite ajuste para corrigir</span>}
                            </div>
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
      )}

      {data.reviews.length > 0 && (
        <Card title="Corrigidos e mantidos">
          <p className="muted small">Registro da análise nesta versão: o que foi corrigido aqui e os avisos mantidos, com o motivo. Vai no relatório.</p>
          <div className="table-wrap">
            <table className="table findings-table">
              <thead><tr><th>Quando</th><th>Centro de custo</th><th>Item</th><th>Apontamento</th><th>Ação</th><th>Correção ou motivo</th><th /></tr></thead>
              <tbody>
                {data.reviews.map((r) => (
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
      )}

      {data.cost_centers.length > 0 && (
        <Card title="Templates corrigidos por centro de custo">
          <p className="muted small">O mesmo modelo de Excel que a área preencheu, já com as correções, para devolver ao gestor (reimportável).</p>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Centro de custo</th><th>OPEX</th><th>CAPEX</th></tr></thead>
              <tbody>
                {data.cost_centers.map((c) => (
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
      )}

      {fixing && <FixModal finding={fixing} projectTypes={data.project_types} onClose={() => setFixing(null)} onDone={(note) => done(`Corrigido: ${note}`)} />}
      {keeping && <KeepModal finding={keeping} onClose={() => setKeeping(null)} onDone={(note) => done(`Aviso mantido: ${note}`)} />}
    </>
  );
}
