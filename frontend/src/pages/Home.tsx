import { Link } from "react-router-dom";
import { api, type Account, type CostCenter, type Cycle, type DatasetVersion, type ImportBatch, type Page } from "../api";
import { useAuth } from "../auth";
import { Badge, Card, Empty, Loading, PageHeader, Stat, useLoad } from "../components/ui";
import { CYCLE_STATUS, DATASET_LABELS, IMPORT_STATUS, fmtDate, fmtDateTime, fmtInt } from "../labels";

function daysUntil(iso: string | null): string | undefined {
  if (!iso) return undefined;
  const diff = Math.ceil((new Date(`${iso}T23:59:59`).getTime() - Date.now()) / 86_400_000);
  return diff >= 0 ? `faltam ${diff} dia(s)` : `encerrado há ${-diff} dia(s)`;
}

export default function Home() {
  const { user, can } = useAuth();
  const canImport = can("CONTROLLER");
  const { data } = useLoad(async () => {
    const [cycles, ccs, accounts] = await Promise.all([
      api<Cycle[]>("/cycles"),
      api<CostCenter[]>("/cost-centers"),
      api<Account[]>("/accounts"),
    ]);
    const [imports, versions] = canImport
      ? await Promise.all([
          api<Page<ImportBatch>>("/imports?limit=5"),
          api<DatasetVersion[]>("/dataset-versions?current_only=true"),
        ])
      : [null, []];
    return { cycle: cycles[0] ?? null, ccs, accounts, imports, versions };
  }, [canImport]);

  if (!data) return <Loading />;
  const { cycle, ccs, accounts, imports, versions } = data;
  const actualScopes = versions.filter((v) => v.dataset_type === "ACTUAL");

  return (
    <>
      <PageHeader
        title={`Olá, ${user?.name.split(" ")[0]}`}
        subtitle={cycle ? `${cycle.name} · realizado de referência ${cycle.actual_reference_year}` : "Nenhum ciclo cadastrado"}
      />
      <div className="stats">
        <Stat
          label="Status do ciclo"
          value={cycle ? <Badge tone={CYCLE_STATUS[cycle.status]?.tone ?? "neutral"}>{CYCLE_STATUS[cycle.status]?.label}</Badge> : "—"}
        />
        <Stat label="Prazo OPEX" value={fmtDate(cycle?.opex_deadline ?? null)} hint={daysUntil(cycle?.opex_deadline ?? null)} />
        <Stat label="Prazo CAPEX" value={fmtDate(cycle?.capex_deadline ?? null)} hint={daysUntil(cycle?.capex_deadline ?? null)} />
        <Stat label="Centros de custo" value={fmtInt(ccs.length)} hint={canImport ? "cadastrados" : "sob sua gestão"} />
        <Stat label="Contas contábeis" value={fmtInt(accounts.length)} />
        {canImport && <Stat label="Bases de realizado" value={fmtInt(actualScopes.length)} hint="empresa × ano carregados" />}
      </div>

      {canImport && (
        <div className="grid-2">
          <Card title="Últimas importações" actions={<Link to="/importacoes" className="link">Ver todas</Link>}>
            {imports && imports.items.length ? (
              <table className="table">
                <tbody>
                  {imports.items.map((b) => (
                    <tr key={b.id}>
                      <td>
                        <Link to={`/importacoes/${b.id}`} className="link">
                          {b.file_name}
                        </Link>
                        <div className="muted small">{DATASET_LABELS[b.dataset_type ?? ""] ?? "Detectando…"}</div>
                      </td>
                      <td className="right">
                        <Badge tone={IMPORT_STATUS[b.status]?.tone ?? "neutral"}>{IMPORT_STATUS[b.status]?.label ?? b.status}</Badge>
                        <div className="muted small">{fmtDateTime(b.created_at)}</div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <Empty>
                Nenhum arquivo importado. <Link to="/importacoes" className="link">Importar agora</Link>
              </Empty>
            )}
          </Card>
          <Card title="Dados carregados (versão vigente)">
            {versions.length ? (
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
                  {versions.map((v) => (
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

      <Card title="Roteiro de implantação">
        <ol className="roadmap">
          <li className="done"><strong>Fase 1 — Fundação:</strong> cadastros, importação versionada, auditoria, parâmetros.</li>
          <li><strong>Fase 2 — OPEX:</strong> histórico 2025 → 2026 → 2027, preenchimento por pacote, justificativas, aprovação.</li>
          <li><strong>Fase 3 — CAPEX:</strong> projetos, itens, cronograma mensal e validação de consistência.</li>
          <li><strong>Fase 4 — Pessoal:</strong> quadro, movimentações, what-if de multiplicador, admissões e desligamentos.</li>
          <li><strong>Fase 5 — Consolidação:</strong> dashboard executivo, pontos de atenção, exportações e versões.</li>
        </ol>
      </Card>
    </>
  );
}
