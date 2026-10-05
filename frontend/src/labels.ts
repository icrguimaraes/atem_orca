export const DATASET_LABELS: Record<string, string> = {
  OPEX_TEMPLATE: "Template OPEX preenchido (cadastros + realizado + orçamento 2027)",
  CAPEX_TEMPLATE: "Template CAPEX preenchido (cadastros + catálogo de ativos + solicitações 2027)",
  MASTER_DATA: "Cadastros (filiais, CCs, contas)",
  COST_CENTERS: "Centros de custo",
  ACCOUNTS: "Contas contábeis",
  ACTUAL: "Realizado",
  REFERENCE_BUDGET: "Orçamento de referência",
  EMPLOYEES: "Quadro de funcionários",
  MACRO_ASSUMPTIONS: "Premissas macroeconômicas",
};

export const LAYOUT_LABELS: Record<string, string> = {
  TEMPLATE_BD: "Aba BD-Novo do template",
  TEMPLATE_OPEX: "Template OPEX 2027 (todas as abas)",
  TEMPLATE_CAPEX: "Template CAPEX 2027 (todas as abas)",
  WIDE_MONTHLY: "Planilha mensal (colunas por mês)",
  SAP_KSB1: "Exportação SAP KSB1",
  TEMPLATE_QUADRO: "Quadro de funcionários",
  TEMPLATE_PREMISSAS: "Premissas macroeconômicas",
};

export const IMPORT_STATUS: Record<string, { label: string; tone: Tone }> = {
  UPLOADED: { label: "Na fila", tone: "neutral" },
  VALIDATING: { label: "Validando", tone: "info" },
  VALIDATED: { label: "Aguardando confirmação", tone: "warn" },
  FAILED: { label: "Falhou", tone: "bad" },
  CONFIRMED: { label: "Confirmada", tone: "info" },
  PROCESSING: { label: "Carregando", tone: "info" },
  COMPLETED: { label: "Concluída", tone: "good" },
  REJECTED: { label: "Descartada", tone: "neutral" },
  REVERTED: { label: "Base excluída", tone: "neutral" },
};

export const ROW_STATUS: Record<string, { label: string; tone: Tone }> = {
  VALID: { label: "Válido", tone: "good" },
  WARNING: { label: "Aviso", tone: "warn" },
  ERROR: { label: "Inconsistente", tone: "bad" },
  DUPLICATE: { label: "Duplicado", tone: "bad" },
};

export const CYCLE_STATUS: Record<string, { label: string; tone: Tone }> = {
  DRAFT: { label: "Em preparação", tone: "neutral" },
  OPEN: { label: "Aberto", tone: "good" },
  CLOSED: { label: "Fechado", tone: "bad" },
};

export const ROLE_LABELS: Record<string, string> = {
  ADMIN: "Administrador",
  CONTROLLER: "Controladoria",
  MANAGER: "Gestor de CC",
  PACKAGE_MANAGER: "Gestor de pacote",
  HR: "RH",
  VIEWER: "Consulta",
};

export const NATURE_LABELS: Record<string, string> = {
  OPEX: "OPEX",
  CAPEX: "CAPEX",
  PESSOAL: "Pessoal",
  FINANCEIRO: "Financeiro",
  CUSTO: "Custo",
};

export const PARAM_LABELS: Record<string, string> = {
  "alert.growth_pct": "Alerta de crescimento (vs. realizado anualizado)",
  "alert.reduction_pct": "Alerta de redução (vs. realizado anualizado)",
  "alert.history_band_pct": "Banda em torno da média histórica",
  "alert.min_relevant_amount": "Valor mínimo para alertar conta sem orçamento (R$)",
  "travel.one_way_factor": "Fator da passagem só de ida",
  "capex.min_unit_value": "Valor unitário mínimo para CAPEX (R$)",
  "capex.min_useful_life_months": "Vida útil mínima para CAPEX (meses)",
  "personnel.salary_adjustment_pct": "Reajuste salarial",
  "personnel.adjustment_month": "Mês do reajuste (data-base)",
};

export type Tone = "good" | "warn" | "bad" | "info" | "neutral";

export const fmtInt = (n: number) => n.toLocaleString("pt-BR");
export const fmtMoney = (v: string | number) =>
  Number(v).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
export const fmtDate = (iso: string | null) =>
  iso ? new Date(iso.length === 10 ? `${iso}T12:00:00` : iso).toLocaleDateString("pt-BR") : "—";
export const fmtDateTime = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "—";
export const fmtSize = (bytes: number) =>
  bytes > 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.ceil(bytes / 1024)} KB`;

export const MONTHS = ["JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"];

/** Rótulos e ordem das colunas da prévia de importação. */
export const FIELD_LABELS: Record<string, string> = {
  company: "Empresa",
  branch: "Filial",
  branch_name: "Nome filial",
  code: "Código",
  name: "Nome",
  cost_center: "Centro de custo",
  cost_center_name: "Denominação CC",
  manager: "Gestor",
  account: "Conta",
  account_name: "Descrição conta",
  package: "Pacote",
  detail: "Detalhamento",
  dre_group: "Agrupamento DRE",
  year: "Ano",
  period: "Período",
  values: "Valores mensais",
  amount: "Valor",
  posting_date: "Data lançamento",
  document: "Documento",
  text: "Texto",
  vendor_name: "Fornecedor",
  registration: "Matrícula",
  position: "Cargo",
  salary: "Salário",
  contract: "Contrato",
  action: "Ação",
  action_month: "Mês ação",
  new_salary: "Novo salário",
  benefits: "Benefícios",
  category: "Categoria",
  indicator: "Indicador",
  segment: "Segmento",
  source: "Fonte",
  value: "Valor",
};

export const RECORD_TYPES: Record<string, string> = {
  FACT: "Lançamentos",
  BUDGET_LINE: "Linhas de orçamento 2027",
  CAPEX_ITEM: "Itens de CAPEX 2027",
  TRAVEL: "Viagens 2027",
  ASSET_ITEM: "Catálogo de ativos",
  BRANCH: "Filiais",
  COST_CENTER: "Centros de custo",
  ACCOUNT: "Contas",
  EMPLOYEE: "Colaboradores",
  VACANCY: "Vagas",
  MACRO: "Premissas",
};

/** Valor compacto para eixos e rótulos: R$ 1,2 mi · R$ 350 mil. */
export const fmtCompact = (v: string | number) => {
  const n = Number(v);
  const abs = Math.abs(n);
  if (abs >= 1e9) return `R$ ${(n / 1e9).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} bi`;
  if (abs >= 1e6) return `R$ ${(n / 1e6).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} mi`;
  if (abs >= 1e3) return `R$ ${(n / 1e3).toLocaleString("pt-BR", { maximumFractionDigits: 0 })} mil`;
  return `R$ ${n.toLocaleString("pt-BR", { maximumFractionDigits: 0 })}`;
};

export const fmtPct = (v: string | null | undefined) =>
  v === null || v === undefined
    ? "—"
    : `${Number(v) > 0 ? "+" : ""}${(Number(v) * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`;

export const ERROR_LABELS: Record<string, string> = {
  STRUCTURE: "Estrutura do arquivo",
  REQUIRED: "Campo obrigatório vazio",
  INVALID_CODE: "Código inválido",
  INVALID_NUMBER: "Número inválido",
  INVALID_DATE: "Data inválida",
  INVALID_PERIOD: "Período inválido",
  INVALID_DOMAIN: "Valor fora da lista permitida",
  INVALID_CONTRACT: "Tipo de contrato não parametrizado",
  UNKNOWN_COMPANY: "Empresa não cadastrada",
  UNKNOWN_BRANCH: "Filial não cadastrada",
  UNKNOWN_COST_CENTER: "Centro de custo não cadastrado",
  UNKNOWN_ACCOUNT: "Conta não cadastrada",
  UNKNOWN_COLUMNS: "Colunas não reconhecidas",
  DUPLICATE: "Registro duplicado no arquivo",
  TOTAL_MISMATCH: "Total difere da soma dos meses",
  NO_VALUES: "Linha sem valores",
  NEW_PACKAGE: "Pacote novo será criado",
  NEW_COST_CENTER: "Centro de custo novo será criado",
  NEW_ACCOUNT: "Conta nova será criada",
  NEGATIVE_VALUE: "Valor negativo",
  SPECIAL_PERIOD: "Período especial (13–16)",
  VACANCY: "Vaga sem matrícula",
  NO_COST_CENTER: "Sem centro de custo",
  YEAR_REQUIRED: "Ano não identificado",
  BUDGET_LOCKED: "Orçamento do CC já enviado/aprovado",
  WRONG_NATURE: "Conta de natureza diferente do módulo",
  UNRESOLVED_LINE: "Linha sem CC ou conta identificável",
  CONSOLIDATOR_MISMATCH: "Linhas diferem do consolidador",
  TRAVEL_RECALCULATED: "Viagem recalculada pelas tarifas",
  TRAVEL_NO_VALUES: "Viagem sem valores",
  TRAVEL_NOT_CALCULATED: "Viagem não calculada",
  NEW_ASSET_CLASS: "Classe de ativo nova será criada",
  UNKNOWN_PROJECT_TYPE: "Tipo de projeto não cadastrado",
  CAPEX_SCHEDULE_MISMATCH: "Cronograma difere do valor total",
  CAPEX_BELOW_MIN_VALUE: "Valor unitário abaixo do mínimo CAPEX",
  CAPEX_SHORT_LIFE: "Vida útil curta para CAPEX",
  CAPEX_NO_PROJECT_TYPE: "Projeto sem tipo",
  CAPEX_NO_JUSTIFICATION: "Sem justificativa",
  CAPEX_INVALID_VALUE: "Valor ou quantidade inválidos",
  CAPEX_NO_ITEMS: "Solicitação sem itens",
  NO_CAPEX_ITEMS: "Template sem itens de CAPEX",
  NO_MONTHS: "Cronograma mensal não encontrado",
  NO_BUDGET_VALUES: "Template sem valores de orçamento",
  NO_CYCLE: "Sem ciclo orçamentário",
};

export const SUBMISSION_STATUS: Record<string, { label: string; tone: Tone }> = {
  DRAFT: { label: "Não iniciado", tone: "neutral" },
  IN_PROGRESS: { label: "Em preenchimento", tone: "info" },
  SUBMITTED: { label: "Enviado para validação", tone: "warn" },
  UNDER_REVIEW: { label: "Em análise", tone: "warn" },
  ADJUSTMENT_REQUESTED: { label: "Ajuste solicitado", tone: "bad" },
  APPROVED: { label: "Aprovado", tone: "good" },
  CONSOLIDATED: { label: "Consolidado", tone: "good" },
};

export const REVIEW_STATUS: Record<string, { label: string; tone: Tone }> = {
  PENDING: { label: "Aguardando", tone: "warn" },
  APPROVED: { label: "Validado", tone: "good" },
  ADJUST_REQUESTED: { label: "Ajuste pedido", tone: "bad" },
  COMMENTED: { label: "Comentado", tone: "info" },
};

export const FLAG_LABELS: Record<string, { label: string; tone: Tone; hint: string }> = {
  GROWTH_ABOVE: { label: "Crescimento acima do limite", tone: "bad", hint: "Proposta maior que a referência + limite de crescimento" },
  REDUCTION_ABOVE: { label: "Redução acima do limite", tone: "warn", hint: "Proposta menor que a referência − limite de redução" },
  NEW_ACCOUNT: { label: "Conta sem histórico", tone: "info", hint: "Sem realizado/orçado anterior para esta conta" },
  NO_BUDGET: { label: "Sem orçamento", tone: "warn", hint: "Houve gasto em 2026, mas nada foi orçado para 2027" },
};

export const MOVEMENT_LABELS: Record<string, { label: string; tone: Tone }> = {
  KEEP: { label: "Manter", tone: "neutral" },
  PROMOTION: { label: "Promoção", tone: "info" },
  SALARY_ADJUSTMENT: { label: "Reajuste individual", tone: "info" },
  TERMINATION: { label: "Desligamento", tone: "bad" },
  TRANSFER: { label: "Transferência", tone: "warn" },
  HIRE: { label: "Admissão", tone: "good" },
};
