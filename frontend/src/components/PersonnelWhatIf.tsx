import { Suspense, lazy, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, type PersonnelPremises, type PremisesWhatIf, type WhatIfCompare, type WhatIfRanked } from "../api";
import { MONTHS, fmtMoney, fmtPct, fmtSignedMoney } from "../labels";
import type { Figure } from "./PlotlyChart";
import { Alert, Card, Stat } from "./ui";

/* What-if das premissas de pessoal (Controladoria): dissídio, multiplicadores, abono do CLT, bônus por CC, retirada
   do rateio e verbas rescisórias, comparando o custo atual com o simulado. Nada é gravado: cada mudança dispara
   (com atraso curto) POST /personnel/premises/what-if, que calcula os dois lados pelo mesmo caminho da consolidação. */

// o Plotly só carrega quando o gráfico aparece (fora do bundle principal)
const PlotlyChart = lazy(() => import("./PlotlyChart").then((m) => ({ default: m.PlotlyChart })));

interface Draft {
  adj: string; // dissídio em %, texto pt-BR
  month: string;
  mults: Record<string, string>;
  abono: string; // R$ por pessoa no ano
  ccPct: string; // ajuste do bônus por CC em %
  ccBonus: boolean;
  removeOverlap: boolean;
  severance: boolean;
}

/** "4,7" → 4.7 · "2.500,00" e "2.500" → 2500; vazio ou inválido → null. */
function num(v: string): number | null {
  let t = v.trim().replace(/\s|R\$/g, "");
  if (t.includes(",")) t = t.replace(/\./g, "").replace(",", ".");
  else if (/^-?\d{1,3}(\.\d{3})+$/.test(t)) t = t.replace(/\./g, "");
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

/** Número para o campo, sem separador de milhar (0,0625 → "6,25"). */
const br = (n: number, digits = 2) => n.toLocaleString("pt-BR", { maximumFractionDigits: digits, useGrouping: false });
const H3 = { margin: "20px 0 8px" };

function initial(p: PersonnelPremises): Draft {
  return {
    adj: br(Number(p.scenario.salary_adjustment_pct) * 100, 4),
    month: String(p.scenario.adjustment_month),
    mults: Object.fromEntries(p.multipliers.filter((m) => m.apply_multiplier).map((m) => [m.code, br(Number(m.multiplier), 4)])),
    abono: br(Number(p.abono.value), 2),
    ccPct: "0",
    ccBonus: true,
    removeOverlap: false,
    severance: true,
  };
}

/** Corpo do request: só o que mudou em relação ao atual (o resto o backend lê do ciclo) + erros de validação. */
function payload(d: Draft, base: Draft): { body: Record<string, unknown>; errors: string[] } {
  const body: Record<string, unknown> = {};
  const errors: string[] = [];
  if (d.adj !== base.adj) {
    const n = num(d.adj);
    if (n === null || n < 0 || n > 50) errors.push("Dissídio entre 0% e 50%");
    else body.salary_adjustment_pct = (n / 100).toFixed(6);
  }
  if (d.month !== base.month) body.adjustment_month = Number(d.month);
  const mults: Record<string, string> = {};
  for (const [code, v] of Object.entries(d.mults)) {
    if (v === base.mults[code]) continue;
    const n = num(v);
    if (n === null || n <= 0 || n > 5) errors.push(`Multiplicador ${code} entre 0 e 5`);
    else mults[code] = n.toFixed(4);
  }
  if (Object.keys(mults).length) body.multipliers = mults;
  if (d.abono !== base.abono) {
    const n = num(d.abono);
    if (n === null || n < 0) errors.push("Abono anual não pode ser negativo");
    else body.annual_bonus = n.toFixed(2);
  }
  if (d.ccBonus && d.ccPct !== base.ccPct) {
    const n = num(d.ccPct);
    if (n === null || n < -100 || n > 200) errors.push("Ajuste do bônus por CC entre -100% e +200%");
    else if (n !== 0) body.cc_bonus_pct = (n / 100).toFixed(6);
  }
  if (!d.ccBonus) body.include_cc_bonus = false;
  if (d.removeOverlap) body.remove_overlap = true;
  if (!d.severance) body.include_severance = false;
  return { body, errors };
}

function Delta({ c }: { c: WhatIfCompare }) {
  const n = Number(c.difference);
  return (
    <>
      <td className={`right nowrap delta ${n > 0 ? "up" : n < 0 ? "down" : ""}`}>{n ? fmtSignedMoney(n) : "—"}</td>
      <td className={`right nowrap delta ${n > 0 ? "up" : n < 0 ? "down" : ""}`}>{n && c.difference_pct ? fmtPct(c.difference_pct) : "—"}</td>
    </>
  );
}

function CompareHead({ first }: { first: string }) {
  return (
    <thead>
      <tr><th>{first}</th><th className="right">Atual</th><th className="right">Simulado</th><th className="right">Diferença</th><th className="right">%</th></tr>
    </thead>
  );
}

function RankedTable({ data, first }: { data: WhatIfRanked; first: string }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <CompareHead first={first} />
        <tbody>
          {data.rows.map((r) => (
            <tr key={`${r.cost_center_id ?? ""}${r.label}`}>
              <td>{r.code ? `${r.code} · ` : ""}{r.label}</td>
              <td className="right nowrap">{fmtMoney(r.current)}</td>
              <td className="right nowrap">{fmtMoney(r.simulated)}</td>
              <Delta c={r} />
            </tr>
          ))}
          {data.others && (
            <tr className="muted">
              <td>{data.others.label}</td>
              <td className="right nowrap">{fmtMoney(data.others.current)}</td>
              <td className="right nowrap">{fmtMoney(data.others.simulated)}</td>
              <Delta c={data.others} />
            </tr>
          )}
        </tbody>
        <tfoot>
          <tr>
            <td><strong>Total</strong></td>
            <td className="right nowrap"><strong>{fmtMoney(data.total.current)}</strong></td>
            <td className="right nowrap"><strong>{fmtMoney(data.total.simulated)}</strong></td>
            <Delta c={data.total} />
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

export function PersonnelWhatIf({ premises }: { premises: PersonnelPremises }) {
  const base = useMemo(() => initial(premises), [premises]);
  const [draft, setDraft] = useState<Draft>(base);
  const [result, setResult] = useState<PremisesWhatIf | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [group, setGroup] = useState<"cc" | "area">("cc");
  const seq = useRef(0);

  useEffect(() => setDraft(base), [base]);
  const { body, errors } = payload(draft, base);
  const key = JSON.stringify(body);

  // pedido com atraso curto a cada mudança; só a resposta mais recente vale
  useEffect(() => {
    if (errors.length) return;
    const id = ++seq.current;
    const timer = window.setTimeout(async () => {
      setBusy(true);
      try {
        const out = await api<PremisesWhatIf>("/personnel/premises/what-if", { method: "POST", body: key });
        if (id === seq.current) {
          setResult(out);
          setError(null);
        }
      } catch (err) {
        if (id === seq.current) setError((err as Error).message);
      } finally {
        if (id === seq.current) setBusy(false);
      }
    }, 350);
    return () => window.clearTimeout(timer);
  }, [key, errors.length]);

  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setDraft((d) => ({ ...d, [k]: v }));
  const changed = key !== "{}";
  const year = premises.target_year;
  const total = result?.summary.find((s) => s.key === "total");
  const diff = total ? Number(total.difference) : 0;

  return (
    <Card
      title={`What-if: simular o custo de pessoal ${year}`}
      actions={
        <button type="button" className="btn btn-sm" onClick={() => setDraft(base)} disabled={!changed}>
          Voltar ao atual
        </button>
      }
    >
      <Alert tone="info">
        Simulação: nada é gravado; para valer, altere em <Link className="link" to="/ciclo">Ciclo e parâmetros</Link>.
      </Alert>

      <div className="stack" style={{ marginTop: 12 }}>
        <div className="form-row" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))" }}>
          <label>
            Dissídio (%)
            <input inputMode="decimal" value={draft.adj} onChange={(e) => set("adj", e.target.value)} />
            <span className="muted small">atual: {base.adj}%</span>
          </label>
          <label>
            A partir de
            <select value={draft.month} onChange={(e) => set("month", e.target.value)}>
              {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
            </select>
            <span className="muted small">atual: {MONTHS[Number(base.month) - 1]}</span>
          </label>
          {premises.multipliers.filter((m) => m.apply_multiplier && m.is_active).map((m) => (
            <label key={m.code}>
              Multiplicador {m.code}
              <input
                inputMode="decimal"
                value={draft.mults[m.code] ?? ""}
                onChange={(e) => set("mults", { ...draft.mults, [m.code]: e.target.value })}
              />
              <span className="muted small">atual: {base.mults[m.code]}×</span>
            </label>
          ))}
          <label>
            Abono anual do CLT (R$/pessoa)
            <input inputMode="decimal" value={draft.abono} onChange={(e) => set("abono", e.target.value)} />
            <span className="muted small">atual: {fmtMoney(premises.abono.value)}</span>
          </label>
          <label>
            Bônus por CC: ajuste (%)
            <input inputMode="decimal" value={draft.ccPct} disabled={!draft.ccBonus} onChange={(e) => set("ccPct", e.target.value)} />
            <span className="muted small">sobre {fmtMoney(premises.bonus_by_cc.total)} do parâmetro · 0 = como está</span>
          </label>
        </div>
        <div className="toggle-options">
          <label>
            <input type="checkbox" checked={draft.ccBonus} onChange={(e) => set("ccBonus", e.target.checked)} />
            Incluir bônus por CC
          </label>
          <label>
            <input type="checkbox" checked={draft.severance} onChange={(e) => set("severance", e.target.checked)} />
            Incluir verbas rescisórias
          </label>
          <label title="Simula a retirada das contas que já recebem abono/bônus do rateio da parte do multiplicador">
            <input type="checkbox" checked={draft.removeOverlap} onChange={(e) => set("removeOverlap", e.target.checked)} />
            Retirar do rateio (só redistribui entre contas)
          </label>
          {busy && <span className="muted small">Calculando…</span>}
        </div>
        {errors.length > 0 && <Alert tone="warn">{errors.join(" · ")}</Alert>}
        {error && <Alert>{error}</Alert>}
        {result?.frozen && <p className="muted small">Versão congelada: a simulação usa o cálculo do quadro da versão {result.version}.</p>}
        {result && draft.removeOverlap && (
          <p className="muted small">
            {result.removed_from_split.length
              ? `Fora do rateio na simulação: ${result.removed_from_split.join(", ")}. O total não muda; o valor passa para as demais contas do rateio.`
              : "Nenhuma conta do rateio recebe abono ou bônus: nada a retirar."}
          </p>
        )}
      </div>

      {result && total && (
        <>
          <div className="stats" style={{ marginTop: 16 }}>
            <Stat label={`Atual ${year}`} value={fmtMoney(total.current)} />
            <Stat label="Simulado" value={fmtMoney(total.simulated)} tone={changed ? "budget" : undefined} />
            <Stat
              label="Diferença"
              value={diff ? fmtSignedMoney(diff) : "—"}
              tone={diff > 0 ? "bad" : diff < 0 ? "good" : undefined}
              hint={diff && total.difference_pct ? `${fmtPct(total.difference_pct)} sobre o atual` : undefined}
            />
          </div>

          <h3 className="section-title" style={H3}>Composição</h3>
          <div className="table-wrap">
            <table className="table">
              <CompareHead first="Componente" />
              <tbody>
                {result.summary.filter((s) => s.key !== "total").map((s) => (
                  <tr key={s.key}>
                    <td>{s.label}</td>
                    <td className="right nowrap">{fmtMoney(s.current)}</td>
                    <td className="right nowrap">{fmtMoney(s.simulated)}</td>
                    <Delta c={s} />
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td><strong>{total.label}</strong></td>
                  <td className="right nowrap"><strong>{fmtMoney(total.current)}</strong></td>
                  <td className="right nowrap"><strong>{fmtMoney(total.simulated)}</strong></td>
                  <Delta c={total} />
                </tr>
              </tfoot>
            </table>
          </div>

          <h3 className="section-title" style={H3}>Por mês</h3>
          <Suspense fallback={<p className="muted small">Carregando gráfico…</p>}>
            <PlotlyChart figure={result.figure as unknown as Figure} height={240} ariaLabel={`Custo mensal de pessoal ${year}: atual e simulado`} />
          </Suspense>

          <h3 className="section-title" style={H3}>Por conta</h3>
          <div className="table-wrap">
            <table className="table">
              <CompareHead first="Conta" />
              <tbody>
                {result.accounts.map((a) => (
                  <tr key={a.code}>
                    <td>{a.code} · {a.label ?? "—"}</td>
                    <td className="right nowrap">{fmtMoney(a.current)}</td>
                    <td className="right nowrap">{fmtMoney(a.simulated)}</td>
                    <Delta c={a} />
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td><strong>Total</strong></td>
                  <td className="right nowrap"><strong>{fmtMoney(total.current)}</strong></td>
                  <td className="right nowrap"><strong>{fmtMoney(total.simulated)}</strong></td>
                  <Delta c={total} />
                </tr>
              </tfoot>
            </table>
          </div>

          <div className="card-head" style={{ marginTop: 16 }}>
            <h3 className="section-title" style={{ margin: 0 }}>
              {group === "cc"
                ? `Por centro de custo${result.cost_centers.others ? ` (${result.cost_centers.rows.length} de ${result.cost_centers.count}, maior diferença primeiro)` : ""}`
                : "Por área"}
            </h3>
            <div className="why-toggle" role="group" aria-label="Agrupar por">
              <button type="button" className={group === "cc" ? "active" : ""} onClick={() => setGroup("cc")}>CC</button>
              <button type="button" className={group === "area" ? "active" : ""} onClick={() => setGroup("area")}>Área</button>
            </div>
          </div>
          <RankedTable data={group === "cc" ? result.cost_centers : result.areas} first={group === "cc" ? "Centro de custo" : "Área"} />
        </>
      )}
    </Card>
  );
}
