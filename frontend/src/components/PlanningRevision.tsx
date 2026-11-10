import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { MONTHS, fmtInt, fmtMoney, fmtSignedMoney } from "../labels";
import { Alert, Card, Empty, Stat, lines } from "./ui";

/* Revisão do Planejamento (10/11/2026): o setor de Planejamento devolve o OPEX ("Prévia Orçamento OPEX", aba
   CONSOLIDADO, por nome de CC e de conta). A carga mostra antes × depois na versão em elaboração e só grava depois de
   confirmar; o que não veio no arquivo vira apontamento para a Controladoria analisar. */

interface Row { before: string; after: string; difference: string }
interface Absent { cost_center: string; account: string; account_name: string; total: string }
interface PlanningPreview {
  version: { id: number; label: string }; year: number; sheet: string; file_rows: number; ignored_zero_rows: number;
  file_total: string; absent: "zero" | "keep"; errors: { row: number; message: string }[];
  matched_cost_centers: { file: string; code: string; name: string; company: string }[];
  totals: Row; monthly: ({ month: number } & Omit<Row, "difference">)[];
  by_cost_center: (Row & { cost_center: string })[]; by_account: (Row & { account: string; name: string })[];
  cells: (Row & { cost_center: string; account: string; account_name: string; month: number })[];
  changed_keys: number; zeroed: Absent[]; kept_absent: Absent[];
  pj_absent: { cost_center: string; total: string }[]; pj_note: string; digest: string;
}

const monthName = (m: number) => MONTHS[m - 1].charAt(0) + MONTHS[m - 1].slice(1).toLowerCase();

function DiffTable<T extends Row>({ rows, label }: { rows: T[]; label: (r: T) => ReactNode }) {
  const changed = rows.filter((r) => Math.abs(Number(r.difference)) >= 0.005);
  if (!changed.length) return <Empty>Sem diferença.</Empty>;
  return (
    <div className="table-wrap scroll-y">
      <table className="table">
        <thead><tr><th>Item</th><th className="right">Atual</th><th className="right">Planejamento</th><th className="right">Diferença</th></tr></thead>
        <tbody>
          {changed.map((r, i) => (
            <tr key={i}>
              <td>{label(r)}</td>
              <td className="right">{fmtMoney(r.before)}</td>
              <td className="right"><strong>{fmtMoney(r.after)}</strong></td>
              <td className={`right ${Number(r.difference) < 0 ? "good-text" : "error-text"}`}>{fmtSignedMoney(r.difference)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function PlanningRevisionCard({ frozen, versionLabel, onApplied }: { frozen: boolean; versionLabel: string; onApplied: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [absent, setAbsent] = useState<"zero" | "keep">("zero");
  const [data, setData] = useState<PlanningPreview | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [tab, setTab] = useState<"cc" | "account" | "cells">("cc");

  function form(extra: Record<string, string> = {}) {
    const f = new FormData();
    f.append("file", file as File);
    f.append("absent", absent);
    Object.entries(extra).forEach(([k, v]) => f.append(k, v));
    return f;
  }

  async function runPreview() {
    if (!file) return;
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      setData(await api<PlanningPreview>("/consolidation/planning-revision/preview", { method: "POST", body: form() }));
    } catch (e) {
      setError((e as Error).message);
      setData(null);
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!data) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ cost_centers: number; lines_adjusted?: number; lines_created?: number }>(
        "/consolidation/planning-revision/apply",
        { method: "POST", body: form({ digest: data.digest, reason }) },
      );
      setDone(`Gravado na ${data.version.label}: ${fmtInt(r.cost_centers)} CC(s), ${fmtInt(r.lines_adjusted ?? 0)} linha(s) ajustada(s), ${fmtInt(r.lines_created ?? 0)} criada(s).`);
      setData(null);
      setFile(null);
      setReason("");
      onApplied();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Revisão do Planejamento">
      <p className="muted small">
        Carrega na <strong>{versionLabel}</strong> o OPEX devolvido pelo Planejamento (aba CONSOLIDADO: empresa, CC e conta por nome, meses).
        Linhas existentes são preservadas e ajustadas na proporção; nada é gravado antes de confirmar. Contratos PJ continuam calculados dos contratos.
      </p>
      {frozen ? (
        <Alert tone="info">A {versionLabel} está congelada: abra uma revisão para carregar o Planejamento.</Alert>
      ) : (
        <div className="planning-form">
          <input type="file" accept=".xlsx,.xlsm" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setData(null); }} aria-label="Arquivo do Planejamento" />
          <label>
            O que não veio no arquivo
            <select value={absent} onChange={(e) => { setAbsent(e.target.value as "zero" | "keep"); setData(null); }}>
              <option value="zero">Zerar (fica igual ao arquivo) e apontar</option>
              <option value="keep">Manter como está e apontar</option>
            </select>
          </label>
          <button type="button" className="btn btn-primary btn-sm" disabled={!file || busy} onClick={runPreview}>{busy && !data ? "Lendo…" : "Ver prévia"}</button>
        </div>
      )}
      {error && <Alert>{error}</Alert>}
      {done && <Alert tone="good">{done} A comparação entre versões abaixo mostra o que mudou.</Alert>}
      {data && (
        <div className="stack planning-preview">
          {data.errors.length > 0 && (
            <Alert>
              {fmtInt(data.errors.length)} linha(s) do arquivo não casaram com o cadastro; nada será gravado até corrigir:
              <ul className="small">{data.errors.slice(0, 12).map((e, i) => <li key={i}>Linha {e.row}: {e.message}</li>)}</ul>
            </Alert>
          )}
          <div className="stats stats-compact">
            <Stat label={`Atual (${data.version.label})`} value={fmtMoney(data.totals.before)} />
            <Stat label="Com o Planejamento" value={fmtMoney(data.totals.after)} tone="budget" hint={lines(`arquivo: ${fmtMoney(data.file_total)}`)} />
            <Stat label="Diferença" value={fmtSignedMoney(data.totals.difference)} tone={Number(data.totals.difference) < 0 ? "good" : "bad"} hint={lines(`${fmtInt(data.changed_keys)} CC × conta alterados`)} />
            <Stat label="Arquivo" value={`${fmtInt(data.file_rows)} linhas`} hint={lines(`aba ${data.sheet}`, `${fmtInt(data.ignored_zero_rows)} linhas zeradas ignoradas`)} />
          </div>
          {data.zeroed.length > 0 && (
            <Alert tone="warn">
              <strong>Não vieram no arquivo e serão zerados</strong> ({fmtMoney(data.zeroed.reduce((s, z) => s + Number(z.total), 0))}) — ficam em Apontamentos para a Controladoria analisar:
              <ul className="small">{data.zeroed.map((z, i) => <li key={i}>{z.cost_center} · {z.account} {z.account_name}: {fmtMoney(z.total)}</li>)}</ul>
            </Alert>
          )}
          {data.kept_absent.length > 0 && (
            <Alert tone="info">
              <strong>Não vieram no arquivo e serão mantidos</strong> ({fmtMoney(data.kept_absent.reduce((s, z) => s + Number(z.total), 0))}):
              <ul className="small">{data.kept_absent.map((z, i) => <li key={i}>{z.cost_center} · {z.account} {z.account_name}: {fmtMoney(z.total)}</li>)}</ul>
            </Alert>
          )}
          {data.pj_absent.length > 0 && (
            <Alert tone="info">
              <strong>Contratos PJ fora do arquivo</strong> ({fmtMoney(data.pj_absent.reduce((s, z) => s + Number(z.total), 0))}): {data.pj_note}. Viram apontamento:
              <ul className="small">{data.pj_absent.map((z, i) => <li key={i}>{z.cost_center}: {fmtMoney(z.total)}</li>)}</ul>
            </Alert>
          )}
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Mês</th>{data.monthly.map((m) => <th key={m.month} className="right">{monthName(m.month)}</th>)}</tr></thead>
              <tbody>
                <tr><td>Atual</td>{data.monthly.map((m) => <td key={m.month} className="right small">{fmtMoney(m.before)}</td>)}</tr>
                <tr><td>Planejamento</td>{data.monthly.map((m) => <td key={m.month} className="right small"><strong>{fmtMoney(m.after)}</strong></td>)}</tr>
              </tbody>
            </table>
          </div>
          <div className="inline-controls" role="tablist">
            {([["cc", "Por centro de custo"], ["account", "Por conta"], ["cells", `Célula a célula (${fmtInt(data.cells.length)})`]] as const).map(([k, l]) => (
              <button key={k} type="button" className="btn btn-ghost btn-sm" aria-pressed={tab === k} onClick={() => setTab(k)}>{l}</button>
            ))}
          </div>
          {tab === "cc" && <DiffTable rows={data.by_cost_center} label={(r) => String(r.cost_center)} />}
          {tab === "account" && <DiffTable rows={data.by_account} label={(r) => <>{String(r.name)}<div className="muted small mono">{String(r.account)}</div></>} />}
          {tab === "cells" && (
            <DiffTable
              rows={data.cells.slice(0, 600)}
              label={(r) => <>{String(r.cost_center)} · {String(r.account_name)}<div className="muted small">{monthName(Number(r.month))} · {String(r.account)}</div></>}
            />
          )}
          <label className="planning-reason">
            Motivo (vai para a auditoria)
            <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Ex.: retorno do Planejamento de 10/11/2026" />
          </label>
          <div className="inline-controls">
            <button type="button" className="btn btn-primary" disabled={busy || data.errors.length > 0 || !reason.trim() || data.changed_keys === 0} onClick={confirm}>
              {busy ? "Gravando…" : `Confirmar e gravar na ${data.version.label}`}
            </button>
            <span className="muted small">Depois, os apontamentos ficam em <Link className="link" to="/apontamentos">Apontamentos</Link>.</span>
          </div>
        </div>
      )}
    </Card>
  );
}
