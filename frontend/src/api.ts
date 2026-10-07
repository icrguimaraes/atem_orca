const TOKEN_KEY = "atem.token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* navegação privada: token fica só em memória */
  }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function errorMessage(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail))
      return detail.map((d) => `${(d.loc || []).slice(-1)[0] ?? ""}: ${d.msg}`).join("; ");
  }
  return `Erro ${status}`;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const resp = await fetch(`/api/v1${path}`, { ...init, headers });
  if (resp.status === 401 && !path.startsWith("/auth/login")) {
    setToken(null);
    window.location.assign("/login");
  }
  if (!resp.ok) {
    let body: unknown = null;
    try {
      body = await resp.json();
    } catch {
      /* corpo vazio */
    }
    throw new ApiError(resp.status, errorMessage(body, resp.status));
  }
  if (resp.status === 204) return undefined as T;
  return resp.json() as Promise<T>;
}

export async function download(path: string, fileName: string) {
  const resp = await fetch(`/api/v1${path}`, { headers: { Authorization: `Bearer ${getToken()}` } });
  if (!resp.ok) {
    // o servidor devolve JSON com `detail` mesmo em rotas de arquivo
    const body = await resp.json().catch(() => null);
    throw new ApiError(resp.status, errorMessage(body, resp.status));
  }
  const url = URL.createObjectURL(await resp.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: fileName });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

// ---------------------------------------------------------------- tipos

export interface User {
  id: number;
  email: string;
  name: string;
  is_active: boolean;
  roles: string[];
  last_login_at: string | null;
}

// acesso a centros de custo (Usuários → Acessos; GET /users traz o resumo, /users/{id}/access o detalhe)
/** Resumo da lista: perfil global (todos os CCs) ou CCs distintos como gestor + escopos. */
export interface AccessSummary { is_global: boolean; cost_centers: number; managed: number; scopes: number }
export interface UserListItem extends User { access: AccessSummary }
export interface AccessCostCenter { id: number; code: string; name: string; company: string; is_active: boolean }
/** Escopo atribuído: um CC ou a empresa inteira (`cost_centers` = quantos CCs a linha libera). */
export interface UserScope {
  id: number; kind: "COST_CENTER" | "COMPANY" | "EMPTY"; cost_center_id: number | null; company_id: number | null;
  code: string | null; name: string; company: string | null; is_active: boolean; cost_centers: number;
}
export interface UserAccess {
  user_id: number; is_global: boolean; cost_centers: number; managed: AccessCostCenter[]; scopes: UserScope[];
}

export interface Company { id: number; code: string; name: string; short_name: string | null; is_active: boolean }
export interface Branch { id: number; company_id: number; code: string; name: string; uf: string | null; is_active: boolean }
export interface CostCenter {
  id: number; company_id: number; code: string; name: string; manager_user_id: number | null;
  manager_name: string | null; is_csc: boolean; is_backoffice: boolean; is_active: boolean;
  department_id?: number | null; area_id?: number | null;  // área e setor (página Áreas e setores)
}
/** Área (Controladoria, Tributos…) — tabela `departments`. */
export interface Department { id: number; name: string }
/** Setor (Fiscal, Contabilidade…) dentro de uma área — tabela `areas`. */
export interface Sector { id: number; name: string; department_id: number | null }
export interface Package {
  id: number; code: string; name: string; roman: string | null; package_type: number; nature: string;
  form_type: string; sort_order: number; is_active: boolean;
}
export interface Account {
  id: number; code: string; name: string; dre_group: string | null; nature: string; package_id: number | null;
  is_active: boolean;
}
export interface Cycle {
  id: number; fiscal_year: number; name: string; status: string; actual_reference_year: number;
  opex_deadline: string | null; capex_deadline: string | null; personnel_deadline: string | null;
}
export interface Parameter { key: string; value: unknown; description: string | null }
export interface Version { id: number; label: string; status: string; reason: string | null; created_at: string }

export interface ImportBatch {
  id: number; dataset_type: string | null; layout: string | null; file_name: string; file_size: number;
  status: string; options: Record<string, unknown> | null; total_rows: number; valid_rows: number;
  warning_rows: number; error_rows: number; duplicate_rows: number; summary: Record<string, any> | null;
  error_message: string | null; created_at: string; validated_at: string | null; completed_at: string | null;
}
export interface ImportRow { row_number: number; sheet: string | null; record_type: string; status: string; data: Record<string, any> }
export interface ErrorGroup { code: string; severity: string; count: number; example: string }
export interface Preview { batch: ImportBatch; errors_by_code: ErrorGroup[]; rows: ImportRow[] }
export interface DatasetVersion {
  id: number; dataset_type: string; scope_key: string; version_number: number; import_batch_id: number | null;
  source: string; is_current: boolean; created_at: string; row_count: number; last_closed_period: number | null;
}
export interface AuditLog {
  id: number; occurred_at: string; user_id: number | null; action: string; entity_type: string;
  entity_id: string | null; before: Record<string, unknown> | null; after: Record<string, unknown> | null;
  reason: string | null;
}
export interface Page<T> { total: number; items: T[] }

export interface ImportErrorItem {
  sheet: string | null; row_number: number | null; column: string | null; code: string; severity: string;
  message: string; value: string | null;
}

export interface ScopeComparison {
  scope: string; year: number; company: string; current_version: number | null; new: number; changed: number;
  unchanged: number; absent: number; absent_action: "KEEP" | "REMOVE"; absent_total: string; current_total: string;
  after_total: string; difference: string; absent_samples: string[];
}

export interface BudgetImportScope {
  cost_center: string; company: string; status: string; editable: boolean; lines: number; total: string;
  replaces_lines: number; replaces_total: string;
}

export interface Comparison {
  kind: "FINANCIAL" | "MASTER" | "EMPLOYEES" | "MACRO" | "NONE" | "TEMPLATE";
  master?: Comparison;
  actual?: Comparison | null;
  budget?: BudgetImportScope[];
  module?: "CAPEX";
  catalog?: { new: number; existing: number };
  movements?: { cost_center: string; name: string; status: string; editable: boolean; actions: number; hires: number }[];
  no_changes: boolean;
  mode?: "MERGE" | "REPLACE";
  scopes?: ScopeComparison[];
  by_type?: Record<string, { CREATE: number; UPDATE?: number; IGNORED?: number; UNCHANGED: number }>;
  new?: number; changed?: number; unchanged?: number;
  template?: boolean; updates_ignored?: number; skipped_scopes?: string[];
}

export interface Ranked { id: number; code: string | null; name: string | null; ref_ytd: string; prev_ytd: string; ytd_var_pct: string | null }
export interface PackageRow {
  package_id: number | null; package: string; package_type: number | null; prev_total: string; prev_ytd: string;
  ref_ytd: string; ref_annualized: string; budget: string; ytd_var_pct: string | null;
}
export interface ModuleKpi { module: string; label: string; main: string; base: string; var_pct: string | null; unscheduled: string }
export interface Overview {
  by_module: ModuleKpi[];
  reference_year: number; previous_year: number | null; selected_years: number[]; selected_months: number[];
  last_closed_period: number | null; years_loaded: number[]; target_year: number | null; period: PeriodInfo;
  budget_years: number[]; available_years: number[]; has_actual: boolean; has_prev: boolean; has_budget: boolean;
  kpis: {
    prev_total: string; prev_ytd: string; ref_ytd: string; actual_total: string; ytd_var_pct: string | null; ref_annualized: string;
    annualized_vs_prev_pct: string | null; budget_total: string; budget_ytd: string; budget_consumption_pct: string | null;
    budget_unscheduled: string;
  };
  monthly: { month: number; prev: string; ref: string; budget: string }[];
  top_cost_centers: Ranked[];
  top_accounts: Ranked[];
  heatmap: { year: number; years: number[]; rows: { id: number; code: string | null; name: string; values: string[]; total: string }[] };
  budget_progress: BudgetProgress | null;
}

/** Período do painel: anos somados × meses, comparação opcional com o ano anterior. */
export interface PeriodInfo {
  years: number[]; years_label: string; months: number[]; modules: string[]; main: "actual" | "budget"; main_label: string;
  actual_label: string | null; budget_label: string | null; closed: number | null; closed_month: string | null;
  compare: boolean; compare_available: boolean; same_period: boolean; same_period_available: boolean; prev_years: number[];
  base_kind: "prev" | "budget" | null; base_label: string | null; annualized_base: boolean; target_year: number | null;
}
export interface BreakdownRow {
  id: number | null; code: string | null; name: string; ref: string; base: string; share_ref: string | null;
  share_base: string | null; var: string; var_pct: string | null; has_children: boolean;
}
export interface Breakdown {
  group_by: "department" | "area" | "package" | "account" | "cost_center"; reference_year: number; previous_year: number | null;
  last_closed_period: number | null; period: PeriodInfo; base: "prev" | "budget" | null; base_label: string | null; main_label: string;
  thresholds: { growth: number; reduction: number }; rows: BreakdownRow[];
  total: { ref: string; base: string; var: string; var_pct: string | null };
}

export interface BudgetProgress {
  target_year: number; ref_year: number; cycle_status: string; deadline: string | null; total_cost_centers: number;
  started_cost_centers: number; status_counts: Record<string, number>; proposed_total: string;
  annualized_started_total: string;
  by_package: { package_id: number | null; package: string; proposed: string; ref_annualized: string }[];
}
export interface QualityCheck {
  code: string; title: string; severity: "ERROR" | "WARNING" | "INFO" | "OK"; count: number; detail: string | null;
  samples: string[];
}

export interface DatasetInfo {
  dataset_type: string; scope_key: string; current_version: number | null; versions: number; rows: number;
  last_loaded_at: string | null; last_file_name: string | null; deletable: boolean;
}

export interface Inventory {
  master: {
    cost_centers: number; cost_centers_without_user: number; accounts_by_nature: Record<string, number>;
    packages: { package: string; package_type: number; accounts: number }[];
  };
  personnel: {
    headcount: number; monthly_payroll: string; monthly_estimated_cost: string; annual_estimated_cost: string;
    by_contract: { contract: string; headcount: number; payroll: string; multiplier: string }[];
    by_cost_center: { code: string | null; name: string; headcount: number; payroll: string }[];
  };
  macro: {
    years: number[];
    rows: { category: string; indicator: string; segment: string | null; source: string | null; reference_date: string | null; values: Record<string, string> }[];
  };
}

// ---------------------------------------------------------------- OPEX

export interface OpexAction { action: string; label: string; requires_comment: boolean }
export interface PackageReviewInfo {
  package_id: number; package: string; status: string; comment: string | null; reviewer: string | null;
  updated_at: string | null; can_review: boolean;
}
export interface OpexHeader {
  submission_id: number; status: string; status_label: string;
  cost_center: { id: number; code: string; name: string; company_id: number; company_code: string; manager_name: string | null };
  cycle: { id: number; name: string; status: string; deadline: string | null };
  version: string;
  years: { prev: number; ref: number; target: number };
  permissions: { edit: boolean; owner: boolean; global: boolean; review_packages: number[]; cycle_blocked: boolean; frozen?: boolean };
  actions: OpexAction[];
  package_reviews: PackageReviewInfo[];
  submitted_at: string | null;
}
export interface OpexAccountRow {
  account_id: number; code: string; name: string; package_id: number | null; package: string | null;
  package_type: number | null; prev_actual: string; ref_actual_ytd: string; ref_annualized: string; ref_budget: string;
  proposed: string; variation_base: string; variation_pct: string | null; flags: string[];
  needs_justification: boolean; justification: string | null; ref_monthly: string[] | null;
}
export interface OpexAccounts {
  prev_year: number; ref_year: number; target_year: number; closed_period: number | null;
  accounts: OpexAccountRow[]; totals: Record<string, string>; pending_justifications: number;
  monthly: { prev: string[]; ref: string[]; budget: string[]; proposed: string[] };
}
export interface OpexLine {
  id: number; account_id: number; package_id: number | null; branch_id: number | null; account_detail_id: number | null;
  line_type: "GENERIC" | "TRAVEL" | "EVENT"; group_ref: string | null; description: string | null;
  justification: string | null; supplier: string | null; contract_manager: string | null;
  attributes: Record<string, any> | null; values: Record<string, string>; total: string; updated_at: string | null;
}
export interface OpexOptions {
  packages: { id: number; name: string; roman: string | null; package_type: number; form_type: string }[];
  accounts: { id: number; code: string; name: string; package_id: number | null; details: { id: number; name: string }[] }[];
  branches: { id: number; code: string; name: string; company_id: number }[];
  lookups: Record<string, { code: string; label: string; extra: Record<string, any> | null }[]>;
  params: { one_way_factor: string };
}
export interface OpexSummaryRow {
  cost_center_id: number; code: string; name: string; company_code: string; manager_name: string | null;
  has_manager_user: boolean; submission_id: number | null; status: string; status_label: string;
  submitted_at: string | null; prev_actual: string; ref_annualized: string; proposed: string; variation_pct: string | null;
}
export interface OpexSummary {
  cycle: { name: string; status: string; deadline: string | null };
  years: { prev: number; ref: number; target: number };
  status_counts: Record<string, number>;
  rows: OpexSummaryRow[];
  progress: BudgetProgress | null;
}
export interface ReviewQueueItem {
  submission_id: number; cost_center_id: number; cost_center: string; package_id: number; package: string;
  review_status: string; submitted_at: string | null; package_total: string;
}
export interface WorkflowEventItem {
  action: string; from_status: string | null; to_status: string; to_label: string; comment: string | null;
  user: string | null; version: string | null; created_at: string;
}

// ---------------------------------------------------------------- CAPEX

export interface CapexIssue { code: string; severity: "CRITICAL" | "WARNING"; message: string }
export interface CapexItem {
  id: number; project_id: number; account_id: number; account_code: string | null; account_name: string | null;
  asset_item_id: number | null; item_name: string; description: string | null; unit_value: string; quantity: string;
  total_value: string; useful_life_months: number | null; values: Record<string, string>; scheduled: string;
  difference: string; issues: CapexIssue[];
}
export interface CapexProject {
  id: number; code: string; branch_id: number | null; is_project: boolean; project_type_code: string | null;
  title: string; description: string | null; justification: string | null; expected_cost_reduction: string | null;
  expected_revenue: string | null; priority: string | null; budget_prev_year: string | null; observations: string | null;
  source: "TEMPLATE" | "SYSTEM"; items: CapexItem[]; total: string; issues: CapexIssue[]; updated_at: string | null;
}
export interface CapexView {
  prev_year: number; ref_year: number; target_year: number;
  projects: CapexProject[];
  accounts: { account_id: number; code: string; name: string; prev_actual: string; ref_actual: string; ref_budget: string; proposed: string }[];
  monthly: string[];
  by_type: { label: string; total: string }[];
  totals: {
    proposed: string; scheduled: string; projects_total: string; requests: number; projects: number; items: number;
    prev_actual: string; ref_actual: string; ref_budget: string;
  };
  issues: { critical: number; warning: number };
}
export interface CapexHeader {
  submission_id: number; status: string; status_label: string;
  cost_center: { id: number; code: string; name: string; company_id: number; company_code: string; manager_name: string | null };
  cycle: { id: number; name: string; status: string; deadline: string | null };
  version: string;
  years: { prev: number; ref: number; target: number };
  permissions: { edit: boolean; owner: boolean; global: boolean; cycle_blocked: boolean; frozen?: boolean };
  actions: OpexAction[];
  submitted_at: string | null;
}
export interface CapexOptions {
  accounts: { id: number; code: string; name: string }[];
  asset_items: { id: number; name: string; asset_class: string | null; account_id: number | null }[];
  branches: { id: number; code: string; name: string; company_id: number }[];
  lookups: Record<string, { code: string; label: string }[]>;
  params: { min_unit_value: string; min_useful_life_months: number };
}
export interface CapexSummaryRow {
  cost_center_id: number; code: string; name: string; company_code: string; manager_name: string | null;
  submission_id: number | null; status: string; status_label: string; requests: number; projects: number;
  items: number; total: string; ref_actual: string; critical: number;
}
export interface CapexSummary {
  cycle: { name: string; status: string; deadline: string | null };
  years: { prev: number; ref: number; target: number };
  status_counts: Record<string, number>;
  rows: CapexSummaryRow[];
  monthly: string[];
  by_account: { code: string; label: string; total: string }[];
  by_type: { label: string; total: string }[];
}

// ---------------------------------------------------------------- Pessoal

export interface PersonnelScenario {
  id: number | null; name: string; salary_adjustment_pct: string; adjustment_month: number;
  multipliers: Record<string, string>; ignored_multiplier_for: string[]; is_baseline?: boolean;
}
export interface PersonnelTotals {
  monthly: string[]; salary_monthly: string[]; charges_monthly: string[]; headcount: number[]; annual: string;
  salary_total: string; charges_total: string; severance_total: string; headcount_start: number; headcount_end: number;
  hires: number; terminations: number; transfers_out: number; transfers_in: number; promotions: number;
}
export interface PersonnelMovementInfo {
  id: number; type: string; label: string; month: number | null; pending?: string[]; new_salary: string | null; new_position: string | null;
  target_cost_center_id: number | null; target_cost_center: string | null; severance_cost: string | null;
  contract_type_code: string | null; quantity: number; reason: string | null; source: "TEMPLATE" | "SYSTEM";
}
export interface PersonnelPosition {
  kind: "EMPLOYEE" | "HIRE" | "TRANSFER_IN"; key: string; employee_id: number | null; registration: string | null;
  name: string; position: string | null; contract_type_code: string; base_salary: string | null;
  movement: PersonnelMovementInfo | null; from_cost_center: string | null; monthly: string[]; headcount: number[];
  severance: string; annual: string;
}
export interface PersonnelView {
  prev_year: number; ref_year: number; target_year: number; scenario: PersonnelScenario;
  positions: PersonnelPosition[]; totals: PersonnelTotals; actual: { prev: string; ref_ytd: string; ref_annualized: string };
  by_contract: { label: string; total: string }[]; benefits: { code: string; name: string; employees: number }[];
}
export interface PersonnelHeader {
  submission_id: number; status: string; status_label: string;
  cost_center: { id: number; code: string; name: string; company_id: number; company_code: string; manager_name: string | null };
  cycle: { id: number; name: string; status: string; deadline: string | null };
  version: string; years: { prev: number; ref: number; target: number };
  permissions: { edit: boolean; owner: boolean; global: boolean; reviewer: boolean; cycle_blocked: boolean; frozen?: boolean };
  actions: OpexAction[];
  package_review: { package: string; status: string; comment: string | null; reviewer: string | null; updated_at: string | null; can_review: boolean } | null;
  submitted_at: string | null;
}
export interface PersonnelOptions {
  contract_types: { code: string; name: string; apply_multiplier: boolean }[];
  positions: string[];
  cost_centers: { id: number; code: string; name: string; company_id: number }[];
  scenario: PersonnelScenario;
}
export interface PersonnelSummaryRow {
  cost_center_id: number; code: string; name: string; company_code: string; manager_name: string | null;
  submission_id: number | null; status: string; status_label: string; headcount_start: number; headcount_end: number;
  hires: number; terminations: number; annual: string; ref_annualized: string; variation_pct: string | null;
}
export interface PersonnelSummary {
  cycle: { name: string; status: string; deadline: string | null };
  years: { prev: number; ref: number; target: number };
  scenario: PersonnelScenario; status_counts: Record<string, number>; rows: PersonnelSummaryRow[];
  totals: PersonnelTotals; ref_annualized: string; terminations_by_month: number[]; hires_by_month: number[];
  hires_by_position: { label: string; count: number }[];
}
export interface WhatIfResult {
  baseline: PersonnelScenario; simulated: PersonnelScenario; base: PersonnelTotals; simulation: PersonnelTotals;
  difference: string; difference_pct: string | null; monthly_impact: string[];
  by_cost_center: { cost_center_id: number; code: string | null; name: string; base: string; simulated: string; difference: string }[];
  by_contract: { contract: string; people: number; base: string; simulated: string; difference: string }[];
}

// ---------------------------------------------------------------- Consolidação

export interface ModuleTotals { label: string; proposed: string; prev_actual: string; ref_annualized: string; ref_budget: string; monthly: string[]; unscheduled: string }
export interface VersionInfo { id: number; label: string; status: string; reason: string | null; frozen_at: string | null; created_at: string | null; current: boolean }
export interface VariationRow {
  account: string; name: string | null; module: string; package: string | null; prev_actual: string; ref_actual_ytd: string;
  ref_annualized: string; ref_budget: string; proposed: string; variation: string; variation_pct: string | null; flags: string[];
}
export interface ConsolidationOverview {
  cycle: { id: number; name: string; status: string };
  years: { prev: number; ref: number; target: number };
  version: { id: number; label: string; status: string };
  versions: VersionInfo[];
  modules: Record<"OPEX" | "CAPEX" | "PERSONNEL", ModuleTotals>;
  total: string; ref_total: string; prev_total: string;
  by_package: { label: string; proposed: string; ref_annualized: string }[];
  by_company: { company: string; total: string }[];
  status_counts: Record<string, Record<string, number>>;
  matrix: {
    cost_center_id: number; code: string; name: string; company_code: string; manager_name: string | null;
    status: Record<string, string>; totals: Record<string, string>; total: string;
  }[];
  variations: VariationRow[];
  flag_counts: Record<string, number>;
  personnel_accounts: Record<string, { code: string; name: string }>;
}
export interface AttentionPoint {
  severity: "high" | "medium" | "low" | "info"; module: string; module_label: string; kind: string;
  cost_center_id: number | null; cost_center: string | null; message: string; link: string | null;
}
export interface FindingFix {
  type: "schedule" | "account" | "text" | "project_type" | "ticket" | "money" | "month" | "sector" | "cost_center" | "confirm";
  total?: string; values?: Record<string, string>; account_id?: number; account?: string; label?: string; route?: string;
  options?: { id: number; label: string }[];
}
export interface Finding {
  key: string; entity: string; entity_id: string; submission_id: number | null;
  severity: "CRITICAL" | "WARNING"; module: string; module_label: string; kind: string; kind_label: string;
  cost_center_id: number; cost_center: string; sector?: string | null; status: string; subject: string; detail: string | null;
  message: string; amount: string | null; link: string | null;
  fix: FindingFix | null; can_keep: boolean; editable: boolean;
}
export interface FindingReviewItem {
  id: number; action: "CORRECTED" | "KEPT"; action_label: string; kind: string; kind_label: string; severity: string;
  module: string; module_label: string; cost_center_id: number; cost_center: string; subject: string; message: string;
  note: string | null; user: string | null; created_at: string; link: string | null;
}
export interface Findings {
  version: string; target_year: number; items: Finding[];
  counts: { critical: number; warning: number; cost_centers: number };
  reviews: FindingReviewItem[];
  cost_centers: { id: number; code: string; label: string; opex_submission_id: number | null; capex_submission_id: number | null }[];
  can_review: boolean; project_types: { value: string; label: string }[]; sectors: { id: number; label: string }[];
}
export interface VersionCompare {
  from: { id: number; label: string; status: string; total: string };
  to: { id: number; label: string; status: string; total: string };
  difference: string; difference_pct: string | null; monthly_difference: string[];
  modules: { module: string; label: string; from: string; to: string; difference: string; difference_pct: string | null }[];
  cost_centers: { cost_center_id: number; code: string; label: string | null; from: string; to: string; difference: string; difference_pct: string | null }[];
  accounts: { cost_center_id: number; cost_center: string; module: string; account: string; label: string | null; from: string; to: string; difference: string; difference_pct: string | null }[];
  changed_accounts: number;
}
