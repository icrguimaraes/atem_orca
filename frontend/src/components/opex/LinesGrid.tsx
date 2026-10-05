import { useEffect, useState } from "react";
import { api, type OpexLine, type OpexOptions } from "../../api";
import { MONTHS, fmtMoney } from "../../labels";
import { Alert, Empty } from "../ui";
import { MoneyInput, parseMoney } from "./MoneyInput";

interface Props {
  submissionId: number;
  packageId: number;
  lines: OpexLine[];
  options: OpexOptions;
  companyId: number;
  editable: boolean;
  onChanged: () => void;
  showSupplier?: boolean;
}

/** Grade editável estilo planilha: cada alteração é salva ao sair do campo. */
export function LinesGrid({ submissionId, packageId, lines, options, companyId, editable, onChanged, showSupplier = true }: Props) {
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState<number | null>(null);
  const accounts = options.accounts.filter((a) => a.package_id === packageId);
  const branches = options.branches.filter((b) => b.company_id === companyId);
  const generic = lines.filter((l) => l.line_type === "GENERIC");

  async function patch(line: OpexLine, body: Record<string, unknown>) {
    setSaving(line.id);
    setError(null);
    try {
      await api(`/opex/lines/${line.id}`, { method: "PATCH", body: JSON.stringify(body) });
      onChanged();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(null);
    }
  }

  async function add() {
    if (!accounts.length) return;
    setError(null);
    try {
      await api(`/opex/submissions/${submissionId}/lines`, {
        method: "POST",
        body: JSON.stringify({ account_id: accounts[0].id, package_id: packageId, values: {} }),
      });
      onChanged();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function remove(line: OpexLine) {
    if (!window.confirm("Excluir esta linha?")) return;
    try {
      await api(`/opex/lines/${line.id}`, { method: "DELETE" });
      onChanged();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  function spread(line: OpexLine) {
    const raw = window.prompt("Valor anual a distribuir igualmente pelos 12 meses:", line.total !== "0.00" ? line.total.replace(".", ",") : "");
    if (raw === null) return;
    const total = parseMoney(raw);
    if (total === null || total < 0) return setError("Valor anual inválido");
    const month = Math.floor((total / 12) * 100) / 100;
    const values: Record<number, number> = {};
    MONTHS.forEach((_, i) => (values[i + 1] = month));
    values[12] = Math.round((total - month * 11) * 100) / 100;
    patch(line, { values });
  }

  return (
    <div className="stack">
      {error && <Alert>{error}</Alert>}
      {generic.length === 0 ? (
        <Empty>Nenhuma linha neste pacote.{editable && " Use “Adicionar linha” para começar."}</Empty>
      ) : (
        <div className="table-wrap grid-wrap">
          <table className="table grid-table">
            <thead>
              <tr>
                <th className="sticky-col">Conta / descrição</th>
                <th>Filial</th>
                {showSupplier && <th>Fornecedor</th>}
                {MONTHS.map((m) => (
                  <th key={m} className="right">{m}</th>
                ))}
                <th className="right">Total</th>
                {editable && <th />}
              </tr>
            </thead>
            <tbody>
              {generic.map((line) => {
                const acc = options.accounts.find((a) => a.id === line.account_id);
                return (
                  <tr key={line.id} className={saving === line.id ? "saving" : undefined}>
                    <td className="sticky-col">
                      <select value={line.account_id} disabled={!editable} onChange={(e) => patch(line, { account_id: Number(e.target.value), account_detail_id: null })}>
                        {accounts.map((a) => (
                          <option key={a.id} value={a.id}>{a.code} · {a.name}</option>
                        ))}
                      </select>
                      {acc && acc.details.length > 0 && (
                        <select value={line.account_detail_id ?? ""} disabled={!editable} onChange={(e) => patch(line, { account_detail_id: e.target.value ? Number(e.target.value) : null })}>
                          <option value="">Detalhamento…</option>
                          {acc.details.map((d) => (
                            <option key={d.id} value={d.id}>{d.name}</option>
                          ))}
                        </select>
                      )}
                      <TextCell value={line.description} placeholder="Descrição / contrato" disabled={!editable} onCommit={(v) => patch(line, { description: v })} />
                    </td>
                    <td>
                      <select value={line.branch_id ?? ""} disabled={!editable} onChange={(e) => patch(line, { branch_id: e.target.value ? Number(e.target.value) : null })}>
                        <option value="">—</option>
                        {branches.map((b) => (
                          <option key={b.id} value={b.id}>{b.code} · {b.name}</option>
                        ))}
                      </select>
                    </td>
                    {showSupplier && (
                      <td>
                        <TextCell value={line.supplier} placeholder="Fornecedor" disabled={!editable} onCommit={(v) => patch(line, { supplier: v })} />
                      </td>
                    )}
                    {MONTHS.map((m, i) => (
                      <td key={m} className="month-cell">
                        <MoneyInput
                          label={`${m}`}
                          value={line.values[String(i + 1)] ?? "0"}
                          disabled={!editable}
                          onCommit={(n) => patch(line, { values: { [i + 1]: n } })}
                          onPasteMany={(nums) => {
                            const values: Record<number, number> = {};
                            nums.slice(0, 12 - i).forEach((n, k) => (values[i + 1 + k] = n));
                            patch(line, { values });
                          }}
                        />
                      </td>
                    ))}
                    <td className="right nowrap"><strong>{fmtMoney(line.total)}</strong></td>
                    {editable && (
                      <td className="nowrap">
                        <button className="btn btn-ghost btn-sm" title="Distribuir valor anual pelos 12 meses" onClick={() => spread(line)}>÷12</button>
                        <button className="btn btn-ghost btn-sm danger" title="Excluir linha" onClick={() => remove(line)}>✕</button>
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {editable && (
        <div className="inline-controls">
          <button className="btn" onClick={add} disabled={!accounts.length}>+ Adicionar linha</button>
          <span className="muted small">Dica: copie 12 valores de uma linha do Excel e cole no mês de JAN — o sistema preenche os meses seguintes.</span>
        </div>
      )}
    </div>
  );
}

function TextCell({ value, placeholder, disabled, onCommit }: { value: string | null; placeholder: string; disabled: boolean; onCommit: (v: string | null) => void }) {
  const [text, setText] = useState(value ?? "");
  useEffect(() => setText(value ?? ""), [value]);
  return (
    <input
      className="text-cell"
      value={text}
      placeholder={placeholder}
      disabled={disabled}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => (text || null) !== (value || null) && onCommit(text.trim() || null)}
      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
    />
  );
}
