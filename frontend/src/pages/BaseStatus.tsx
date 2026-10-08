import { Link } from "react-router-dom";
import { api, type DatasetVersion, type ImportBatch, type Inventory, type Page, type QualityCheck } from "../api";
import { Badge, Card, Empty, Loading, PageHeader, Stat, useLoad } from "../components/ui";
import { DATASET_LABELS, IMPORT_STATUS, fmtDateTime, fmtInt, fmtMoney } from "../labels";

/* Página de consulta da Controladoria (saiu do fim do Painel em 08/10/2026): cadastros, contratos do quadro, qualidade
   da base, últimas importações e bases vigentes. */

const SEVERITY = {
  ERROR: { tone: "bad", label: "Erro" },
  WARNING: { tone: "warn", label: "Atenção" },
  INFO: { tone: "info", label: "Info" },
  OK: { tone: "good", label: "OK" },
} as const;

export default function BaseStatus() {
  const inventory = useLoad(() => api<Inventory>("/dashboard/inventory"));
  const admin = useLoad(async () => {
    const [quality, imports, versions] = await Promise.all([
      api<{ checks: QualityCheck[]; issues: number }>("/dashboard/data-quality"),
      api<Page<ImportBatch>>("/imports?limit=5"),
      api<DatasetVersion[]>("/dataset-versions?current_only=true"),
    ]);
    return { quality, imports, versions };
  });
  const inv = inventory.data;
  if (!inv && !admin.data) return <Loading />;

  return (
    <>
      <PageHeader title="Situação da base" subtitle="Consulta: cadastros, quadro de pessoal, qualidade da base, importações e versões vigentes." />
      <div className="section-stack">
      {inv && (
        <Card title="Cadastros">
          <div className="stats">
            <Stat label="Centros de custo ativos" value={fmtInt(inv.master.cost_centers)} hint={inv.master.cost_centers_without_user ? `${fmtInt(inv.master.cost_centers_without_user)} sem usuário gestor` : "todos com gestor"} />
            {Object.entries(inv.master.accounts_by_nature).map(([n, c]) => (
              <Stat key={n} label={`Contas ${n}`} value={fmtInt(c)} />
            ))}
            <Stat label="Pacotes GMD" value={fmtInt(inv.master.packages.length)} hint={`${inv.master.packages.filter((p) => p.package_type === 1).length} com validação obrigatória`} />
          </div>
        </Card>
      )}

      {inv && inv.personnel.by_contract.length > 0 && (
            <Card title="Por tipo de contrato">
              <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr><th>Contrato</th><th className="right">Pessoas</th><th className="right">Folha mensal</th><th className="right">Multiplicador</th></tr>
                </thead>
                <tbody>
                  {inv.personnel.by_contract.map((c) => (
                    <tr key={c.contract}>
                      <td>{c.contract}</td>
                      <td className="right">{fmtInt(c.headcount)}</td>
                      <td className="right">{fmtMoney(c.payroll)}</td>
                      <td className="right">{Number(c.multiplier).toLocaleString("pt-BR")}×</td>
                    </tr>
                  ))}
                </tbody>
              </table></div>
            </Card>
      )}

      {admin.data && (
        <Card
          title="Qualidade da base"
          actions={admin.data.quality.issues ? <Badge tone="warn">{admin.data.quality.issues} ponto(s)</Badge> : <Badge tone="good">Tudo certo</Badge>}
        >
          <ul className="checks-list">
            {admin.data.quality.checks.map((c) => (
              <li key={c.code}>
                <Badge tone={SEVERITY[c.severity].tone}>{SEVERITY[c.severity].label}</Badge>
                <div>
                  <div>
                    {c.title}
                    {c.count > 1 && c.severity !== "OK" && <strong> · {fmtInt(c.count)}</strong>}
                  </div>
                  {c.severity !== "OK" && c.detail && <div className="muted small">{c.detail}</div>}
                  {c.severity !== "OK" && c.samples.length > 0 && (
                    <div className="muted small mono">{c.samples.slice(0, 6).join(" · ")}{c.samples.length > 6 ? " …" : ""}</div>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {admin.data && (
        <div className="grid-2">
          <Card title="Últimas importações" actions={<Link to="/importacoes" className="link">Ver todas</Link>}>
            {admin.data.imports.items.length ? (
              <div className="table-wrap">
              <table className="table">
                <tbody>
                  {admin.data.imports.items.map((b) => (
                    <tr key={b.id}>
                      <td>
                        <Link to={`/importacoes/${b.id}`} className="link">{b.file_name}</Link>
                        <div className="muted small">{DATASET_LABELS[b.dataset_type ?? ""] ?? "Detectando…"}</div>
                      </td>
                      <td className="right">
                        <Badge tone={IMPORT_STATUS[b.status]?.tone ?? "neutral"}>{IMPORT_STATUS[b.status]?.label ?? b.status}</Badge>
                        <div className="muted small">{fmtDateTime(b.created_at)}</div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table></div>
            ) : (
              <Empty>Nenhum arquivo importado. <Link to="/importacoes" className="link">Importar agora</Link></Empty>
            )}
          </Card>
          <Card title="Bases vigentes">
            {admin.data.versions.length ? (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Base</th>
                      <th>Escopo</th>
                      <th className="right">Versão</th>
                      <th className="right">Registros</th>
                    </tr>
                  </thead>
                  <tbody>
                    {admin.data.versions.map((v) => (
                      <tr key={v.id}>
                        <td>{DATASET_LABELS[v.dataset_type] ?? v.dataset_type}</td>
                        <td className="mono small">{v.scope_key}</td>
                        <td className="right">v{v.version_number}</td>
                        <td className="right">{fmtInt(v.row_count)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty>Nenhuma base carregada ainda.</Empty>
            )}
          </Card>
        </div>
      )}
      </div>
    </>
  );
}
