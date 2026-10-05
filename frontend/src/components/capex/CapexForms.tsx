import { useMemo, useState } from "react";
import { api, type CapexItem, type CapexOptions, type CapexProject } from "../../api";
import { MONTHS, fmtMoney } from "../../labels";
import { MoneyInput, parseMoney } from "../opex/MoneyInput";
import { Alert, Modal } from "../ui";

const num = (v: string | number | null | undefined) => (v === null || v === undefined || v === "" ? 0 : Number(v));
const round2 = (n: number) => Math.round(n * 100) / 100;

/** Solicitação: projeto (tipo + justificativa quantificada obrigatórios) ou aquisição avulsa. */
export function ProjectForm({ submissionId, project, options, onClose, onSaved }: {
  submissionId: number; project?: CapexProject; options: CapexOptions; onClose: () => void; onSaved: (p: CapexProject) => void;
}) {
  const [form, setForm] = useState({
    title: project?.title ?? "",
    is_project: project?.is_project ?? false,
    project_type_code: project?.project_type_code ?? "",
    branch_id: project?.branch_id ? String(project.branch_id) : "",
    priority: project?.priority ?? "",
    description: project?.description ?? "",
    justification: project?.justification ?? "",
    expected_cost_reduction: project?.expected_cost_reduction ?? "",
    expected_revenue: project?.expected_revenue ?? "",
    observations: project?.observations ?? "",
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form, v: string | boolean) => setForm((f) => ({ ...f, [k]: v }));

  async function save() {
    setBusy(true);
    setError(null);
    const money = (t: string) => (t.trim() ? parseMoney(t) : null);
    const body = {
      title: form.title,
      is_project: form.is_project,
      project_type_code: form.is_project ? form.project_type_code || null : null,
      branch_id: form.branch_id ? Number(form.branch_id) : null,
      priority: form.priority || null,
      description: form.description || null,
      justification: form.justification || null,
      expected_cost_reduction: money(String(form.expected_cost_reduction)),
      expected_revenue: money(String(form.expected_revenue)),
      observations: form.observations || null,
    };
    try {
      const saved = project
        ? await api<CapexProject>(`/capex/projects/${project.id}`, { method: "PATCH", body: JSON.stringify(body) })
        : await api<CapexProject>(`/capex/submissions/${submissionId}/projects`, { method: "POST", body: JSON.stringify(body) });
      onSaved(saved);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      wide
      title={project ? `Editar ${project.code}` : "Nova solicitação de CAPEX"}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>Cancelar</button>
          <button className="btn btn-primary" disabled={busy || !form.title.trim()} onClick={save}>{project ? "Salvar" : "Criar e adicionar itens"}</button>
        </>
      }
    >
      <div className="stack">
        {error && <Alert>{error}</Alert>}
        <label>Título<input value={form.title} onChange={(e) => set("title", e.target.value)} placeholder="Ex.: Ampliação da base de Itaituba" autoFocus /></label>
        <label className="check-row">
          <input type="checkbox" checked={form.is_project} onChange={(e) => set("is_project", e.target.checked)} />
          É um projeto (vários itens com objetivo comum, ex.: construção, implantação, automação)
        </label>
        <div className="form-row">
          {form.is_project && (
            <label>Tipo do projeto
              <select value={form.project_type_code} onChange={(e) => set("project_type_code", e.target.value)}>
                <option value="">Selecione…</option>
                {(options.lookups.CAPEX_PROJECT_TYPE ?? []).map((t) => <option key={t.code} value={t.code}>{t.label}</option>)}
              </select>
            </label>
          )}
          <label>Filial
            <select value={form.branch_id} onChange={(e) => set("branch_id", e.target.value)}>
              <option value="">—</option>
              {options.branches.map((b) => <option key={b.id} value={b.id}>{b.code} · {b.name}</option>)}
            </select>
          </label>
          <label>Prioridade
            <select value={form.priority} onChange={(e) => set("priority", e.target.value)}>
              <option value="">—</option>
              {(options.lookups.PRIORITY ?? []).map((t) => <option key={t.code} value={t.code}>{t.label}</option>)}
            </select>
          </label>
        </div>
        <label>Descrição<textarea rows={2} value={form.description} onChange={(e) => set("description", e.target.value)} /></label>
        <label>
          Justificativa {form.is_project ? "(obrigatória: objetivo e redução de custo ou receita esperada)" : ""}
          <textarea rows={3} value={form.justification} onChange={(e) => set("justification", e.target.value)} />
        </label>
        {form.is_project && (
          <div className="form-row">
            <label>Redução de custo esperada (R$/ano)<input inputMode="decimal" value={form.expected_cost_reduction} onChange={(e) => set("expected_cost_reduction", e.target.value)} /></label>
            <label>Receita esperada (R$/ano)<input inputMode="decimal" value={form.expected_revenue} onChange={(e) => set("expected_revenue", e.target.value)} /></label>
          </div>
        )}
        <label>Observações<textarea rows={2} value={form.observations} onChange={(e) => set("observations", e.target.value)} /></label>
      </div>
    </Modal>
  );
}

/** Item: valor unitário × quantidade e cronograma mensal, que precisa fechar com o total. */
export function ItemForm({ projectId, item, options, onClose, onSaved }: {
  projectId: number; item?: CapexItem; options: CapexOptions; onClose: () => void; onSaved: () => void;
}) {
  const [assetText, setAssetText] = useState(item?.asset_item_id ? options.asset_items.find((a) => a.id === item.asset_item_id)?.name ?? "" : "");
  const [accountId, setAccountId] = useState(item ? String(item.account_id) : "");
  const [name, setName] = useState(item?.item_name ?? "");
  const [description, setDescription] = useState(item?.description ?? "");
  const [unit, setUnit] = useState(num(item?.unit_value));
  const [qty, setQty] = useState(item ? String(num(item.quantity)) : "1");
  const [life, setLife] = useState(item?.useful_life_months ? String(item.useful_life_months) : "");
  const [values, setValues] = useState<number[]>(MONTHS.map((_, i) => num(item?.values[String(i + 1)])));
  const [oneMonth, setOneMonth] = useState("1");
  const [from, setFrom] = useState("1");
  const [to, setTo] = useState("12");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const asset = options.asset_items.find((a) => a.name.toUpperCase() === assetText.trim().toUpperCase());
  const total = round2(unit * Number(qty.replace(",", ".") || 0));
  const scheduled = round2(values.reduce((s, v) => s + v, 0));
  const diff = round2(scheduled - total);
  const minValue = Number(options.params.min_unit_value);
  const warnings = useMemo(() => {
    const out: string[] = [];
    if (unit > 0 && unit <= minValue) out.push(`Valor unitário até ${fmtMoney(minValue)}: avalie se é despesa (OPEX).`);
    if (life && Number(life) <= options.params.min_useful_life_months) out.push(`Vida útil até ${options.params.min_useful_life_months} meses não caracteriza CAPEX.`);
    return out;
  }, [unit, life, minValue, options.params.min_useful_life_months]);

  function pickAsset(text: string) {
    setAssetText(text);
    const found = options.asset_items.find((a) => a.name.toUpperCase() === text.trim().toUpperCase());
    if (found) {
      if (found.account_id) setAccountId(String(found.account_id));
      if (!name.trim()) setName(found.name);
    }
  }

  const allIn = () => setValues(MONTHS.map((_, i) => (i + 1 === Number(oneMonth) ? total : 0)));
  function spread() {
    const a = Number(from), b = Math.max(Number(to), a);
    const n = b - a + 1;
    const part = Math.floor((total / n) * 100) / 100;
    setValues(MONTHS.map((_, i) => {
      const m = i + 1;
      if (m < a || m > b) return 0;
      return m === b ? round2(total - part * (n - 1)) : part; // último mês absorve o arredondamento
    }));
  }
  function closeGap() {
    const last = values.map((v, i) => (v ? i : -1)).filter((i) => i >= 0).pop() ?? 0;
    setValues(values.map((v, i) => (i === last ? round2(v - diff) : v)));
  }

  async function save() {
    setBusy(true);
    setError(null);
    const body = {
      account_id: accountId ? Number(accountId) : null,
      asset_item_id: asset?.id ?? null,
      item_name: name,
      description: description || null,
      unit_value: unit,
      quantity: Number(qty.replace(",", ".") || 0),
      useful_life_months: life ? Number(life) : null,
      values: Object.fromEntries(values.map((v, i) => [String(i + 1), v])),
    };
    try {
      if (item) await api(`/capex/items/${item.id}`, { method: "PATCH", body: JSON.stringify(body) });
      else await api(`/capex/projects/${projectId}/items`, { method: "POST", body: JSON.stringify(body) });
      onSaved();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      wide
      title={item ? `Editar item · ${item.item_name}` : "Novo item"}
      onClose={onClose}
      footer={
        <>
          {diff !== 0 && <span className="muted small" style={{ marginRight: "auto" }}>Pode salvar com diferença, mas o envio fica bloqueado até fechar.</span>}
          <button className="btn btn-ghost" onClick={onClose}>Cancelar</button>
          <button className="btn btn-primary" disabled={busy || !name.trim() || !accountId || total <= 0} onClick={save}>Salvar item</button>
        </>
      }
    >
      <div className="stack">
        {error && <Alert>{error}</Alert>}
        <div className="form-row">
          <label>Item do catálogo de ativos
            <input list="asset-items" value={assetText} onChange={(e) => pickAsset(e.target.value)} placeholder="Digite para buscar (ex.: NOTEBOOK)" />
            <datalist id="asset-items">
              {options.asset_items.map((a) => <option key={a.id} value={a.name}>{a.asset_class ?? ""}</option>)}
            </datalist>
            {asset && <span className="muted small">Classe: {asset.asset_class ?? "—"}</span>}
          </label>
          <label>Conta do ativo
            <select value={accountId} onChange={(e) => setAccountId(e.target.value)} required>
              <option value="">Selecione…</option>
              {options.accounts.map((a) => <option key={a.id} value={a.id}>{a.code} · {a.name}</option>)}
            </select>
          </label>
        </div>
        <div className="form-row">
          <label>Item<input value={name} onChange={(e) => setName(e.target.value)} placeholder="Ex.: Notebook i7 16GB" /></label>
          <label>Descrição detalhada<input value={description} onChange={(e) => setDescription(e.target.value)} /></label>
        </div>
        <div className="form-row">
          <label>Valor unitário (R$)<MoneyInput value={unit} onCommit={setUnit} label="Valor unitário" /></label>
          <label>Quantidade<input inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} /></label>
          <label>Vida útil (meses)<input type="number" min={0} value={life} onChange={(e) => setLife(e.target.value)} placeholder="opcional" /></label>
          <label>Valor total<input value={fmtMoney(total)} disabled /></label>
        </div>
        {warnings.map((w) => <Alert key={w} tone="warn">{w}</Alert>)}

        <div className="card-head" style={{ marginBottom: 0 }}>
          <h2 style={{ fontSize: 14 }}>Cronograma de desembolso (competência da execução)</h2>
        </div>
        <div className="schedule-tools">
          <span>Tudo em</span>
          <select value={oneMonth} onChange={(e) => setOneMonth(e.target.value)} aria-label="Mês único">
            {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
          </select>
          <button type="button" className="btn btn-sm" onClick={allIn} disabled={total <= 0}>Aplicar</button>
          <span className="muted">·</span>
          <span>Dividir igualmente de</span>
          <select value={from} onChange={(e) => setFrom(e.target.value)} aria-label="Mês inicial">
            {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
          </select>
          <span>a</span>
          <select value={to} onChange={(e) => setTo(e.target.value)} aria-label="Mês final">
            {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
          </select>
          <button type="button" className="btn btn-sm" onClick={spread} disabled={total <= 0}>Distribuir</button>
        </div>
        <div className="schedule-grid">
          {MONTHS.map((m, i) => (
            <label key={m}>{m}
              <MoneyInput
                value={values[i]}
                label={`Cronograma ${m}`}
                onCommit={(n) => setValues((v) => v.map((x, j) => (j === i ? n : x)))}
                onPasteMany={(nums) => setValues((v) => v.map((x, j) => (j >= i && j - i < nums.length ? nums[j - i] : x)))}
              />
            </label>
          ))}
        </div>
        <div className="schedule-status">
          <span>Total do item: <strong>{fmtMoney(total)}</strong></span>
          <span>Cronograma: <strong>{fmtMoney(scheduled)}</strong></span>
          {diff === 0 ? (
            <span className="good">✓ cronograma fecha com o total</span>
          ) : (
            <>
              <span className="bad">Diferença: {fmtMoney(diff)}</span>
              {scheduled > 0 && <button type="button" className="btn btn-sm" onClick={closeGap}>Ajustar no último mês</button>}
            </>
          )}
        </div>
      </div>
    </Modal>
  );
}
