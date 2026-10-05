import { useMemo, useState } from "react";
import { api, type Comparison, type ImportErrorItem } from "../api";
import { ERROR_LABELS, RECORD_TYPES, fmtInt, fmtMoney } from "../labels";
import { Alert, Badge, Card, Empty, Loading, SearchBox, useLoad } from "./ui";

/** Lista completa de inconsistências na própria tela (sem precisar baixar o Excel). */
export function ErrorsPanel({ batchId, code, onCode, onClose }: { batchId: string; code: string; onCode: (c: string) => void; onClose: () => void }) {
  const [q, setQ] = useState("");
  const [severity, setSeverity] = useState("");
  const { data, error } = useLoad(
    () => api<ImportErrorItem[]>(`/imports/${batchId}/errors?limit=5000${code ? `&code=${code}` : ""}`),
    [batchId, code],
  );
  const rows = useMemo(
    () =>
      (data ?? []).filter(
        (e) =>
          (!severity || e.severity === severity) &&
          (!q || [e.message, e.value, e.column, String(e.row_number)].some((f) => f?.toLowerCase().includes(q.toLowerCase()))),
      ),
    [data, q, severity],
  );
  const codes = useMemo(() => [...new Set((data ?? []).map((e) => e.code))], [data]);

  return (
    <Card
      title="Todas as inconsistências e avisos"
      actions={
        <div className="inline-controls">
          <SearchBox value={q} onChange={setQ} placeholder="Buscar mensagem, valor ou linha" />
          <select value={code} onChange={(e) => onCode(e.target.value)}>
            <option value="">Todos os tipos</option>
            {(code ? [code] : codes).map((c) => (
              <option key={c} value={c}>{ERROR_LABELS[c] ?? c}</option>
            ))}
          </select>
          <select value={severity} onChange={(e) => setSeverity(e.target.value)}>
            <option value="">Erros e avisos</option>
            <option value="ERROR">Só erros</option>
            <option value="WARNING">Só avisos</option>
          </select>
          <button className="btn btn-ghost btn-sm" onClick={onClose}>Fechar</button>
        </div>
      }
    >
      {error && <Alert>{error}</Alert>}
      {!data ? (
        <Loading />
      ) : rows.length === 0 ? (
        <Empty>Nenhuma ocorrência com esses filtros.</Empty>
      ) : (
        <>
          <p className="muted small">{fmtInt(rows.length)} ocorrência(s){data.length >= 5000 ? " (exibindo as 5.000 primeiras; o relatório Excel traz todas)" : ""}</p>
          <div className="table-wrap scroll-y">
            <table className="table table-compact">
              <thead>
                <tr>
                  <th>Linha</th>
                  <th>Aba</th>
                  <th>Situação</th>
                  <th>Tipo</th>
                  <th>Coluna</th>
                  <th>Mensagem</th>
                  <th>Valor</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((e, i) => (
                  <tr key={i}>
                    <td className="muted">{e.row_number ?? "—"}</td>
                    <td className="muted">{e.sheet ?? "—"}</td>
                    <td><Badge tone={e.severity === "ERROR" ? "bad" : "warn"}>{e.severity === "ERROR" ? "Erro" : "Aviso"}</Badge></td>
                    <td>{ERROR_LABELS[e.code] ?? e.code}</td>
                    <td>{e.column ?? "—"}</td>
                    <td className="wrap">{e.message}</td>
                    <td className="mono">{e.value ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Card>
  );
}

const ACTION_LABELS = { CREATE: "Novos", UPDATE: "Alterados", UNCHANGED: "Sem alteração" } as const;

/** O que a carga vai mudar em relação à base vigente. */
export function ComparisonCard({ comparison }: { comparison: Comparison }) {
  if (comparison.kind === "NONE") return null;
  return (
    <Card title="Comparação com a base vigente">
      {comparison.no_changes && (
        <Alert tone="warn">Nenhuma diferença em relação aos dados já carregados. Confirmar só criaria uma versão idêntica.</Alert>
      )}
      {comparison.kind === "FINANCIAL" && comparison.scopes && (
        <>
          <p className="muted small">
            Modo <strong>{comparison.mode === "REPLACE" ? "Substituir" : "Atualizar (mesclar)"}</strong>:{" "}
            {comparison.mode === "REPLACE"
              ? "a base da empresa no ano passa a ser exatamente o conteúdo deste arquivo."
              : "atualiza as combinações filial × CC × conta presentes no arquivo e mantém as demais."}
          </p>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Base</th>
                  <th className="right">Novos</th>
                  <th className="right">Alterados</th>
                  <th className="right">Iguais</th>
                  <th className="right">Fora do arquivo</th>
                  <th className="right">Total vigente</th>
                  <th className="right">Total após a carga</th>
                </tr>
              </thead>
              <tbody>
                {comparison.scopes.map((s) => (
                  <tr key={s.scope}>
                    <td>
                      Empresa {s.company} · {s.year}
                      <div className="muted small">{s.current_version ? `vigente: v${s.current_version}` : "primeira carga"}</div>
                    </td>
                    <td className="right">{fmtInt(s.new)}</td>
                    <td className="right">{fmtInt(s.changed)}</td>
                    <td className="right">{fmtInt(s.unchanged)}</td>
                    <td className="right">
                      {fmtInt(s.absent)}
                      {s.absent > 0 && (
                        <div className={`small ${s.absent_action === "REMOVE" ? "error-text" : "muted"}`}>
                          {s.absent_action === "REMOVE" ? "serão removidos" : "serão mantidos"}
                        </div>
                      )}
                    </td>
                    <td className="right">{fmtMoney(s.current_total)}</td>
                    <td className="right"><strong>{fmtMoney(s.after_total)}</strong></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {comparison.scopes.some((s) => s.absent_action === "REMOVE" && s.absent > 0) && (
            <Alert tone="bad">
              Atenção: no modo Substituir, combinações que não estão no arquivo deixam de valer. Exemplos:{" "}
              {comparison.scopes.flatMap((s) => s.absent_samples).slice(0, 5).join("; ")}
            </Alert>
          )}
        </>
      )}
      {comparison.kind === "MASTER" && comparison.by_type && (
        <table className="table">
          <thead>
            <tr>
              <th>Cadastro</th>
              {Object.values(ACTION_LABELS).map((l) => (
                <th key={l} className="right">{l}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Object.entries(comparison.by_type).map(([type, c]) => (
              <tr key={type}>
                <td>{RECORD_TYPES[type] ?? type}</td>
                <td className="right">{fmtInt(c.CREATE)}</td>
                <td className="right">{fmtInt(c.UPDATE)}</td>
                <td className="right">{fmtInt(c.UNCHANGED)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {(comparison.kind === "EMPLOYEES" || comparison.kind === "MACRO") && (
        <dl className="kv">
          <dt>Novos</dt><dd>{fmtInt(comparison.new ?? 0)}</dd>
          <dt>Alterados</dt><dd>{fmtInt(comparison.changed ?? 0)}</dd>
          <dt>Sem alteração</dt><dd>{fmtInt(comparison.unchanged ?? 0)}</dd>
        </dl>
      )}
    </Card>
  );
}
