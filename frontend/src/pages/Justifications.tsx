import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, download } from "../api";
import { FilterBar } from "../components/FilterBar";
import { Alert, Badge, Card, Empty, Loading, PageHeader, SearchBox, Stat, useLoad } from "../components/ui";
import { fmtInt, fmtMoney, fmtPct } from "../labels";
import { usePersistentState } from "../persist";
import { Subject } from "../components/WhyPanel";

/* Justificativas do orçamento numa tela só (regra de 08/10/2026: "justificar tudo" — quem defende o número é o
   gestor da área). Cada item mostra o contexto (referência × 2027, linhas, cargo e salário) e salva na hora, com
   registro na auditoria e em Apontamentos. Daqui saem o Excel das justificativas e os templates OPEX/CAPEX do CC,
   já com as justificativas. */

interface JustItem {
  key: string; submission_id: number; module: "OPEX" | "PERSONNEL" | "CAPEX"; kind: string;
  cost_center_id: number; cost_center: string; department: string | null; sector: string | null; status: string;
  subject: string; group: string; base: string | null; proposed: string | null; flags: string[];
  details: string[]; line_texts: string[]; text: string; justified: boolean; editable: boolean; movement_type?: string;
}
interface JustData {
  version: string; target_year: number; ref_year: number; items: JustItem[];
  counts: { total: number; missing: number; cost_centers: number };
}

const MODULES = [
  { value: "", label: "OPEX, Pessoal e CAPEX" },
  { value: "OPEX", label: "OPEX" },
  { value: "PERSONNEL", label: "Pessoal" },
  { value: "CAPEX", label: "CAPEX" },
];
const MODULE_LABEL: Record<string, string> = { OPEX: "OPEX", PERSONNEL: "Pessoal", CAPEX: "CAPEX" };
const FLAG_LABEL: Record<string, string> = {
  NEW_ACCOUNT: "conta nova",
  NO_BUDGET: "zerada no orçamento",
  GROWTH_ABOVE: "cresce acima do limite",
  REDUCTION_ABOVE: "cai acima do limite",
};

function fold(s: string) {
  return s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

function Values({ item, refYear, target }: { item: JustItem; refYear: number; target: number }) {
  const base = item.base === null ? null : Number(item.base);
  const prop = item.proposed === null ? null : Number(item.proposed);
  if (item.module === "PERSONNEL") return null;
  const pct = base && prop !== null ? (prop - base) / base : null;
  return (
    <div className="just-values">
      {base !== null && <span><span className="muted">{refYear} anual.</span> {fmtMoney(base)}</span>}
      {prop !== null && <span><span className="muted">{target}</span> <strong>{fmtMoney(prop)}</strong></span>}
      {pct !== null && (
        <span className={pct > 0 ? "error-text" : "good-text"}>
          {fmtPct(String(pct))} ({prop! - base! >= 0 ? "+" : "−"}{fmtMoney(Math.abs(prop! - base!))})
        </span>
      )}
    </div>
  );
}

function ItemRow({ item, refYear, target, onSaved }: { item: JustItem; refYear: number; target: number; onSaved: (i: JustItem) => void }) {
  const [text, setText] = useState(item.text);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "good" | "bad"; text: string } | null>(null);
  const dirty = text.trim() !== item.text.trim();

  async function save() {
    if (!text.trim()) {
      setMsg({ tone: "bad", text: "Escreva a justificativa antes de salvar" });
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const r = await api<{ text: string; justified: boolean }>("/justifications", {
        method: "PUT",
        body: JSON.stringify({ key: item.key, text }),
      });
      onSaved({ ...item, text: r.text, justified: r.justified });
      setMsg({ tone: "good", text: "Salvo e registrado" });
    } catch (e) {
      setMsg({ tone: "bad", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className={`just-item ${item.justified ? "" : "missing"}`}>
      <div className="just-head">
        <div>
          <div className="just-subject"><Subject text={item.subject} movementType={item.movement_type} /></div>
          <div className="muted small">
            {MODULE_LABEL[item.module]} · {item.group}
            {item.flags.map((f) => <span key={f}> · {FLAG_LABEL[f] ?? f}</span>)}
          </div>
        </div>
        <Badge tone={item.justified ? "good" : "bad"}>{item.justified ? "Justificado" : "Falta justificar"}</Badge>
      </div>
      <Values item={item} refYear={refYear} target={target} />
      {item.details.length > 0 && (
        <ul className="just-details muted small">
          {item.details.map((d, i) => <li key={i}>{d}</li>)}
        </ul>
      )}
      {item.line_texts.length > 0 && !item.text && (
        <div className="muted small">Justificativa nas linhas do template: “{item.line_texts.join(" / ")}”</div>
      )}
      {item.editable ? (
        <div className="just-edit">
          <textarea
            rows={2}
            value={text}
            onChange={(e) => { setText(e.target.value); setMsg(null); }}
            placeholder="Por que este valor? (o que é, por que mudou em relação à referência, premissa usada)"
            aria-label={`Justificativa de ${item.subject}`}
          />
          <div className="just-actions">
            <button type="button" className="btn btn-sm btn-primary" disabled={busy || !dirty} onClick={save}>
              {busy ? "Salvando…" : "Salvar"}
            </button>
            {msg && <span className={`small ${msg.tone === "good" ? "good-text" : "error-text"}`}>{msg.text}</span>}
          </div>
        </div>
      ) : (
        <div className="small">{item.text || <span className="muted">Sem justificativa (somente consulta)</span>}</div>
      )}
    </li>
  );
}

export default function Justifications() {
  const { data, error, reload } = useLoad(() => api<JustData>("/justifications"));
  const [cc, setCc] = usePersistentState("justificativas.cc", "");
  const [module, setModule] = usePersistentState("justificativas.module", "");
  const [status, setStatus] = usePersistentState("justificativas.status", "missing");
  const [search, setSearch] = usePersistentState("justificativas.search", "");
  const [items, setItems] = useState<JustItem[] | null>(null);
  const [dlError, setDlError] = useState<string | null>(null);
  const all = items ?? data?.items ?? [];

  const ccOptions = useMemo(() => {
    const by = new Map<number, { label: string; missing: number }>();
    for (const i of data?.items ?? []) {
      const c = by.get(i.cost_center_id) ?? { label: i.cost_center, missing: 0 };
      if (!i.justified) c.missing += 1;
      by.set(i.cost_center_id, c);
    }
    return [...by.entries()].sort((a, b) => a[1].label.localeCompare(b[1].label));
  }, [data]);

  if (error) return <Alert>{error}</Alert>;
  if (!data) return <Loading />;

  const needle = fold(search.trim());
  const shown = all.filter((i) =>
    (!cc || String(i.cost_center_id) === cc) &&
    (!module || i.module === module) &&
    (status !== "missing" || !i.justified) &&
    (!needle || fold(`${i.subject} ${i.group} ${i.details.join(" ")} ${i.text}`).includes(needle)));
  const groups = new Map<number, JustItem[]>();
  for (const i of shown) groups.set(i.cost_center_id, [...(groups.get(i.cost_center_id) ?? []), i]);
  const missing = all.filter((i) => !i.justified).length;
  const subsOf = (ccId: number, mod: string) => all.find((i) => i.cost_center_id === ccId && i.module === mod)?.submission_id;
  const code = (label: string) => label.split(" · ")[0];

  function onSaved(next: JustItem) {
    setItems(all.map((i) => (i.key === next.key ? next : i)));
  }
  function getFile(path: string, name: string) {
    setDlError(null);
    download(path, name).catch((e) => setDlError((e as Error).message));
  }

  return (
    <>
      <PageHeader
        title="Justificativas"
        subtitle="Regra do ciclo: tudo o que compõe o orçamento precisa de justificativa — contas do OPEX, movimentações de pessoal e solicitações de CAPEX. Quem defende o número é o gestor da área."
        actions={
          <button
            type="button"
            className="btn"
            onClick={() => getFile(`/justifications/export.xlsx${cc ? `?cost_center_id=${cc}` : ""}`, `Justificativas_${data.target_year}_v${data.version}.xlsx`)}
          >
            Exportar justificativas (Excel)
          </button>
        }
      />
      {dlError && <Alert>{dlError}</Alert>}
      <div className="stats">
        <Stat label="Itens a justificar" value={fmtInt(all.length)} hint={`${fmtInt(ccOptions.length)} centro(s) de custo`} />
        <Stat label="Justificados" value={fmtInt(all.length - missing)} tone="good" hint={all.length ? `${Math.round(((all.length - missing) / all.length) * 100)}% do total` : undefined} />
        <Stat label="Faltando" value={fmtInt(missing)} tone={missing ? "bad" : "good"} hint="bloqueiam o envio do orçamento" />
      </div>
      <FilterBar
        onReset={() => { setCc(""); setModule(""); setStatus("missing"); setSearch(""); }}
        resetCount={[cc, module, status !== "missing" ? "x" : "", search].filter(Boolean).length}
        fields={[
          {
            key: "cc", label: "Centro de custo", value: cc, onChange: setCc, wide: true,
            options: [
              { value: "", label: "Todos os centros de custo" },
              ...ccOptions.map(([id, c]) => ({ value: String(id), label: `${c.label}${c.missing ? ` · ${c.missing} faltando` : " · ok"}` })),
            ],
          },
          { key: "module", label: "Módulo", value: module, onChange: setModule, options: MODULES },
          {
            key: "status", label: "Situação", value: status === "missing" ? "" : status, onChange: (v) => setStatus(v || "missing"),
            options: [{ value: "", label: "Só o que falta" }, { value: "all", label: "Tudo (inclui justificados)" }],
          },
        ]}
        extra={<SearchBox value={search} onChange={setSearch} placeholder="Buscar conta, pessoa, projeto…" />}
      />
      {groups.size === 0 ? (
        <Empty>{status === "missing" && !needle ? "Tudo justificado nos filtros escolhidos." : "Nada encontrado nos filtros escolhidos."}</Empty>
      ) : (
        <div className="section-stack">
          {[...groups.entries()].map(([ccId, list]) => {
            const first = list[0];
            const opex = subsOf(ccId, "OPEX");
            const capex = subsOf(ccId, "CAPEX");
            const ccMissing = all.filter((i) => i.cost_center_id === ccId && !i.justified).length;
            return (
              <Card
                key={ccId}
                title={`${first.cost_center}${first.sector ? ` · ${first.department ?? ""} › ${first.sector}` : ""}`}
                actions={
                  <div className="inline-controls">
                    <Badge tone={ccMissing ? "bad" : "good"}>{ccMissing ? `${ccMissing} faltando` : "tudo justificado"}</Badge>
                    {opex && (
                      <button type="button" className="btn btn-sm" onClick={() => getFile(`/opex/submissions/${opex}/template.xlsx`, `Template_OPEX_${data.target_year}_${code(first.cost_center)}_v${data.version}.xlsx`)}>
                        Template OPEX
                      </button>
                    )}
                    {capex && (
                      <button type="button" className="btn btn-sm" onClick={() => getFile(`/capex/submissions/${capex}/template.xlsx`, `Template_CAPEX_${data.target_year}_${code(first.cost_center)}_v${data.version}.xlsx`)}>
                        Template CAPEX
                      </button>
                    )}
                    <Link to={`/apontamentos?cc=${ccId}`} className="btn btn-sm btn-ghost">Apontamentos</Link>
                  </div>
                }
              >
                <ul className="just-list">
                  {list.map((i) => <ItemRow key={i.key} item={i} refYear={data.ref_year} target={data.target_year} onSaved={onSaved} />)}
                </ul>
              </Card>
            );
          })}
        </div>
      )}
      <p className="muted small">
        Atualizar a lista: <button type="button" className="link" onClick={() => { setItems(null); reload(); }}>recarregar</button>. Os templates OPEX
        baixados aqui trazem a justificativa da conta nas linhas sem justificativa própria; o CAPEX traz a justificativa de cada solicitação.
      </p>
    </>
  );
}
