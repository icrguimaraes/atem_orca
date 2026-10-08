import { Fragment, useEffect, useState } from "react";
import { api, type OpexAccountRow, type OpexAccounts } from "../../api";
import { FLAG_LABELS, MONTHS, fmtMoney, fmtPct } from "../../labels";
import { Sparkline } from "../charts";
import { Alert, Badge, Empty } from "../ui";

function Monthly({ submissionId, accountId, years }: { submissionId: number; accountId: number; years: { prev: number; ref: number; target: number } }) {
  const [data, setData] = useState<Record<string, Record<string, string>> | null>(null);
  useEffect(() => {
    api<Record<string, Record<string, string>>>(`/opex/submissions/${submissionId}/accounts/${accountId}/monthly`).then(setData);
  }, [submissionId, accountId]);
  if (!data) return <div className="muted small">Carregando…</div>;
  const series: [string, string][] = [
    [`${years.prev} realizado`, "prev_actual"],
    [`${years.ref} realizado`, "ref_actual"],
    [`${years.ref} orçado`, "ref_budget"],
    [`${years.target} proposto`, "proposed"],
  ];
  return (
    <div className="table-wrap">
    <table className="table table-compact monthly">
      <thead>
        <tr><th />{MONTHS.map((m) => <th key={m} className="right">{m}</th>)}</tr>
      </thead>
      <tbody>
        {series.map(([label, key]) => (
          <tr key={key}>
            <td className="nowrap">{label}</td>
            {MONTHS.map((_, i) => {
              const v = Number(data[key][String(i + 1)]);
              return <td key={i} className="right">{v ? v.toLocaleString("pt-BR", { maximumFractionDigits: 0 }) : "—"}</td>;
            })}
          </tr>
        ))}
      </tbody>
    </table></div>
  );
}

function Justification({ row, submissionId, editable, onSaved }: { row: OpexAccountRow; submissionId: number; editable: boolean; onSaved: () => void }) {
  const [text, setText] = useState(row.justification ?? "");
  const [state, setState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  useEffect(() => setText(row.justification ?? ""), [row.justification]);
  if (!editable)
    return row.justification ? <div className="small">{row.justification}</div> : row.needs_justification ? <span className="error-text">sem justificativa</span> : null;
  async function save(value: string = text) {
    if ((value.trim() || null) === (row.justification || null)) return;
    setState("saving");
    try {
      await api(`/opex/submissions/${submissionId}/justifications/${row.account_id}`, { method: "PUT", body: JSON.stringify({ text: value }) });
      setState("saved");
      onSaved();
    } catch {
      setState("error");
    }
  }
  return (
    <div>
      <textarea
        className={`justification ${row.needs_justification && !text.trim() ? "required" : ""}`}
        rows={2}
        value={text}
        placeholder={row.needs_justification ? "Obrigatória: explique o valor e a variação" : "Opcional"}
        onChange={(e) => setText(e.target.value)}
        onBlur={() => save()}
      />
      {row.needs_justification && !text.trim() && Number(row.proposed) === 0 && (
        <button
          type="button"
          className="btn-link link small"
          onClick={() => { const t = "Conta não será orçada em 2027."; setText(t); void save(t); }}
        >
          Não vou orçar esta conta
        </button>
      )}
      {state === "saving" && <span className="muted small">salvando…</span>}
      {state === "error" && <span className="error-text">não foi possível salvar</span>}
    </div>
  );
}

export function AccountsTab({ submissionId, data, years, editable, onChanged, onGoToPackage }: {
  submissionId: number; data: OpexAccounts; years: { prev: number; ref: number; target: number }; editable: boolean;
  onGoToPackage: (packageId: number, accountId?: number) => void;
  onChanged: () => void; 
}) {
  const [open, setOpen] = useState<number | null>(null);
  const [onlyAlerts, setOnlyAlerts] = useState(false);
  const showBudget = Number(data.totals.ref_budget ?? 0) !== 0; // sem orçado 2026 carregado, a coluna só ocupa espaço
  const [error, setError] = useState<string | null>(null);
  const rows = data.accounts.filter((r) => !onlyAlerts || r.flags.length > 0);
  const closed = data.closed_period ? MONTHS[data.closed_period - 1] : null;

  async function applyAverage(row: OpexAccountRow) {
    const total = Number(row.ref_annualized);
    const month = Math.floor((total / 12) * 100) / 100;
    const values: Record<number, number> = {};
    for (let m = 1; m <= 11; m++) values[m] = month;
    values[12] = Math.round((total - month * 11) * 100) / 100;
    setError(null);
    try {
      await api(`/opex/submissions/${submissionId}/lines`, {
        method: "POST",
        body: JSON.stringify({ account_id: row.account_id, package_id: row.package_id, values, description: `Base: média ${years.ref} anualizada` }),
      });
      onChanged();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <div className="stack">
      <div className="inline-controls">
        <label className="check">
          <input type="checkbox" checked={onlyAlerts} onChange={(e) => setOnlyAlerts(e.target.checked)} />
          Só contas com alerta
        </label>
        <span className="muted small">
          Referência para a variação: {years.ref} anualizado ({closed ? `realizado até ${closed} × 12 / ${data.closed_period}` : "sem realizado"}); na falta, orçado {years.ref} ou realizado {years.prev}.
        </span>
      </div>
      {error && <Alert>{error}</Alert>}
      {rows.length === 0 ? (
        <Empty>{data.accounts.length ? "Nenhuma conta com alerta." : "Sem histórico nem orçamento para este centro de custo. Comece pela aba Preencher por pacote."}</Empty>
      ) : (
        <div className="table-wrap">
          <table className="table accounts-table">
            <thead>
              <tr>
                <th>Conta</th>
                <th className="right">{years.prev} R</th>
                <th className="col-spark">{years.ref} mês a mês</th>
                <th className="right">{years.ref} R{closed ? ` (até ${closed})` : ""}</th>
                <th className="right">{years.ref} anualizado</th>
                {showBudget && <th className="right">{years.ref} orçado</th>}
                <th className="right">{years.target} proposto</th>
                <th className="right">Var.</th>
                <th>Justificativa</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <Fragment key={r.account_id}>
                  <tr className={r.needs_justification && !r.justification ? "row-alert" : undefined}>
                    <td>
                      <div>{r.name}</div>
                      <div className="muted small mono">{r.code} · {r.package ?? "sem pacote"}</div>
                      <div className="row-tools">
                        <button className="btn btn-ghost btn-sm" onClick={() => setOpen(open === r.account_id ? null : r.account_id)}>
                          {open === r.account_id ? "Ocultar mensal" : "Mensal"}
                        </button>
                        {editable && r.package_id && (
                          <button className="btn btn-ghost btn-sm" title="Abre o pacote e cria uma linha nesta conta" onClick={() => onGoToPackage(r.package_id!, r.account_id)}>Lançar</button>
                        )}
                        {editable && Number(r.proposed) === 0 && Number(r.ref_annualized) > 0 && r.package_id && (
                          <button className="btn btn-ghost btn-sm" title={`Cria uma linha com ${years.ref} anualizado dividido em 12`} onClick={() => applyAverage(r)}>
                            Usar média {years.ref}
                          </button>
                        )}
                      </div>
                      {r.flags.map((f) => (
                        <Badge key={f} tone={FLAG_LABELS[f]?.tone ?? "neutral"}>{FLAG_LABELS[f]?.label ?? f}</Badge>
                      ))}
                    </td>
                    <td className="right">{fmtMoney(r.prev_actual)}</td>
                    <td className="col-spark">{r.ref_monthly ? <Sparkline values={r.ref_monthly.map(Number).slice(0, data.closed_period ?? 12)} /> : <span className="muted small">—</span>}</td>
                    <td className="right">{fmtMoney(r.ref_actual_ytd)}</td>
                    <td className="right">{fmtMoney(r.ref_annualized)}</td>
                    {showBudget && <td className="right">{fmtMoney(r.ref_budget)}</td>}
                    <td className="right"><strong>{fmtMoney(r.proposed)}</strong></td>
                    <td className="right nowrap">{fmtPct(r.variation_pct)}</td>
                    <td className="just-cell">
                      <Justification row={r} submissionId={submissionId} editable={editable} onSaved={onChanged} />
                    </td>
                  </tr>
                  {open === r.account_id && (
                    <tr className="sub-row">
                      <td colSpan={showBudget ? 9 : 8}><Monthly submissionId={submissionId} accountId={r.account_id} years={years} /></td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <td><strong>Total</strong></td>
                {(["prev_actual"] as const).map((k) => (
                  <td key={k} className="right"><strong>{fmtMoney(data.totals[k] ?? 0)}</strong></td>
                ))}
                <td className="col-spark" />
                {(showBudget ? (["ref_actual_ytd", "ref_annualized", "ref_budget", "proposed"] as const) : (["ref_actual_ytd", "ref_annualized", "proposed"] as const)).map((k) => (
                  <td key={k} className="right"><strong>{fmtMoney(data.totals[k] ?? 0)}</strong></td>
                ))}
                <td className="right">
                  {Number(data.totals.ref_annualized) ? fmtPct(String(Number(data.totals.proposed) / Number(data.totals.ref_annualized) - 1)) : "—"}
                </td>
                <td />
              </tr>
            </tfoot>
          </table>
        </div>
      )}
    </div>
  );
}
