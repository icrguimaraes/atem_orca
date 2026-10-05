import { useState } from "react";
import { api, type PersonnelOptions, type PersonnelPosition, type PersonnelView } from "../../api";
import { MONTHS, fmtMoney } from "../../labels";
import { MoneyInput } from "../opex/MoneyInput";
import { Alert, Modal } from "../ui";

const ACTIONS = [
  { value: "KEEP", label: "Manter (sem alteração no ano)" },
  { value: "PROMOTION", label: "Promover (novo cargo e salário)" },
  { value: "SALARY_ADJUSTMENT", label: "Reajuste individual (mérito)" },
  { value: "TERMINATION", label: "Desligar" },
  { value: "TRANSFER", label: "Transferir para outro centro de custo" },
  { value: "HIRE", label: "Admissão prevista no ano (já cadastrado)" },
];

/** Ação do ano para um colaborador do quadro. */
export function MovementForm({ submissionId, position, options, currentCcId, year, onClose, onSaved }: {
  submissionId: number; position: PersonnelPosition; options: PersonnelOptions; currentCcId: number; year: number;
  onClose: () => void; onSaved: (v: PersonnelView) => void;
}) {
  const mv = position.movement;
  const [type, setType] = useState(mv?.type ?? "KEEP");
  const [month, setMonth] = useState(String(mv?.month ?? 1));
  const [newSalary, setNewSalary] = useState(Number(mv?.new_salary ?? 0));
  const [newPosition, setNewPosition] = useState(mv?.new_position ?? "");
  const [target, setTarget] = useState(mv?.target_cost_center_id ? String(mv.target_cost_center_id) : "");
  const [severance, setSeverance] = useState(Number(mv?.severance_cost ?? 0));
  const [reason, setReason] = useState(mv?.reason ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const base = Number(position.base_salary ?? 0);
  const needsReason = ["TERMINATION", "TRANSFER", "HIRE"].includes(type);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const v = await api<PersonnelView>(`/personnel/submissions/${submissionId}/employees/${position.employee_id}/movement`, {
        method: "PUT",
        body: JSON.stringify({
          type,
          month: type === "KEEP" ? null : Number(month),
          new_salary: ["PROMOTION", "SALARY_ADJUSTMENT", "TRANSFER", "HIRE"].includes(type) && newSalary ? newSalary : null,
          new_position: type === "PROMOTION" ? newPosition || null : null,
          target_cost_center_id: type === "TRANSFER" && target ? Number(target) : null,
          severance_cost: type === "TERMINATION" && severance ? severance : null,
          reason: reason || null,
        }),
      });
      onSaved(v);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title={`${position.name} · ação em ${year}`}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>Cancelar</button>
          <button className="btn btn-primary" disabled={busy} onClick={save}>Salvar</button>
        </>
      }
    >
      <div className="stack">
        {error && <Alert>{error}</Alert>}
        <p className="muted small">
          {position.position ?? "—"} · {position.contract_type_code} · salário atual {fmtMoney(base)}
        </p>
        <label>Ação
          <select value={type} onChange={(e) => setType(e.target.value)} autoFocus>
            {ACTIONS.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
          </select>
        </label>
        {type !== "KEEP" && (
          <div className="form-row">
            <label>Mês da ação
              <select value={month} onChange={(e) => setMonth(e.target.value)}>
                {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
              </select>
            </label>
            {["PROMOTION", "SALARY_ADJUSTMENT"].includes(type) && (
              <label>Novo salário (R$)
                <MoneyInput value={newSalary} onCommit={setNewSalary} label="Novo salário" />
                {newSalary > 0 && base > 0 && <span className="muted small">{((newSalary / base - 1) * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}% sobre o atual</span>}
              </label>
            )}
            {type === "PROMOTION" && (
              <label>Novo cargo
                <input list="positions" value={newPosition} onChange={(e) => setNewPosition(e.target.value)} />
                <datalist id="positions">{options.positions.map((p) => <option key={p} value={p} />)}</datalist>
              </label>
            )}
            {type === "TERMINATION" && (
              <label>Verba rescisória estimada (R$, opcional)
                <MoneyInput value={severance} onCommit={setSeverance} label="Verba rescisória" />
              </label>
            )}
            {type === "TRANSFER" && (
              <>
                <label>Centro de custo de destino
                  <select value={target} onChange={(e) => setTarget(e.target.value)}>
                    <option value="">Selecione…</option>
                    {options.cost_centers.filter((c) => c.id !== currentCcId).map((c) => <option key={c.id} value={c.id}>{c.code} · {c.name}</option>)}
                  </select>
                </label>
                <label>Salário no destino (opcional)
                  <MoneyInput value={newSalary} onCommit={setNewSalary} label="Salário no destino" />
                </label>
              </>
            )}
            {type === "HIRE" && (
              <label>Salário de admissão (opcional)
                <MoneyInput value={newSalary} onCommit={setNewSalary} label="Salário de admissão" />
              </label>
            )}
          </div>
        )}
        {type === "TERMINATION" && <p className="muted small">O custo vai até o mês anterior ao desligamento; a verba rescisória entra no mês do desligamento.</p>}
        {type === "TRANSFER" && <p className="muted small">O custo sai deste CC no mês da transferência e passa a contar no CC de destino.</p>}
        {type !== "KEEP" && (
          <label>Justificativa {needsReason ? "(obrigatória para enviar)" : "(opcional)"}
            <textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} />
          </label>
        )}
      </div>
    </Modal>
  );
}

/** Vaga: contratação planejada (sem colaborador). */
export function HireForm({ submissionId, position, options, onClose, onSaved }: {
  submissionId: number; position?: PersonnelPosition; options: PersonnelOptions; onClose: () => void; onSaved: (v: PersonnelView) => void;
}) {
  const mv = position?.movement;
  const [name, setName] = useState(position?.position ?? position?.name ?? "");
  const [quantity, setQuantity] = useState(String(mv?.quantity ?? 1));
  const [month, setMonth] = useState(String(mv?.month ?? 1));
  const [salary, setSalary] = useState(Number(mv?.new_salary ?? 0));
  const [contract, setContract] = useState(mv?.contract_type_code ?? "CLT");
  const [reason, setReason] = useState(mv?.reason ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const mult = Number(options.scenario.multipliers[contract] ?? 1);
  const applies = !options.scenario.ignored_multiplier_for.includes(contract);
  const adj = Number(options.scenario.salary_adjustment_pct);
  const months = 13 - Number(month);
  const estimate = salary * Number(quantity || 0) * (1 + adj) * (applies ? mult : 1) * months;

  async function save() {
    setBusy(true);
    setError(null);
    const body = JSON.stringify({ position_name: name, quantity: Number(quantity), month: Number(month), new_salary: salary, contract_type_code: contract, reason: reason || null });
    try {
      const v = mv
        ? await api<PersonnelView>(`/personnel/movements/${mv.id}`, { method: "PATCH", body })
        : await api<PersonnelView>(`/personnel/submissions/${submissionId}/hires`, { method: "POST", body });
      onSaved(v);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title={mv ? "Editar vaga" : "Nova vaga"}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>Cancelar</button>
          <button className="btn btn-primary" disabled={busy || !name.trim() || salary <= 0} onClick={save}>Salvar vaga</button>
        </>
      }
    >
      <div className="stack">
        {error && <Alert>{error}</Alert>}
        <label>Cargo
          <input list="positions-hire" value={name} onChange={(e) => setName(e.target.value)} placeholder="Ex.: Analista de Dados Pleno" autoFocus />
          <datalist id="positions-hire">{options.positions.map((p) => <option key={p} value={p} />)}</datalist>
        </label>
        <div className="form-row">
          <label>Quantidade<input type="number" min={1} value={quantity} onChange={(e) => setQuantity(e.target.value)} /></label>
          <label>Mês de entrada
            <select value={month} onChange={(e) => setMonth(e.target.value)}>
              {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
            </select>
          </label>
          <label>Contrato
            <select value={contract} onChange={(e) => setContract(e.target.value)}>
              {options.contract_types.map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}
            </select>
          </label>
          <label>Salário mensal (R$)<MoneyInput value={salary} onCommit={setSalary} label="Salário" /></label>
        </div>
        {salary > 0 && (
          <p className="muted small">
            Custo estimado no ano: <strong>{fmtMoney(estimate)}</strong> ({months} mês(es) × {quantity} pessoa(s), reajuste {(adj * 100).toLocaleString("pt-BR")}%,{" "}
            {applies ? `multiplicador ${mult.toLocaleString("pt-BR")}` : "sem multiplicador"}).
          </p>
        )}
        <label>Justificativa (obrigatória para enviar)
          <textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Ex.: expansão do time de dados para o projeto X" />
        </label>
      </div>
    </Modal>
  );
}
