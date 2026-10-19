import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { api, download, type TraceEntries, type TraceLevel, type TraceRow, type TraceTree } from "../api";
import { useAuth } from "../auth";
import { fmtMoney, fmtShare } from "../labels";
import { RecordRow } from "../pages/Trace";
import { Empty, Loading } from "./ui";

/* Rastro em painel lateral (19/10/2026, "abrir igual à tela do por quê?"): o "rastro" da tabela do Painel abre aqui, já no
   recorte da linha, e desce área → setor → centro de custo → pacote → conta → lançamento (com a Denominação do KSB1) sem
   sair do Painel. A trilha fica no próprio painel; "Abrir na página do Rastro" leva o mesmo recorte para /rastro. */

const LEVELS: TraceLevel[] = ["department", "area", "cost_center", "package", "account"];
const LEVEL_LABELS: Record<TraceLevel, string> = { department: "Área", area: "Setor", cost_center: "Centro de custo", package: "Pacote", account: "Conta" };
const PLURAL: Record<string, string> = { Área: "Áreas", Setor: "Setores", "Centro de custo": "Centros de custo", Pacote: "Pacotes", Conta: "Contas", Lançamento: "Lançamentos" };
const PARENT: Record<TraceLevel, [string, string]> = {
  department: ["parent_department_id", "parent_no_department"],
  area: ["parent_area_id", "parent_no_area"],
  cost_center: ["parent_cost_center_id", ""],
  package: ["parent_package_id", "parent_no_package"],
  account: ["parent_account_id", ""],
};
const TRAIL_KEYS = LEVELS.flatMap((l) => PARENT[l].filter(Boolean));
const PAGE = 50;

/** Separa a trilha (parent_*) dos filtros da página (anos, meses, tipo, empresa…). */
function split(qs: string): { trail: [string, string][]; filters: string } {
  const p = new URLSearchParams(qs);
  const trail: [string, string][] = [];
  const rest = new URLSearchParams();
  p.forEach((v, k) => (TRAIL_KEYS.includes(k) ? trail.push([k, v]) : rest.append(k, v)));
  return { trail, filters: rest.toString() };
}

export function TraceDrawer({ qs, label, onClose }: { qs: string; label: string; onClose: () => void }) {
  const { user, can } = useAuth();
  const isController = can("CONTROLLER");
  const initial = split(qs);
  const [trail, setTrail] = useState<[string, string][]>(initial.trail);
  const filters = initial.filters;
  const [tree, setTree] = useState<TraceTree | null>(null);
  const [entries, setEntries] = useState<TraceEntries | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showEntries, setShowEntries] = useState(false);
  const [search, setSearch] = useState("");
  const [needle, setNeedle] = useState("");
  const [offset, setOffset] = useState(0);

  const trailQs = trail.map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");
  const fullQs = [trailQs, filters].filter(Boolean).join("&");
  const chosen = LEVELS.filter((l) => PARENT[l].some((k) => k && trail.some(([t]) => t === k)));
  const level = LEVELS.find((l) => !chosen.includes(l)) ?? null;
  const leaf = level === null;
  const wantEntries = leaf || showEntries;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  useEffect(() => {
    const t = window.setTimeout(() => setNeedle(search.trim()), 300);
    return () => window.clearTimeout(t);
  }, [search]);
  useEffect(() => setOffset(0), [fullQs, needle]);
  useEffect(() => {
    let alive = true;
    setError(null);
    if (level) api<TraceTree>(`/trace/tree?level=${level}&${fullQs}`).then((d) => alive && setTree(d)).catch((e: Error) => alive && setError(e.message));
    else setTree(null);
    return () => { alive = false; };
  }, [level, fullQs]);
  useEffect(() => {
    let alive = true;
    if (!wantEntries) { setEntries(null); return; }
    setEntries(null);
    api<TraceEntries>(`/trace/entries?offset=${offset}&limit=${PAGE}${needle ? `&search=${encodeURIComponent(needle)}` : ""}&${fullQs}`)
      .then((d) => alive && setEntries(d))
      .catch((e: Error) => alive && setError(e.message));
    return () => { alive = false; };
  }, [wantEntries, fullQs, needle, offset]);

  function open(row: TraceRow) {
    if (!level) return;
    const [byId, none] = PARENT[level];
    if (row.id === null) {
      if (none) setTrail([...trail, [none, "true"]]);
    } else setTrail([...trail, [byId, String(row.id)]]);
    setShowEntries(false);
  }
  function backTo(idx: number) {
    // mantém os níveis da trilha até idx (exclusive)
    const keep = chosen.slice(0, idx).flatMap((l) => PARENT[l].filter(Boolean));
    setTrail(trail.filter(([k]) => keep.includes(k)));
  }

  const info = tree ?? entries;
  const crumbs = info?.trail ?? [];
  const t = tree && tree.level === level ? tree : null;
  const rows = t ? [...t.rows].sort((a, b) => Number(b.value) - Number(a.value)) : [];
  const period = info?.period;

  return createPortal(
    <div className="drawer-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside className="drawer wd td" role="dialog" aria-modal="true" aria-label="Rastro">
        <header className="wd-head">
          <div className="wd-head-main">
            <div className="wd-eyebrow">Rastro · do total ao lançamento</div>
            <h2 className="wd-title">{label}</h2>
            {info && <div className="wd-period">{info.series_label}{period?.years?.length ? ` · ${period.years.join(" + ")}` : ""}</div>}
          </div>
          <button type="button" className="wd-close" onClick={onClose} aria-label="Fechar">✕</button>
        </header>
        <div className="wd-body">
          {error && <div className="error-text">{error}</div>}
          <nav className="breadcrumb td-crumbs" aria-label="Trilha do rastro">
            {crumbs.length === 0 && <span className="muted small">Recorte da linha</span>}
            {crumbs.map((c, i) => (
              <span key={c.level}>
                {i > 0 && <span className="muted"> › </span>}
                <button type="button" className="btn-link" onClick={() => backTo(i + 1)} disabled={i === crumbs.length - 1 && leaf} title={`Voltar para ${LEVEL_LABELS[c.level]}`}>
                  {c.label}: {c.code ? `${c.code} · ` : ""}{c.name}
                </button>
              </span>
            ))}
            {level && <span className="muted small"> › {LEVEL_LABELS[level]}</span>}
          </nav>

          {level && (
            <section className="wd-sec">
              {!t ? <Loading /> : !rows.length ? <Empty>Sem valores neste recorte.</Empty> : (
                <>
                  <div className="section-tools">
                    <span className="muted small">
                      {LEVEL_LABELS[level]} · total <strong>{fmtMoney(t.total)}</strong> · {t.count} {(t.count === 1 ? LEVEL_LABELS[level] : PLURAL[LEVEL_LABELS[level]]).toLowerCase()} · clique para descer até {t.next_label.toLowerCase()}
                    </span>
                    <button type="button" className="btn btn-ghost btn-sm" onClick={() => setShowEntries((v) => !v)}>
                      {showEntries ? "Ocultar lançamentos" : "Ver lançamentos"}
                    </button>
                  </div>
                  <div className="table-wrap">
                    <table className="table drill-table">
                      <thead>
                        <tr>
                          <th>{LEVEL_LABELS[level]}</th>
                          <th className="right">{t.series_label}</th>
                          <th className="right">AV %</th>
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((r) => {
                          const clickable = r.id !== null || Boolean(PARENT[level][1]);
                          return (
                            <tr key={r.id ?? "none"}>
                              <td>
                                {clickable ? <button type="button" className="row-filter" onClick={() => open(r)}>{r.name}</button> : r.name}
                                {r.code && <span className="muted small mono"> {r.code}</span>}
                              </td>
                              <td className="right nowrap">{fmtMoney(r.value)}</td>
                              <td className="right nowrap muted">{fmtShare(r.share)}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </section>
          )}

          {wantEntries && (
            <section className="wd-sec">
              <div className="section-tools">
                <span className="muted small">
                  {entries ? <>{entries.count} lançamento{entries.count === 1 ? "" : "s"} somando <strong>{fmtMoney(entries.total)}</strong>{needle && " (busca ativa)"}</> : "Lançamentos"}
                </span>
                <input type="search" className="drill-search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Buscar descrição, fornecedor, documento…" aria-label="Buscar lançamentos" />
              </div>
              {!entries ? <Loading /> : !entries.items.length ? <Empty>Nenhum lançamento neste recorte.</Empty> : (
                <>
                  <div className="table-wrap">
                    <table className="table">
                      <thead>
                        <tr><th>Tipo</th><th>Centro de custo</th><th>Conta</th><th>Lançamento</th><th>Mês</th><th className="right">Valor</th><th>Origem</th><th>Abrir</th></tr>
                      </thead>
                      <tbody>
                        {entries.items.map((r) => <RecordRow key={r.id} r={r} isController={isController} pj={(user?.pj_access ?? "NONE") !== "NONE"} />)}
                      </tbody>
                    </table>
                  </div>
                  {entries.count > PAGE && (
                    <div className="pager">
                      <span className="muted small">{offset + 1}–{Math.min(offset + PAGE, entries.count)} de {entries.count}</span>
                      <button type="button" className="btn btn-ghost btn-sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Anteriores</button>
                      <button type="button" className="btn btn-ghost btn-sm" disabled={offset + PAGE >= entries.count} onClick={() => setOffset(offset + PAGE)}>Próximos</button>
                    </div>
                  )}
                </>
              )}
            </section>
          )}

          <footer className="td-foot">
            <Link to={`/rastro?${fullQs}${showEntries ? "&lancamentos=1" : ""}`} onClick={onClose}>Abrir na página do Rastro</Link>
            {wantEntries && entries && entries.count > 0 && (
              <button type="button" className="btn-link" onClick={() => download(`/trace/entries.xlsx?${[needle ? `search=${encodeURIComponent(needle)}` : "", fullQs].filter(Boolean).join("&")}`, "rastro.xlsx")}>
                Baixar Excel
              </button>
            )}
          </footer>
        </div>
      </aside>
    </div>,
    document.body,
  );
}
