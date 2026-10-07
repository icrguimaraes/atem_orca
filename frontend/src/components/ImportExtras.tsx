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
  // realizado de template com a base já carregada: não entra (a tabela fica só para conferência)
  const skipped = new Set(comparison.kind === "FINANCIAL" && comparison.template ? comparison.skipped_scopes ?? [] : []);
  if (comparison.kind === "TEMPLATE")
    return (
      <>
        {comparison.budget && comparison.budget.length > 0 && (
          <Card title={comparison.module === "CAPEX" ? "CAPEX 2027 que será carregado" : "Orçamento 2027 que será carregado"}>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr><th>Centro de custo</th><th>Situação</th><th className="right">{comparison.module === "CAPEX" ? "Itens" : "Linhas"}</th><th className="right">Total 2027</th><th className="right">Substitui (de template anterior)</th></tr>
                </thead>
                <tbody>
                  {comparison.budget.map((b) => (
                    <tr key={`${b.company}-${b.cost_center}`}>
                      <td className="mono">{b.company} · {b.cost_center}</td>
                      <td>{b.editable ? <Badge tone="good">aceita carga</Badge> : <Badge tone="bad">bloqueado ({b.status})</Badge>}</td>
                      <td className="right">{fmtInt(b.lines)}</td>
                      <td className="right"><strong>{fmtMoney(b.total)}</strong></td>
                      <td className="right">{b.replaces_lines ? `${fmtInt(b.replaces_lines)} ${comparison.module === "CAPEX" ? "itens" : "linhas"} · ${fmtMoney(b.replaces_total)}` : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="muted small">
              {comparison.module === "CAPEX"
                ? "Solicitações cadastradas diretamente no sistema não são afetadas. Linhas de projeto com o mesmo tipo e justificativa viram uma solicitação com vários itens."
                : "Linhas lançadas diretamente no sistema não são afetadas."}
            </p>
          </Card>
        )}
        {comparison.catalog && (comparison.catalog.new > 0 || comparison.catalog.existing > 0) && (
          <Card title="Catálogo de ativos (aba LISTA ATIVOS)">
            <p>
              <strong>{fmtInt(comparison.catalog.new)}</strong> itens novos · {fmtInt(comparison.catalog.existing)} já cadastrados. O catálogo sugere a conta contábil a partir do item escolhido.
            </p>
          </Card>
        )}
        {comparison.actual && <ComparisonCard comparison={comparison.actual} />}
        {comparison.master && <ComparisonCard comparison={comparison.master} />}
      </>
    );
  return (
    <Card title="Comparação com a base vigente">
      {skipped.size > 0 && (
        <Alert tone="info">
          A base já tem realizado para {[...skipped].map((s) => s.replace("ACTUAL:", "").replace(":", " · empresa ")).join(", ")}: a aba
          “Realizado” deste template <strong>não</strong> será carregada e a base continua como está. A tabela abaixo é só para
          conferência (diferença entre o template e a base). Para atualizar o realizado, importe o arquivo pela opção “Realizado”.
        </Alert>
      )}
      {comparison.no_changes && skipped.size === 0 && (
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
                    <td className="right">
                      {skipped.has(s.scope) ? (
                        <><strong>{fmtMoney(s.current_total)}</strong><div className="muted small">não muda (ignorado)</div></>
                      ) : (
                        <strong>{fmtMoney(s.after_total)}</strong>
                      )}
                    </td>
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
      {comparison.kind === "MASTER" && comparison.template && (
        <p className="muted small">Template de gestor só cria cadastros novos; filiais, centros de custo e contas já existentes não são alterados.</p>
      )}
      {comparison.kind === "MASTER" && comparison.by_type && (
        <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Cadastro</th>
              {Object.values(ACTION_LABELS).map((l) => (
                <th key={l} className="right">{l === "Alterados" && comparison.template ? "Existentes (não alterados)" : l}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Object.entries(comparison.by_type).map(([type, c]) => (
              <tr key={type}>
                <td>{RECORD_TYPES[type] ?? type}</td>
                <td className="right">{fmtInt(c.CREATE)}</td>
                <td className="right">{fmtInt(c.UPDATE ?? c.IGNORED ?? 0)}</td>
                <td className="right">{fmtInt(c.UNCHANGED)}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
      {(comparison.kind === "EMPLOYEES" || comparison.kind === "MACRO") && (
        <dl className="kv">
          <dt>Novos</dt><dd>{fmtInt(comparison.new ?? 0)}</dd>
          <dt>Alterados</dt><dd>{fmtInt(comparison.changed ?? 0)}</dd>
          <dt>Sem alteração</dt><dd>{fmtInt(comparison.unchanged ?? 0)}</dd>
        </dl>
      )}
      {comparison.kind === "EMPLOYEES" && (comparison.movements?.length ?? 0) > 0 && (
        <div className="table-wrap" style={{ marginTop: 16 }}>
          <table className="table">
            <thead>
              <tr><th>Orçamento de pessoal do CC</th><th>Situação</th><th className="right">Ações do quadro</th><th className="right">Vagas</th></tr>
            </thead>
            <tbody>
              {comparison.movements!.map((m) => (
                <tr key={m.cost_center}>
                  <td>{m.name}<div className="muted small mono">{m.cost_center}</div></td>
                  <td>{m.editable ? <Badge tone="good">aceita carga</Badge> : <Badge tone="bad">bloqueado ({m.status})</Badge>}</td>
                  <td className="right">{fmtInt(m.actions)}</td>
                  <td className="right">{fmtInt(m.hires)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted small">Promoções, desligamentos e vagas substituem os que vieram de importação anterior; os lançados no sistema são mantidos.</p>
        </div>
      )}
    </Card>
  );
}
