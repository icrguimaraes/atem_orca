import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, download, type Comparison, type Preview } from "../api";
import { ComparisonCard, ErrorsPanel } from "../components/ImportExtras";
import { Alert, Badge, Card, Empty, Loading, PageHeader, Stat, useLoad } from "../components/ui";
import {
  DATASET_LABELS,
  ERROR_LABELS,
  FIELD_LABELS,
  IMPORT_STATUS,
  LAYOUT_LABELS,
  MONTHS,
  RECORD_TYPES,
  ROW_STATUS,
  fmtDateTime,
  fmtInt,
  fmtCompact,
  fmtMoney,
} from "../labels";

const ACTIVE = new Set(["UPLOADED", "VALIDATING", "CONFIRMED", "PROCESSING"]);
const HIDDEN_KEYS = new Set(["company_id", "branch_id", "cost_center_id", "account_id", "_action", "_duplicate"]);

const LOAD_GROUPS: Record<string, string> = { master: "Cadastros", actual: "Realizado", budget: "Orçamento", capex: "CAPEX", catalog: "Catálogo", movements: "Pessoal" };

/** Resultado da carga vem aninhado por parte (cadastros, realizado, orçamento…): achata em linhas legíveis. */
function flattenLoad(load: Record<string, unknown>, prefix = ""): [string, string | number][] {
  const out: [string, string | number][] = [];
  for (const [k, v] of Object.entries(load)) {
    if (k === "versions") continue;
    const label = prefix ? `${prefix} · ${LOAD_LABELS[k] ?? k}` : (LOAD_GROUPS[k] ?? LOAD_LABELS[k] ?? k);
    if (v && typeof v === "object" && !Array.isArray(v)) out.push(...flattenLoad(v as Record<string, unknown>, LOAD_GROUPS[k] ?? LOAD_LABELS[k] ?? k));
    else if (typeof v === "number" || typeof v === "string") out.push([label, v]);
  }
  return out;
}

const LOAD_LABELS: Record<string, string> = {
  lines_created: "Linhas criadas",
  lines_replaced: "Linhas substituídas (de template anterior)",
  cost_centers: "Centros de custo carregados",
  cost_centers_locked: "Centros de custo bloqueados (não carregados)",
  trips: "Viagens",
  items_created: "Itens criados",
  requests_created: "Solicitações criadas",
  requests_replaced: "Solicitações substituídas",
  updates_ignored: "Existentes não alterados (template)",
  skipped: "Observação",
  branches_created: "Filiais criadas",
  branches_updated: "Filiais atualizadas",
  cost_centers_created: "Centros de custo criados",
  cost_centers_updated: "Centros de custo atualizados",
  accounts_created: "Contas criadas",
  accounts_updated: "Contas atualizadas",
  packages_created: "Pacotes criados",
  account_details_created: "Detalhamentos criados",
  entries: "Lançamentos gravados",
  employees_created: "Colaboradores criados",
  employees_updated: "Colaboradores atualizados",
  employees_deactivated: "Colaboradores inativados",
  vacancies_pending: "Vagas (admissões planejadas)",
  assumptions: "Premissas gravadas",
};

const FIELD_ORDER = Object.keys(FIELD_LABELS);

function renderCell(key: string, v: unknown): string {
  if (key === "values" && v && typeof v === "object") {
    const entries = Object.entries(v as Record<string, string>);
    return entries.length
      ? entries.map(([m, x]) => `${MONTHS[Number(m) - 1]} ${Number(x).toLocaleString("pt-BR")}`).join(" · ")
      : "—";
  }
  if (key === "action_month" && typeof v === "number") return MONTHS[v - 1] ?? String(v);
  return renderValue(v);
}

function renderValue(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "object") {
    const entries = Object.entries(v as Record<string, unknown>);
    return entries.length ? entries.map(([k, x]) => `${k}: ${x}`).join(" · ") : "—";
  }
  return String(v);
}

export default function ImportDetail() {
  const { id } = useParams();
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [force, setForce] = useState(false);
  const [errorsOpen, setErrorsOpen] = useState(false);
  const [errorCode, setErrorCode] = useState("");
  const { data, error, reload } = useLoad(
    () => api<Preview>(`/imports/${id}/preview?limit=200${filter ? `&status=${filter}` : ""}`),
    [id, filter],
  );
  const batch = data?.batch;

  useEffect(() => {
    if (!batch || !ACTIVE.has(batch.status)) return;
    const t = setInterval(reload, 2000);
    return () => clearInterval(t);
  }, [batch, reload]);

  async function act(action: "confirm" | "reject") {
    if (action === "reject" && !window.confirm("Descartar esta importação? Nenhum dado será gravado.")) return;
    setBusy(true);
    setActionError(null);
    try {
      await api(`/imports/${id}/${action}${action === "confirm" && force ? "?force=true" : ""}`, { method: "POST" });
      reload();
    } catch (err) {
      setActionError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (error) return <Alert>{error}</Alert>;
  if (!data || !batch) return <Loading />;

  const status = IMPORT_STATUS[batch.status] ?? { label: batch.status, tone: "neutral" as const };
  const summary = batch.summary ?? {};
  const meta = (summary.meta ?? {}) as Record<string, unknown>;
  const load = (summary.load ?? null) as Record<string, unknown> | null;
  const comparison = (summary.comparison ?? null) as Comparison | null;
  const blockers = [
    summary.same_file_imported_in ? `Este mesmo arquivo já foi importado (importação #${summary.same_file_imported_in}).` : null,
    comparison?.no_changes ? "O arquivo não traz alterações em relação aos dados vigentes." : null,
  ].filter(Boolean) as string[];
  const hasIssues = batch.error_rows > 0 || batch.duplicate_rows > 0 || batch.warning_rows > 0 || data.errors_by_code.length > 0;
  const openErrors = (code = "") => {
    setErrorCode(code);
    setErrorsOpen(true);
    setTimeout(() => document.getElementById("errors-panel")?.scrollIntoView({ behavior: "smooth" }), 50);
  };
  const present = new Set(data.rows.flatMap((r) => Object.keys(r.data)));
  const columns = [
    ...FIELD_ORDER.filter((k) => present.has(k)),
    ...[...present].filter((k) => !FIELD_LABELS[k] && !HIDDEN_KEYS.has(k) && !k.startsWith("_")),
  ].filter((k) => data.rows.some((r) => r.data[k] !== null && r.data[k] !== undefined && r.data[k] !== ""));

  return (
    <>
      <PageHeader
        title={batch.file_name}
        subtitle={`${DATASET_LABELS[batch.dataset_type ?? ""] ?? "Tipo em detecção"} · ${LAYOUT_LABELS[batch.layout ?? ""] ?? ""} · enviado em ${fmtDateTime(batch.created_at)}`}
        actions={
          <>
            <Link to="/importacoes" className="btn btn-ghost">
              Voltar
            </Link>
            {(hasIssues || batch.status === "FAILED") && (
              <>
                <button className="btn" onClick={() => openErrors()}>
                  Ver inconsistências
                </button>
                <button className="btn btn-ghost" onClick={() => download(`/imports/${id}/errors.xlsx`, `inconsistencias_${id}.xlsx`)}>
                  Baixar Excel
                </button>
              </>
            )}
            {["VALIDATED", "FAILED", "UPLOADED"].includes(batch.status) && (
              <button className="btn btn-ghost" disabled={busy} onClick={() => act("reject")}>
                Descartar
              </button>
            )}
            {batch.status === "VALIDATED" && (
              <button className="btn btn-primary" disabled={busy || (blockers.length > 0 && !force)} onClick={() => act("confirm")}>
                Confirmar importação de {fmtInt(batch.valid_rows)} registros
              </button>
            )}
          </>
        }
      />

      <div className="status-line">
        <Badge tone={status.tone}>{status.label}</Badge>
        {ACTIVE.has(batch.status) && <span className="muted small">atualizando automaticamente…</span>}
      </div>
      {batch.status === "VALIDATED" && blockers.length > 0 && (
        <Alert tone="warn">
          <strong>Confirmação bloqueada para evitar carga repetida.</strong> {blockers.join(" ")}
          <label className="check" style={{ marginTop: 8 }}>
            <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
            Entendi, quero importar mesmo assim (gera nova versão; os valores não são somados em dobro)
          </label>
        </Alert>
      )}
      {batch.error_message && <Alert>{batch.error_message}</Alert>}
      {actionError && <Alert>{actionError}</Alert>}

      <div className="stats">
        <Stat label="Registros encontrados" value={fmtInt(batch.total_rows)} />
        <Stat label="Válidos" value={fmtInt(batch.valid_rows)} tone="good" />
        <Stat label="Com avisos" value={fmtInt(batch.warning_rows)} tone="warn" hint="entram na carga" />
        <Stat label="Inconsistentes" value={fmtInt(batch.error_rows)} tone="bad" hint="não entram na carga" />
        <Stat label="Duplicados" value={fmtInt(batch.duplicate_rows)} tone="bad" hint="não entram na carga" />
        {summary.total_amount !== undefined && <Stat label="Valor válido" value={fmtCompact(summary.total_amount)} hint={fmtMoney(summary.total_amount)} />}
      </div>

      {(meta.year !== undefined || summary.cost_centers !== undefined || summary.actions) && (
        <Card title="Resumo do conteúdo">
          <dl className="kv">
            {meta.year !== undefined && (<><dt>Ano</dt><dd>{String(meta.year)}</dd></>)}
            {Array.isArray(meta.months) && (<><dt>Meses</dt><dd>{(meta.months as number[]).join(", ")}</dd></>)}
            {summary.cost_centers !== undefined && (<><dt>Centros de custo</dt><dd>{fmtInt(summary.cost_centers)}</dd></>)}
            {summary.accounts !== undefined && (<><dt>Contas</dt><dd>{fmtInt(summary.accounts)}</dd></>)}
            {summary.actions && (<><dt>Cadastros</dt><dd>{renderValue({ novos: summary.actions.CREATE ?? 0, existentes: summary.actions.UPDATE ?? 0 })}</dd></>)}
            {(summary.new_cost_centers?.length > 0 || summary.new_accounts?.length > 0) && (
              <>
                <dt>Cadastros novos</dt>
                <dd>
                  {[
                    summary.new_cost_centers?.length ? `${fmtInt(summary.new_cost_centers.length)} centro(s) de custo` : null,
                    summary.new_accounts?.length ? `${fmtInt(summary.new_accounts.length)} conta(s)` : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}{" "}
                  <span className="muted small">(criados na confirmação, com o nome do arquivo)</span>
                  <div className="muted small mono" style={{ fontWeight: 400, marginTop: 4 }}>
                    {[...(summary.new_cost_centers ?? []), ...(summary.new_accounts ?? [])]
                      .map((x: { code: string; name: string | null }) => (x.name ? `${x.code} ${x.name}` : x.code))
                      .join(" · ")}
                  </div>
                </dd>
              </>
            )}
            {summary.record_types && (<><dt>Tipos de registro</dt><dd>
                  {Object.entries(summary.record_types as Record<string, number>)
                    .map(([k, n]) => `${RECORD_TYPES[k] ?? k}: ${fmtInt(n)}`)
                    .join(" · ")}
                </dd></>)}
          </dl>
        </Card>
      )}

      {load && (
        <Card title="Resultado da carga">
          <dl className="kv">
            {flattenLoad(load).map(([k, v]) => (
              <div key={k} className="kv-row">
                <dt>{k}</dt>
                <dd>{typeof v === "number" ? fmtInt(v) : String(v)}</dd>
              </div>
            ))}
            {Array.isArray(load.versions) &&
              (load.versions as { scope: string; version: number; entries: number }[]).map((v) => (
                <div key={v.scope} className="kv-row">
                  <dt>Versão gerada</dt>
                  <dd className="mono">
                    {v.scope} → v{v.version} ({fmtInt(v.entries)} lançamentos)
                  </dd>
                </div>
              ))}
          </dl>
        </Card>
      )}

      {comparison && batch.status !== "FAILED" && <ComparisonCard comparison={comparison} />}

      {data.errors_by_code.length > 0 && (
        <Card
          title="Inconsistências e avisos"
          actions={
            <button className="btn btn-sm" onClick={() => openErrors()}>
              Ver todas as ocorrências
            </button>
          }
        >
          <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Situação</th>
                <th>Tipo de ocorrência</th>
                <th className="right">Ocorrências</th>
                <th>Exemplo</th>
              </tr>
            </thead>
            <tbody>
              {data.errors_by_code.map((e) => (
                <tr key={`${e.code}-${e.severity}`} className="clickable" onClick={() => openErrors(e.code)} title="Ver ocorrências">
                  <td>
                    <Badge tone={e.severity === "ERROR" ? "bad" : "warn"}>{e.severity === "ERROR" ? "Erro" : "Aviso"}</Badge>
                  </td>
                  <td>
                    {ERROR_LABELS[e.code] ?? e.code}
                    <div className="muted small mono">{e.code}</div>
                  </td>
                  <td className="right">{fmtInt(e.count)}</td>
                  <td>{e.example}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        </Card>
      )}

      {errorsOpen && id && (
        <div id="errors-panel">
          <ErrorsPanel batchId={id} code={errorCode} onCode={setErrorCode} onClose={() => setErrorsOpen(false)} />
        </div>
      )}

      <Card
        title="Prévia dos registros"
        actions={
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="">Todos</option>
            {Object.entries(ROW_STATUS).map(([k, v]) => (
              <option key={k} value={k}>
                {v.label}
              </option>
            ))}
          </select>
        }
      >
        {data.rows.length === 0 ? (
          <Empty>{ACTIVE.has(batch.status) ? "Processando…" : "Nenhum registro para exibir."}</Empty>
        ) : (
          <div className="table-wrap">
            <table className="table table-compact">
              <thead>
                <tr>
                  <th>Linha</th>
                  <th>Situação</th>
                  {columns.map((c) => (
                    <th key={c}>{FIELD_LABELS[c] ?? c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.rows.map((r) => (
                  <tr key={`${r.sheet}-${r.row_number}-${r.record_type}`}>
                    <td className="muted">{r.row_number}</td>
                    <td>
                      <Badge tone={ROW_STATUS[r.status]?.tone ?? "neutral"}>{ROW_STATUS[r.status]?.label ?? r.status}</Badge>
                    </td>
                    {columns.map((c) => (
                      <td key={c}>{renderCell(c, r.data[c])}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="muted small">Exibindo até 200 linhas. O relatório de erros traz todas as inconsistências.</p>
      </Card>
    </>
  );
}
