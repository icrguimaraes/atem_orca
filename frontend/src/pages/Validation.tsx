import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  api,
  type Finding,
  type Findings as FindingsData,
  type ValidationFile,
  type ValidationOverview,
  type ValidationSheet,
} from "../api";
import { Alert, Badge, Card, Empty, Loading, PageHeader, SearchBox, useLoad } from "../components/ui";
import { DATASET_LABELS, FIELD_LABELS, MONTHS, RECORD_TYPES, fmtDateTime, fmtInt, fmtMoney, type Tone } from "../labels";

const STATUS: Record<string, { label: string; tone: Tone; cls: string }> = {
  VALID: { label: "Lida", tone: "good", cls: "row-valid" },
  WARNING: { label: "Lida com aviso", tone: "warn", cls: "row-warning" },
  ERROR: { label: "Não entrou (erro)", tone: "bad", cls: "row-error" },
  DUPLICATE: { label: "Não entrou (duplicada)", tone: "bad", cls: "row-error" },
};
const WORST = ["ERROR", "DUPLICATE", "WARNING", "VALID"];
// campos que não ajudam a conferir a linha (ids internos e marcadores)
const HIDDEN = new Set(["company_id", "branch_id", "cost_center_id", "account_id", "keyless", "from_lines", "package_hint"]);
// nomes em português dos campos que não estão em FIELD_LABELS
const EXTRA_LABELS: Record<string, string> = {
  supplier: "Fornecedor",
  contract_manager: "Gestor do contrato",
  description: "Detalhamento",
  justification: "Justificativa",
  account_detail: "Produto/serviço",
  assumption: "Premissa",
  travel: "Viagem",
  amounts: "Valores por conta",
  item: "Item",
  quantity: "Quantidade",
  unit_value: "Valor unitário",
  file_total: "Total no arquivo",
  is_project: "Projeto?",
  project_type: "Tipo de projeto",
  action: "Ação",
  action_month: "Mês da ação",
  new_salary: "Novo salário",
  new_position: "Novo cargo",
  cc_from_position: "CC pelo cargo (setor)",
};

/** Letra da coluna como no Excel (1 → A, 27 → AA). */
function colName(n: number): string {
  let s = "";
  while (n > 0) {
    const r = (n - 1) % 26;
    s = String.fromCharCode(65 + r) + s;
    n = Math.floor((n - 1) / 26);
  }
  return s;
}

/** Valor como aparece na célula: números sem separador de milhar quando inteiros (códigos de CC/conta) e com até 4
 *  casas quando quebrados (centavos "quebrados" aparecem como estão); datas em dd/mm/aaaa. */
function cellText(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "number") {
    if (Number.isInteger(v)) return String(v);
    return v.toLocaleString("pt-BR", { maximumFractionDigits: 4 });
  }
  if (typeof v === "boolean") return v ? "VERDADEIRO" : "FALSO";
  const s = String(v);
  const iso = /^(\d{4})-(\d{2})-(\d{2})(T00:00:00)?$/.exec(s);
  if (iso) return `${iso[3]}/${iso[2]}/${iso[1]}`;
  return s;
}

function fieldText(key: string, v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (key === "values" && typeof v === "object") return "";
  if (typeof v === "object") {
    const entries = Object.entries(v as Record<string, unknown>);
    return entries.length ? entries.map(([k, x]) => `${k}: ${typeof x === "object" ? JSON.stringify(x) : x}`).join(" · ") : "—";
  }
  return String(v);
}

function worst(statuses: string[]): string | null {
  for (const s of WORST) if (statuses.includes(s)) return s;
  return null;
}

export default function Validation() {
  const { id } = useParams();
  return id ? <FileView id={Number(id)} /> : <FileList />;
}

/** Arquivos importados (versão vigente de cada um). */
function FileList() {
  const { data, error } = useLoad(() => api<ValidationFile[]>("/validation/files"));
  const [q, setQ] = useState("");
  if (error) return <Alert>{error}</Alert>;
  if (!data) return <Loading />;
  const term = q.trim().toLowerCase();
  const shown = data.filter((f) => !term || `${f.file_name} ${f.cost_centers.join(" ")} ${f.uploaded_by ?? ""}`.toLowerCase().includes(term));
  return (
    <>
      <PageHeader
        title="Validação"
        subtitle="Cada planilha como foi enviada, ao lado do que o sistema leu em cada linha. Abra um arquivo para conferir aba por aba; nos Apontamentos, “Ver no Excel” abre direto na linha."
      />
      <Card title={`Arquivos importados · ${fmtInt(shown.length)}`} actions={<SearchBox value={q} onChange={setQ} placeholder="Buscar arquivo, CC ou pessoa…" />}>
        {shown.length === 0 ? (
          <Empty>Nenhum arquivo de template importado.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr><th>Arquivo</th><th>Centros de custo</th><th className="right">Linhas lidas</th><th>Enviado</th><th /></tr>
              </thead>
              <tbody>
                {shown.map((f) => (
                  <tr key={f.id}>
                    <td>
                      <Link className="link" to={`/validacao/${f.id}`}>{f.file_name}</Link>
                      <div className="muted small">{DATASET_LABELS[f.dataset_type] ?? f.dataset_type} · importação #{f.id}{f.older_versions ? ` · ${f.older_versions} versão(ões) anterior(es)` : ""}</div>
                    </td>
                    <td className="small">{f.cost_centers.length ? f.cost_centers.join(", ") : <span className="muted">—</span>}</td>
                    <td className="right nowrap">
                      <span title="válidas">{fmtInt(f.valid_rows - f.warning_rows)}</span>
                      {f.warning_rows > 0 && <> · <Badge tone="warn">{fmtInt(f.warning_rows)} aviso(s)</Badge></>}
                      {f.error_rows > 0 && <> · <Badge tone="bad">{fmtInt(f.error_rows)} erro(s)</Badge></>}
                    </td>
                    <td className="small nowrap">{f.created_at ? fmtDateTime(f.created_at) : "—"}<div className="muted">{f.uploaded_by ?? ""}</div></td>
                    <td><Link className="btn btn-sm" to={`/validacao/${f.id}`}>Abrir</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}

/** Um arquivo: abas, a planilha como está e o painel "como o sistema leu". */
function FileView({ id }: { id: number }) {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const target = Number(params.get("row") || 0) || null;
  const overview = useLoad(() => api<ValidationOverview>(`/validation/${id}`), [id]);
  const findings = useLoad(() => api<FindingsData>("/findings"));
  const [showAll, setShowAll] = useState(false);
  const [onlyRead, setOnlyRead] = useState(false);
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<number | null>(target);

  const sheets = overview.data?.sheets ?? [];
  const relevant = sheets.filter((s) => s.records > 0 || s.missing_formulas > 0);
  const tabs = showAll || relevant.length === 0 ? sheets : relevant;
  const sheetName = params.get("sheet") || tabs[0]?.name || "";
  const sheet = useLoad(
    () => (sheetName ? api<ValidationSheet>(`/validation/${id}/sheet?name=${encodeURIComponent(sheetName)}`) : Promise.resolve(null)),
    [id, sheetName],
  );

  // apontamentos abertos deste arquivo, por aba e linha
  const openFindings = useMemo(() => {
    const out = new Map<string, Finding[]>();
    for (const f of findings.data?.items ?? []) {
      const s = f.source;
      if (!s || s.batch_id !== id) continue;
      const k = `${s.sheet}|${s.row}`;
      out.set(k, [...(out.get(k) ?? []), f]);
    }
    return out;
  }, [findings.data, id]);
  const findingsBySheet = useMemo(() => {
    const out = new Map<string, number>();
    for (const [k, list] of openFindings) out.set(k.split("|")[0], (out.get(k.split("|")[0]) ?? 0) + list.length);
    return out;
  }, [openFindings]);

  const gridRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    setSelected(target);
  }, [target, sheetName]);
  useEffect(() => {
    if (!target || !sheet.data) return;
    const el = gridRef.current?.querySelector(`[data-row="${target}"]`);
    el?.scrollIntoView({ block: "center" });
  }, [target, sheet.data]);

  if (overview.error) return <Alert>{overview.error}</Alert>;
  if (!overview.data) return <Loading />;
  const o = overview.data;
  const s = sheet.data;
  const missing = new Set((s?.missing ?? []).map(([r, c]) => `${r}:${c}`));
  const term = q.trim().toLowerCase();
  const rows = (s?.rows ?? []).filter((r) => {
    if (onlyRead && !s?.marks[String(r.n)]) return false;
    if (term && !r.cells.some((c) => cellText(c).toLowerCase().includes(term))) return false;
    return true;
  });
  // só as colunas com algum valor (as letras continuam as do Excel; um traço marca colunas vazias escondidas)
  const usedCols = (() => {
    if (!s) return [] as number[];
    const used = new Set<number>();
    for (const r of s.rows) r.cells.forEach((c, i) => { if (cellText(c) !== "") used.add(i); });
    for (const [, c] of s.missing) used.add(c - 1);
    return [...used].sort((a, b) => a - b);
  })();
  const gapBefore = (k: number) => k > 0 && usedCols[k] - usedCols[k - 1] > 1;
  const mark = selected && s ? s.marks[String(selected)] : undefined;
  const selFindings = selected ? openFindings.get(`${sheetName}|${selected}`) ?? [] : [];
  const selMissing = selected && s ? (s.missing.filter(([r]) => r === selected).map(([, c]) => colName(c))) : [];
  const pickSheet = (name: string) => setParams({ sheet: name });
  const pickRow = (n: number) => setSelected((cur) => (cur === n ? null : n));

  return (
    <>
      <PageHeader
        title="Validação"
        subtitle={`${o.file_name} · ${DATASET_LABELS[o.dataset_type ?? ""] ?? o.dataset_type ?? ""} · importação #${o.id}${o.created_at ? ` · ${fmtDateTime(o.created_at)}` : ""}`}
        actions={
          <>
            <button type="button" className="btn btn-ghost" onClick={() => navigate("/validacao")}>Todos os arquivos</button>
            <Link className="btn btn-ghost" to={`/importacoes/${o.id}`}>Prévia da importação</Link>
          </>
        }
      />

      <div className="validation-summary">
        <span><strong>{fmtInt(o.total_rows)}</strong> linhas lidas</span>
        <span className="vs-dot vs-valid" /> <span>{fmtInt(o.valid_rows - o.warning_rows)} válidas</span>
        <span className="vs-dot vs-warning" /> <span>{fmtInt(o.warning_rows)} com aviso</span>
        <span className="vs-dot vs-error" /> <span>{fmtInt(o.error_rows)} não entraram</span>
        <span className="vs-dot vs-missing" /> <span>{fmtInt(sheets.reduce((a, x) => a + x.missing_formulas, 0))} células com fórmula sem valor</span>
      </div>

      <div className="tabs validation-tabs">
        {tabs.map((t) => (
          <button key={t.name} type="button" className={t.name === sheetName ? "active" : ""} onClick={() => pickSheet(t.name)} title={`${t.rows} linhas no arquivo`}>
            {t.name}
            {t.records > 0 && <span className={`count${t.errors ? " count-bad" : ""}`}>{fmtInt(t.records)}</span>}
            {(findingsBySheet.get(t.name) ?? 0) > 0 && <span className="count count-warn" title="apontamentos abertos">⚑ {fmtInt(findingsBySheet.get(t.name) ?? 0)}</span>}
          </button>
        ))}
        {relevant.length > 0 && relevant.length < sheets.length && (
          <button type="button" className="tabs-more" onClick={() => setShowAll(!showAll)}>
            {showAll ? "Só abas lidas" : `+ ${sheets.length - relevant.length} aba(s) não lida(s)`}
          </button>
        )}
      </div>

      <div className="validation-toolbar">
        <SearchBox value={q} onChange={setQ} placeholder="Procurar nesta aba…" />
        <label className="check"><input type="checkbox" checked={onlyRead} onChange={(e) => setOnlyRead(e.target.checked)} /> Só linhas lidas pelo sistema</label>
        <span className="validation-legend">
          <span className="legend-chip row-valid">lida</span>
          <span className="legend-chip row-warning">com aviso</span>
          <span className="legend-chip row-error">não entrou</span>
          <span className="legend-chip cell-missing">fórmula sem valor</span>
          <span className="legend-chip row-finding">⚑ apontamento aberto</span>
        </span>
      </div>

      <div className="validation-layout">
        <div className="sheet-frame" ref={gridRef}>
          {sheet.error && <Alert>{sheet.error}</Alert>}
          {!s && !sheet.error && <Loading />}
          {s && rows.length === 0 && <Empty>Nada para mostrar nesta aba com esses filtros.</Empty>}
          {s && rows.length > 0 && (
            <table className="sheet-grid">
              <thead>
                <tr>
                  <th className="corner" />
                  {usedCols.map((ci, k) => <th key={ci} className={gapBefore(k) ? "gap" : undefined} title={gapBefore(k) ? `colunas ${colName(usedCols[k - 1] + 2)}–${colName(ci)} vazias` : undefined}>{colName(ci + 1)}</th>)}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const m = s.marks[String(r.n)];
                  const st = m ? worst([...m.records.map((x) => x.status), ...m.issues.map((x) => (x.severity === "ERROR" ? "ERROR" : "WARNING"))]) : null;
                  const hasFinding = openFindings.has(`${sheetName}|${r.n}`);
                  const cls = [st ? STATUS[st]?.cls : "", hasFinding ? "row-finding" : "", selected === r.n ? "row-selected" : "", target === r.n ? "row-target" : ""].filter(Boolean).join(" ");
                  return (
                    <tr key={r.n} data-row={r.n} className={cls} onClick={() => pickRow(r.n)}>
                      <th className="rownum">{hasFinding && <span className="flag">⚑</span>}{r.n}</th>
                      {usedCols.map((i, k) => {
                        const c = r.cells[i];
                        const miss = missing.has(`${r.n}:${i + 1}`);
                        const txt = cellText(c);
                        return (
                          <td key={i} className={`${typeof c === "number" ? "num" : ""}${miss ? " cell-missing" : ""}${gapBefore(k) ? " gap" : ""}`} title={miss ? "Fórmula sem valor calculado (abra no Excel e recalcule)" : txt.length > 24 ? txt : undefined}>
                            {miss && !txt ? "∅" : txt}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
          {s?.truncated && <p className="muted small">Mostrando as primeiras {fmtInt(s.rows.length)} linhas com conteúdo.</p>}
        </div>

        <aside className="row-panel">
          {!selected ? (
            <div className="muted small">Clique numa linha da planilha para ver como o sistema a leu: centro de custo, conta, valores por mês e as ocorrências da importação.</div>
          ) : (
            <>
              <div className="row-panel-head">
                <strong>{sheetName} · linha {selected}</strong>
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => setSelected(null)}>Fechar</button>
              </div>
              {selMissing.length > 0 && (
                <Alert tone="warn">Fórmula sem valor nas colunas {selMissing.join(", ")}: o arquivo foi salvo sem recalcular. Abra no Excel, Ctrl+Alt+F9, salve e importe de novo.</Alert>
              )}
              {selFindings.length > 0 && (
                <div className="row-panel-block">
                  <h4>Apontamentos abertos</h4>
                  {selFindings.map((f) => (
                    <div key={f.key} className="row-finding-item">
                      <Badge tone={f.severity === "CRITICAL" ? "bad" : "warn"}>{f.kind_label}</Badge>
                      <div className="small">{f.subject}</div>
                      <div className="muted small">{f.message}</div>
                      <Link className="link small" to={`/apontamentos?cc=${f.cost_center_id}`}>Corrigir em Apontamentos →</Link>
                    </div>
                  ))}
                </div>
              )}
              {!mark ? (
                <p className="muted small">Linha não lida pelo sistema: cabeçalho, instrução, totalizador ou linha sem valores para 2027.</p>
              ) : (
                <>
                  {mark.records.map((rec, k) => {
                    const st = STATUS[rec.status] ?? { label: rec.status, tone: "neutral" as Tone };
                    const values = (rec.data.values ?? null) as Record<string, string> | null;
                    const total = values ? Object.values(values).reduce((a, v) => a + Number(v || 0), 0) : null;
                    const fields = Object.entries(rec.data).filter(([key, v]) => !HIDDEN.has(key) && key !== "values" && v !== null && v !== "" && !(typeof v === "object" && v !== null && Object.keys(v).length === 0));
                    return (
                      <div key={k} className="row-panel-block">
                        <div className="row-panel-title">
                          <Badge tone={st.tone}>{st.label}</Badge>
                          <span className="muted small">{RECORD_TYPES[rec.record_type] ?? rec.record_type}</span>
                        </div>
                        <dl className="row-fields">
                          {fields.map(([key, v]) => (
                            <div key={key}><dt>{FIELD_LABELS[key] ?? EXTRA_LABELS[key] ?? key}</dt><dd>{fieldText(key, v)}</dd></div>
                          ))}
                        </dl>
                        {values && Object.keys(values).length > 0 && (
                          <div className="row-months">
                            {MONTHS.map((mName, i) => {
                              const v = values[String(i + 1)];
                              return <div key={mName} className={v && Number(v) ? "" : "zero"}><span>{mName}</span><strong>{v && Number(v) ? fmtMoney(v) : "—"}</strong></div>;
                            })}
                            <div className="total"><span>Total</span><strong>{fmtMoney(String(total ?? 0))}</strong></div>
                          </div>
                        )}
                      </div>
                    );
                  })}
                  {mark.issues.length > 0 && (
                    <div className="row-panel-block">
                      <h4>Ocorrências da importação</h4>
                      <ul className="row-issues">
                        {mark.issues.map((x, k) => (
                          <li key={k}><Badge tone={x.severity === "ERROR" ? "bad" : "warn"}>{x.severity === "ERROR" ? "Erro" : "Aviso"}</Badge> {x.message}{x.column ? <span className="muted"> · {x.column}</span> : null}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </>
              )}
            </>
          )}
        </aside>
      </div>
    </>
  );
}
