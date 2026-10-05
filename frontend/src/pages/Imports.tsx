import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type ImportBatch, type Page } from "../api";
import { Alert, Badge, Card, Empty, Loading, PageHeader, useLoad } from "../components/ui";
import { DATASET_LABELS, IMPORT_STATUS, fmtDateTime, fmtInt, fmtSize } from "../labels";

const ACTIVE = new Set(["UPLOADED", "VALIDATING", "CONFIRMED", "PROCESSING"]);

const HELP: Record<string, string> = {
  "": "O sistema identifica o tipo pelo conteúdo (abas e cabeçalhos).",
  MASTER_DATA: "Aba BD-Novo dos templates ou planilha com Filiais / Centro de Custo / Conta do Razão.",
  ACTUAL: "Layout da aba Realizado (Empresa, Filial, Centro de Custos, Conta Razão e uma coluna por mês) ou KSB1.",
  REFERENCE_BUDGET: "Mesmo layout do realizado, com os valores orçados (ex.: Orçamento 2026).",
  EMPLOYEES: "Aba QUADRO FUNCIONARIOS (matrícula, nome, cargo, CC, salário, ação).",
  MACRO_ASSUMPTIONS: "Aba PREMISSAS MACROECONOMICAS (indicador, fonte, anos).",
};

function UploadForm({ onDone }: { onDone: (id: number) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [type, setType] = useState("");
  const [year, setYear] = useState("");
  const [company, setCompany] = useState("1001");
  const [createMissing, setCreateMissing] = useState(false);
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
    form.append("create_missing_dimensions", String(createMissing));
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
            {["MASTER_DATA", "ACTUAL", "REFERENCE_BUDGET", "EMPLOYEES", "MACRO_ASSUMPTIONS"].map((t) => (
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
      </div>
      <p className="muted small">{HELP[type] ?? ""}</p>
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
