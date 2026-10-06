import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type ImportBatch, type Page } from "../api";
import { DatasetsCard } from "../components/DatasetsCard";
import { Alert, Badge, Card, Empty, Loading, PageHeader, useLoad } from "../components/ui";
import { DATASET_LABELS, IMPORT_STATUS, fmtDateTime, fmtInt, fmtSize } from "../labels";

const ACTIVE = new Set(["UPLOADED", "VALIDATING", "CONFIRMED", "PROCESSING"]);

const HELP: Record<string, string> = {
  "": "O sistema identifica o tipo pelo conteúdo (abas e cabeçalhos). O template OPEX preenchido é reconhecido automaticamente.",
  CAPEX_TEMPLATE: "Template CAPEX 2027 preenchido: carrega cadastros (BD-Novo), o catálogo de ativos (LISTA ATIVOS) e cada linha da aba Template_Orç como item de CAPEX do centro de custo. Linhas de projeto com o mesmo tipo e justificativa viram uma solicitação; reimportar substitui só o que veio do template.",
  OPEX_TEMPLATE: "Template OPEX 2027 como volta do gestor: carrega cadastros (BD-Novo), o realizado da aba Realizado e os valores das abas I a XII como orçamento 2027 do centro de custo. Reimportar substitui só as linhas que vieram do template.",
  MASTER_DATA: "Aba BD-Novo dos templates ou planilha com Filiais / Centro de Custo / Conta do Razão.",
  ACTUAL: "Layout da aba Realizado (Empresa, Filial, Centro de Custos, Conta Razão e uma coluna por mês) ou KSB1.",
  REFERENCE_BUDGET: "Mesmo layout do realizado, com os valores orçados (ex.: Orçamento 2026).",
  EMPLOYEES: "Aba QUADRO FUNCIONARIOS (matrícula, nome, cargo, CC, salário, ação). As ações (PROMOVER, REMOVER, INCLUIR) e as vagas entram no orçamento de Pessoal do CC. Se o arquivo não tiver a coluna CENTRO DE CUSTO preenchida, informe o CC padrão.",
  MACRO_ASSUMPTIONS: "Aba PREMISSAS MACROECONOMICAS (indicador, fonte, anos).",
};

function UploadForm({ onDone }: { onDone: (id: number) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [type, setType] = useState("");
  const [year, setYear] = useState("");
  const [company, setCompany] = useState("1001");
  const [defaultCc, setDefaultCc] = useState("");
  const [createMissing, setCreateMissing] = useState(false);
  const [mode, setMode] = useState<"MERGE" | "REPLACE">("MERGE");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const financial = type === "ACTUAL" || type === "REFERENCE_BUDGET";

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append("file", file);
    if (type) form.append("dataset_type", type);
    if (year) form.append("reference_year", year);
    if (company) form.append("company_code", company);
    if (defaultCc.trim()) form.append("cost_center_code", defaultCc.trim());
    form.append("create_missing_dimensions", String(createMissing));
    form.append("mode", mode);
    try {
      const batch = await api<ImportBatch>("/imports", { method: "POST", body: form });
      onDone(batch.id);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="upload" onSubmit={submit}>
      <label className="dropzone">
        <input type="file" accept=".xlsx,.xlsm,.csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        {file ? (
          <span>
            <strong>{file.name}</strong> · {fmtSize(file.size)}
          </span>
        ) : (
          <span>
            <strong>Clique para escolher o arquivo</strong> (.xlsx, .xlsm ou .csv)
          </span>
        )}
      </label>
      <div className="form-row">
        <label>
          Tipo de dado
          <select value={type} onChange={(e) => setType(e.target.value)}>
            <option value="">Detectar automaticamente</option>
            {["OPEX_TEMPLATE", "CAPEX_TEMPLATE", "MASTER_DATA", "ACTUAL", "REFERENCE_BUDGET", "EMPLOYEES", "MACRO_ASSUMPTIONS"].map((t) => (
              <option key={t} value={t}>
                {DATASET_LABELS[t]}
              </option>
            ))}
          </select>
        </label>
        <label>
          Empresa padrão
          <input value={company} onChange={(e) => setCompany(e.target.value)} placeholder="1001" />
        </label>
        <label>
          Ano de referência
          <input value={year} onChange={(e) => setYear(e.target.value)} placeholder="se não houver nos cabeçalhos" inputMode="numeric" />
        </label>
        {(type === "" || type === "EMPLOYEES") && (
          <label>
            CC padrão do quadro de pessoal
            <input value={defaultCc} onChange={(e) => setDefaultCc(e.target.value)} placeholder="ex.: 1050101011 (linhas sem CC)" inputMode="numeric" />
          </label>
        )}
      </div>
      <p className="muted small">{HELP[type] ?? ""}</p>
      {(financial || type === "") && (
        <div className="mode-choice">
          <span className="muted small">Para realizado e orçamento de referência:</span>
          <label className="check">
            <input type="radio" name="mode" checked={mode === "MERGE"} onChange={() => setMode("MERGE")} />
            <span>
              <strong>Atualizar</strong> — grava as linhas do arquivo e mantém o que já existe para os demais CCs e contas
              (recomendado para cargas parciais).
            </span>
          </label>
          <label className="check">
            <input type="radio" name="mode" checked={mode === "REPLACE"} onChange={() => setMode("REPLACE")} />
            <span>
              <strong>Substituir</strong> — a base da empresa no ano passa a ser exatamente este arquivo (use só com o
              realizado completo).
            </span>
          </label>
        </div>
      )}
      {financial && (
        <label className="check">
          <input type="checkbox" checked={createMissing} onChange={(e) => setCreateMissing(e.target.checked)} />
          Cadastrar automaticamente centros de custo e contas que ainda não existem
        </label>
      )}
      {error && <Alert>{error}</Alert>}
      <div>
        <button className="btn btn-primary" disabled={!file || busy}>
          {busy ? "Enviando…" : "Enviar e validar"}
        </button>
      </div>
    </form>
  );
}

export default function Imports() {
  const navigate = useNavigate();
  const { data, error, reload } = useLoad(() => api<Page<ImportBatch>>("/imports?limit=100"));
  const busy = data?.items.some((b) => ACTIVE.has(b.status));

  useEffect(() => {
    if (!busy) return;
    const t = setInterval(reload, 2500);
    return () => clearInterval(t);
  }, [busy, reload]);

  return (
    <>
      <PageHeader
        title="Importação de dados"
        subtitle="Envie planilhas atualizadas. Nada é gravado antes da sua confirmação, e cada carga gera uma nova versão sem apagar o histórico."
      />
      <Card title="Novo arquivo">
        <UploadForm onDone={(id) => navigate(`/importacoes/${id}`)} />
      </Card>
      <DatasetsCard refreshKey={data?.items.filter((b) => b.status === "COMPLETED").length ?? 0} onChanged={reload} />
      <Card title="Histórico de importações">
        {error && <Alert>{error}</Alert>}
        {!data ? (
          <Loading />
        ) : data.items.length === 0 ? (
          <Empty>Nenhuma importação ainda.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Arquivo</th>
                  <th>Tipo</th>
                  <th>Status</th>
                  <th className="right">Registros</th>
                  <th className="right">Válidos</th>
                  <th className="right">Inconsist.</th>
                  <th>Enviado em</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((b) => (
                  <tr key={b.id}>
                    <td className="muted">{b.id}</td>
                    <td>
                      <Link className="link" to={`/importacoes/${b.id}`}>
                        {b.file_name}
                      </Link>
                    </td>
                    <td>{DATASET_LABELS[b.dataset_type ?? ""] ?? "—"}</td>
                    <td>
                      <Badge tone={IMPORT_STATUS[b.status]?.tone ?? "neutral"}>{IMPORT_STATUS[b.status]?.label ?? b.status}</Badge>
                    </td>
                    <td className="right">{fmtInt(b.total_rows)}</td>
                    <td className="right">{fmtInt(b.valid_rows)}</td>
                    <td className="right">{fmtInt(b.error_rows + b.duplicate_rows)}</td>
                    <td>{fmtDateTime(b.created_at)}</td>
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
