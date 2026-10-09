import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type PersonnelPremises as Premises } from "../api";
import { Alert, Badge, Card, Empty, Loading, Modal, PageHeader, Stat, useLoad } from "../components/ui";
import { MONTHS, fmtMoney, fmtShare } from "../labels";

/* Premissas do custo de pessoal do ciclo (Controladoria): dissídio, multiplicadores, abono anual do CLT, bônus por
   CC, rateio da parte do multiplicador entre contas e total por conta. Aponta (e corrige) as contas do rateio que já
   recebem abono/bônus como linha própria — contagem em dobro (apontamento PERSONNEL_SPLIT_OVERLAP). Os valores são
   editados em "Ciclo e parâmetros". */

const COMPONENTS: Record<string, string> = {
  salary: "salário",
  charges: "rateio do multiplicador",
  severance: "verbas rescisórias",
  bonus: "abono anual",
  cc_bonus: "bônus por CC",
};

/** Peso normalizado com 2 casas: 0,0573 → 5,73%. */
const pct2 = (v: string) => `${(Number(v) * 100).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`;

export default function PersonnelPremises() {
  const { data, error, reload } = useLoad(() => api<Premises>("/personnel/premises"));
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "good" | "bad"; text: string } | null>(null);

  if (error) return <Alert>{error}</Alert>;
  if (!data) return <Loading />;
  const p = data;
  const readOnly = p.frozen || p.cycle_status === "CLOSED";

  async function removeOverlap() {
    setBusy(true);
    setMsg(null);
    try {
      const out = await api<{ removed: string[] }>("/personnel/premises/remove-overlap", { method: "POST" });
      setMsg({ tone: "good", text: `Retirada(s) do rateio: ${out.removed.join(", ")}. O total de pessoal não mudou; o valor foi redistribuído entre as demais contas.` });
      setConfirm(false);
      reload();
    } catch (err) {
      setMsg({ tone: "bad", text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Premissas de pessoal"
        subtitle={`Orçamento ${p.target_year} · versão ${p.version} · cenário base "${p.scenario.name}": o que forma o custo de pessoal e em que contas ele cai.`}
        actions={<Link className="btn btn-sm" to="/ciclo">Ciclo e parâmetros</Link>}
      />
      <div className="section-stack">
        {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}
        {readOnly && (
          <Alert tone="info">
            {p.frozen ? "Versão congelada: só leitura (total por conta da fotografia). Para mudar premissas, abra uma revisão." : "Ciclo encerrado: só leitura."}
          </Alert>
        )}

        {p.overlap.length > 0 && (
          <Alert tone="warn">
            <strong>Contagem em dobro no rateio de encargos.</strong>{" "}
            {p.overlap.map((o) => `${o.code} ${o.name}`).join(" e ")} já recebe{p.overlap.length > 1 ? "m" : ""} o{" "}
            {p.overlap.map((o) => o.label).join(" e o ")} como linha própria, e o rateio da parte do multiplicador ainda
            manda {fmtMoney(p.overlap_amount)} para {p.overlap.length > 1 ? "essas contas" : "essa conta"} em {p.target_year}.
            O multiplicador (ex.: CLT {p.multipliers.find((m) => m.code === "CLT")?.multiplier.replace(".", ",") ?? "1,8"}×) já
            cobre encargos e benefícios; "Retirar do rateio" redistribui esse valor entre as demais contas do rateio — o total
            de pessoal não muda. Para reduzir o total, revise o multiplicador ou o abono/bônus em{" "}
            <Link className="link" to="/ciclo">Ciclo e parâmetros</Link>.
            {!readOnly && (
              <div style={{ marginTop: 10 }}>
                <button type="button" className="btn btn-primary btn-sm" onClick={() => setConfirm(true)}>Retirar do rateio</button>
              </div>
            )}
          </Alert>
        )}

        <Card title={`Custo de pessoal ${p.target_year}`}>
          <div className="stats">
            <Stat label="Total de pessoal" value={fmtMoney(p.total)} />
            <Stat label="Salários (com dissídio)" value={fmtMoney(p.salary.total)} hint={p.salary.account.code} />
            <Stat label="Parte do multiplicador" value={fmtMoney(p.charges.total)} hint={p.charges.has_split ? "rateada entre contas" : p.charges.default_account.code} />
            <Stat label="Abono anual do CLT" value={fmtMoney(p.abono.total)} hint={p.abono.account.code} />
            <Stat label="Bônus CLT por CC" value={fmtMoney(p.bonus_by_cc.booked)} hint={p.bonus_by_cc.account.code} />
            <Stat label="Verbas rescisórias" value={fmtMoney(p.severance.total)} hint={p.severance.account.code} />
          </div>
        </Card>

        <div className="grid-2">
          <Card title="Dissídio e multiplicadores">
            <div className="stats stats-compact">
              <Stat label="Dissídio" value={fmtShare(p.scenario.salary_adjustment_pct)} />
              <Stat label="A partir de" value={MONTHS[p.scenario.adjustment_month - 1] ?? "—"} />
            </div>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr><th>Contrato</th><th className="right">Multiplicador</th></tr>
                </thead>
                <tbody>
                  {p.multipliers.map((m) => (
                    <tr key={m.code}>
                      <td>{m.code} · {m.name}{!m.is_active && <span className="muted small"> (inativo)</span>}</td>
                      <td className="right">{m.apply_multiplier ? `${Number(m.multiplier).toLocaleString("pt-BR")}×` : <span className="muted">sem multiplicador</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <Card title="Abono anual do CLT">
            <div className="stats stats-compact">
              <Stat label="Por colaborador" value={fmtMoney(p.abono.value)} hint="R$/ano, em 12 parcelas" />
              <Stat label={`Total ${p.target_year}`} value={fmtMoney(p.abono.total)} />
            </div>
            <p className="muted small">
              Contratos: {p.abono.contracts.join(", ")} · sem multiplicador · conta {p.abono.account.code} {p.abono.account.name}
              {p.overlap.some((o) => o.kind === "bonus") && <> · <Badge tone="warn">também no rateio</Badge></>}
            </p>
          </Card>
        </div>

        <Card
          title="Bônus CLT por centro de custo"
          actions={p.overlap.some((o) => o.kind === "cc_bonus") ? <Badge tone="warn">conta também no rateio</Badge> : undefined}
        >
          <p className="muted small">Valor anual já com dissídio, diluído de JAN a DEZ na conta {p.bonus_by_cc.account.code} {p.bonus_by_cc.account.name}.</p>
          {p.bonus_by_cc.rows.length === 0 ? (
            <Empty>Nenhum bônus por CC no ciclo.</Empty>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr><th>Centro de custo</th><th className="right">Valor anual</th><th className="right">No orçamento</th></tr>
                </thead>
                <tbody>
                  {p.bonus_by_cc.rows.map((r) => (
                    <tr key={r.code}>
                      <td>{r.code}{r.name ? ` · ${r.name}` : <span className="muted small"> (CC não cadastrado)</span>}</td>
                      <td className="right nowrap">{fmtMoney(r.annual)}</td>
                      <td className="right nowrap">{fmtMoney(r.booked)}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr><td><strong>Total</strong></td><td className="right nowrap"><strong>{fmtMoney(p.bonus_by_cc.total)}</strong></td><td className="right nowrap"><strong>{fmtMoney(p.bonus_by_cc.booked)}</strong></td></tr>
                </tfoot>
              </table>
            </div>
          )}
        </Card>

        <Card title="Rateio da parte do multiplicador">
          <p className="muted small">
            Encargos e benefícios = custo − salário − verbas rescisórias − abono − bônus por CC, rateados pelos pesos de
            "Rateio de encargos e benefícios por conta" (normalizados para somar 100%).
            {!p.charges.has_split && ` Sem rateio: tudo na conta ${p.charges.default_account.code}.`}
          </p>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr><th>Conta</th><th className="right">Peso</th><th className="right">% do rateio</th><th className="right">{p.target_year}</th></tr>
              </thead>
              <tbody>
                {p.charges.rows.map((r) => (
                  <tr key={r.code}>
                    <td>
                      {r.code} · {r.name}
                      {r.overlap && <> <Badge tone="warn">já recebe abono/bônus</Badge></>}
                    </td>
                    <td className="right">{r.weight ? Number(r.weight).toLocaleString("pt-BR") : "—"}</td>
                    <td className="right">{pct2(r.share)}</td>
                    <td className="right nowrap">{fmtMoney(r.amount)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr><td><strong>Total</strong></td><td /><td className="right">100%</td><td className="right nowrap"><strong>{fmtMoney(p.charges.total)}</strong></td></tr>
              </tfoot>
            </table>
          </div>
        </Card>

        <Card title={`Pessoal ${p.target_year} por conta`}>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr><th>Conta</th><th>Composição</th><th className="right">{p.target_year}</th></tr>
              </thead>
              <tbody>
                {p.accounts.map((a) => (
                  <tr key={a.code}>
                    <td>{a.code} · {a.name ?? "—"}</td>
                    <td className="muted small">
                      {Object.entries(a.components).map(([k, v]) => `${COMPONENTS[k] ?? k} ${fmtMoney(v)}`).join(" + ") || "—"}
                    </td>
                    <td className="right nowrap">{fmtMoney(a.total)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr><td><strong>Total</strong></td><td /><td className="right nowrap"><strong>{fmtMoney(p.total)}</strong></td></tr>
              </tfoot>
            </table>
          </div>
        </Card>
      </div>

      {confirm && (
        <Modal
          title="Retirar do rateio"
          onClose={() => setConfirm(false)}
          footer={
            <>
              <button type="button" className="btn btn-ghost" onClick={() => setConfirm(false)} disabled={busy}>Cancelar</button>
              <button type="button" className="btn btn-primary" onClick={removeOverlap} disabled={busy}>{busy ? "Retirando…" : "Retirar do rateio"}</button>
            </>
          }
        >
          <p>
            Sai{p.overlap.length > 1 ? "em" : ""} do parâmetro "Rateio de encargos e benefícios por conta":{" "}
            <strong>{p.overlap.map((o) => o.code).join(", ")}</strong>.
          </p>
          <p className="muted small">
            Os {fmtMoney(p.overlap_amount)} que o rateio mandava para {p.overlap.length > 1 ? "essas contas" : "essa conta"} passam
            para as demais, na proporção dos pesos. O total de pessoal ({fmtMoney(p.total)}) não muda. A alteração fica na
            auditoria, como qualquer mudança de parâmetro.
          </p>
        </Modal>
      )}
    </>
  );
}
