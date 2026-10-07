import { useEffect, useState } from "react";
import { api, type CostCenter, type OpexLine } from "../../api";
import { fmtMoney } from "../../labels";
import { Alert, Modal } from "../ui";

/** Controladoria: leva o lançamento para o OPEX de outro CC da mesma empresa (CC lançado errado na planilha), com
 *  motivo — fica em Apontamentos (corrigidos) e na auditoria, sem reimportar. */
export function MoveLineModal({ line, companyId, currentCostCenterId, onClose, onDone }: {
  line: OpexLine; companyId: number; currentCostCenterId: number; onClose: () => void; onDone: (note: string) => void;
}) {
  const [ccs, setCcs] = useState<CostCenter[]>([]);
  const [query, setQuery] = useState("");
  const [target, setTarget] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<CostCenter[]>("/cost-centers")
      .then((all) => setCcs(all.filter((c) => c.company_id === companyId && c.is_active && c.id !== currentCostCenterId)))
      .catch((err) => setError((err as Error).message));
  }, [companyId, currentCostCenterId]);

  const q = query.trim().toLowerCase();
  const options = ccs.filter((c) => !q || `${c.code} ${c.name}`.toLowerCase().includes(q)).sort((a, b) => a.code.localeCompare(b.code));

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ note: string }>(`/opex/lines/${line.id}/move`, {
        method: "POST",
        body: JSON.stringify({ target_cost_center_id: Number(target), reason }),
      });
      onDone(r.note);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <Modal
      title="Mover lançamento para outro CC"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>Cancelar</button>
          <button type="button" className="btn btn-primary" disabled={!target || !reason.trim() || busy} onClick={save}>{busy ? "Movendo…" : "Mover"}</button>
        </>
      }
    >
      <div className="stack">
        <p className="small"><strong>{line.supplier || line.description || "Lançamento"}</strong> · {fmtMoney(line.total)}</p>
        <p className="muted small">Fica registrado em Apontamentos (corrigidos) e na auditoria. Se o arquivo da área for importado de novo, vale o que estiver na planilha.</p>
        {error && <Alert>{error}</Alert>}
        <label>Centro de custo de destino
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Buscar por código ou nome" />
        </label>
        <select value={target} onChange={(e) => setTarget(e.target.value)} size={Math.min(8, Math.max(3, options.length))} aria-label="Centro de custo de destino">
          {options.map((c) => <option key={c.id} value={c.id}>{c.code} · {c.name}</option>)}
        </select>
        <label>Motivo
          <textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Ex.: inventário do imobilizado é da Contabilidade; lançado no CC de Marketing por engano" />
        </label>
      </div>
    </Modal>
  );
}
