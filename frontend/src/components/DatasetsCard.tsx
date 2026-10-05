import { useState } from "react";
import { api, type DatasetInfo } from "../api";
import { useAuth } from "../auth";
import { DATASET_LABELS, fmtDateTime, fmtInt } from "../labels";
import { Alert, Card, Empty, Loading, Modal, useLoad } from "./ui";

function scopeLabel(d: DatasetInfo): string {
  const parts = d.scope_key.split(":");
  if (d.dataset_type === "ACTUAL") return `Empresa ${parts[2]} · ${parts[1]}`;
  if (d.dataset_type === "REFERENCE_BUDGET") return `Empresa ${parts[3]} · ${parts[2]} (${parts[1]})`;
  if (d.dataset_type === "EMPLOYEES") return `Empresa(s) ${parts[1]}`;
  return "Geral";
}

interface Target { dataset_type: string; scope_key?: string; label: string; rows: number }

export function DatasetsCard({ refreshKey, onChanged }: { refreshKey: number; onChanged: () => void }) {
  const { can } = useAuth();
  const isAdmin = can();
  const { data, error, reload } = useLoad(() => api<DatasetInfo[]>("/datasets"), [refreshKey]);
  const [target, setTarget] = useState<Target | null>(null);
  const [word, setWord] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "good" | "bad"; text: string } | null>(null);

  async function remove() {
    if (!target) return;
    setBusy(true);
    const params = new URLSearchParams({ dataset_type: target.dataset_type, confirm: word });
    if (target.scope_key) params.set("scope_key", target.scope_key);
    if (reason) params.set("reason", reason);
    try {
      const r = await api<{ versions_deleted: number; current_rows_deleted: number; employees_deleted: number }>(
        `/datasets?${params}`,
        { method: "DELETE" },
      );
      setMsg({
        tone: "good",
        text: `${target.label}: base excluída (${fmtInt(r.versions_deleted)} versão(ões), ${fmtInt(r.current_rows_deleted)} registro(s) vigentes${r.employees_deleted ? `, ${fmtInt(r.employees_deleted)} colaborador(es)` : ""}). Já pode reimportar.`,
      });
      setTarget(null);
      reload();
      onChanged();
    } catch (err) {
      setMsg({ tone: "bad", text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  }

  const open = (t: Target) => {
    setTarget(t);
    setWord("");
    setReason("");
    setMsg(null);
  };
  const types = [...new Set((data ?? []).filter((d) => d.deletable).map((d) => d.dataset_type))];

  return (
    <Card
      title="Bases carregadas"
      actions={
        isAdmin &&
        types.length > 0 && (
          <select
            value=""
            onChange={(e) => {
              const t = e.target.value;
              const rows = (data ?? []).filter((d) => d.dataset_type === t).reduce((s, d) => s + d.rows, 0);
              if (t) open({ dataset_type: t, label: `Todas as bases de ${DATASET_LABELS[t] ?? t}`, rows });
            }}
          >
            <option value="">Excluir todas de um tipo…</option>
            {types.map((t) => (
              <option key={t} value={t}>{DATASET_LABELS[t] ?? t}</option>
            ))}
          </select>
        )
      }
    >
      {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}
      {error && <Alert>{error}</Alert>}
      {!data ? (
        <Loading />
      ) : data.length === 0 ? (
        <Empty>Nenhuma base carregada.</Empty>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Base</th>
                <th>Escopo</th>
                <th className="right">Versão vigente</th>
                <th className="right">Registros</th>
                <th>Última carga</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.map((d) => (
                <tr key={d.scope_key}>
                  <td>{DATASET_LABELS[d.dataset_type] ?? d.dataset_type}</td>
                  <td>
                    {scopeLabel(d)}
                    <div className="muted small mono">{d.scope_key}</div>
                  </td>
                  <td className="right">{d.current_version ? `v${d.current_version}` : "—"} <span className="muted small">de {d.versions}</span></td>
                  <td className="right">{fmtInt(d.rows)}</td>
                  <td>
                    {fmtDateTime(d.last_loaded_at)}
                    <div className="muted small">{d.last_file_name}</div>
                  </td>
                  <td className="row-actions">
                    {isAdmin && d.deletable && (
                      <button className="btn btn-ghost btn-sm danger" onClick={() => open({ dataset_type: d.dataset_type, scope_key: d.scope_key, label: `${DATASET_LABELS[d.dataset_type]} · ${scopeLabel(d)}`, rows: d.rows })}>
                        Excluir
                      </button>
                    )}
                    {!d.deletable && <span className="muted small">edite em Cadastros</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {target && (
        <Modal
          title="Excluir base"
          onClose={() => setTarget(null)}
          footer={
            <>
              <button className="btn btn-ghost" onClick={() => setTarget(null)}>Cancelar</button>
              <button className="btn btn-danger" disabled={busy || word.trim().toUpperCase() !== "EXCLUIR"} onClick={remove}>
                {busy ? "Excluindo…" : "Excluir definitivamente"}
              </button>
            </>
          }
        >
          <div className="stack">
            <p>
              Você vai excluir <strong>{target.label}</strong> — todas as versões
              {target.rows ? <> ({fmtInt(target.rows)} registros na versão vigente)</> : null}. Os arquivos poderão ser
              importados de novo em seguida. A exclusão fica registrada na auditoria e não pode ser desfeita.
            </p>
            <label>
              Motivo (opcional)
              <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Ex.: recarga para teste" />
            </label>
            <label>
              Digite EXCLUIR para confirmar
              <input value={word} onChange={(e) => setWord(e.target.value)} autoFocus />
            </label>
          </div>
        </Modal>
      )}
    </Card>
  );
}
