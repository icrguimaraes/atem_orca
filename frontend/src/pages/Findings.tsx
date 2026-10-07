import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Finding, type Findings as FindingsData } from "../api";
import { FilterBar } from "../components/FilterBar";
import { Alert, Badge, Card, Empty, Loading, PageHeader, Stat, useLoad } from "../components/ui";
import { SUBMISSION_STATUS, fmtInt, fmtMoney, type Tone } from "../labels";

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

/** Divergências e erros de preenchimento dos orçamentos em aberto, item a item (o que cada tela de CC aponta). */
export default function Findings() {
  const { data, error } = useLoad(() => api<FindingsData>("/findings"));
  const [module, setModule] = useState("");
  const [severity, setSeverity] = useState("");
  const [kind, setKind] = useState("");
  const [cc, setCc] = useState("");
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

  return (
    <>
      <PageHeader
        title="Apontamentos"
        subtitle="Divergências e erros de preenchimento nos orçamentos em aberto, item a item. Críticos bloqueiam o envio; avisos ficam para a análise da Controladoria."
        actions={filtered ? <button type="button" className="btn" onClick={clear}>Limpar filtros</button> : undefined}
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
      {items.length === 0 ? (
        <Empty>Nenhum apontamento nos orçamentos em aberto da versão {data.version}.</Empty>
      ) : (
        <>
          <div className="stats">
            <Stat label="Apontamentos" value={fmtInt(shown.length)} hint={filtered ? `de ${fmtInt(items.length)} no total` : `versão ${data.version}`} />
            <Stat label="Críticos" value={fmtInt(critical)} tone={critical ? "bad" : "good"} hint="bloqueiam o envio" />
            <Stat label="Avisos" value={fmtInt(shown.length - critical)} tone={shown.length - critical ? "warn" : undefined} hint="para a análise" />
            <Stat label="Centros de custo" value={fmtInt(new Set(shown.map((i) => i.cost_center_id)).size)} hint="com apontamento" />
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
                    <tr><th>Gravidade</th><th>Centro de custo</th><th>Item</th><th>Apontamento</th><th className="right">Valor</th></tr>
                  </thead>
                  <tbody>
                    {shown.map((i, n) => (
                      <tr key={n}>
                        <td><Badge tone={SEVERITY[i.severity].tone}>{SEVERITY[i.severity].label}</Badge></td>
                        <td className="col-cc">
                          {i.link ? <Link className="link" to={i.link}>{i.cost_center}</Link> : i.cost_center}
                          <div className="muted small">{i.module_label} · {SUBMISSION_STATUS[i.status]?.label ?? i.status}</div>
                        </td>
                        <td className="col-item">{i.subject}{i.detail && <div className="muted small">{i.detail}</div>}</td>
                        <td className="col-msg"><strong>{i.kind_label}</strong><div>{i.message}</div></td>
                        <td className="right nowrap">{i.amount === null ? "—" : fmtMoney(i.amount)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}
    </>
  );
}
