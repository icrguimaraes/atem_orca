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
  if (!resp.ok) throw new ApiError(resp.status, `Erro ${resp.status}`);
  const url = URL.createObjectURL(await resp.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: fileName });
  a.click();
  URL.revokeObjectURL(url);
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

export interface Company { id: number; code: string; name: string; short_name: string | null; is_active: boolean }
export interface Branch { id: number; company_id: number; code: string; name: string; uf: string | null; is_active: boolean }
export interface CostCenter {
  id: number; company_id: number; code: string; name: string; manager_user_id: number | null;
  manager_name: string | null; is_csc: boolean; is_backoffice: boolean; is_active: boolean;
}
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

export interface Comparison {
  kind: "FINANCIAL" | "MASTER" | "EMPLOYEES" | "MACRO" | "NONE";
  no_changes: boolean;
  mode?: "MERGE" | "REPLACE";
  scopes?: ScopeComparison[];
  by_type?: Record<string, { CREATE: number; UPDATE: number; UNCHANGED: number }>;
  new?: number; changed?: number; unchanged?: number;
}

export interface Ranked { id: number; code: string | null; name: string | null; ref_ytd: string; prev_ytd: string; ytd_var_pct: string | null }
export interface PackageRow {
  package_id: number | null; package: string; package_type: number | null; prev_total: string; prev_ytd: string;
  ref_ytd: string; ref_annualized: string; budget: string; ytd_var_pct: string | null;
}
export interface Overview {
  reference_year: number; previous_year: number; last_closed_period: number | null; years_loaded: number[];
  budget_years: number[]; available_years: number[]; has_actual: boolean; has_prev: boolean; has_budget: boolean;
  kpis: {
    prev_total: string; prev_ytd: string; ref_ytd: string; ytd_var_pct: string | null; ref_annualized: string;
    annualized_vs_prev_pct: string | null; budget_total: string; budget_ytd: string; budget_consumption_pct: string | null;
  };
  monthly: { month: number; prev: string; ref: string; budget: string }[];
  by_package: PackageRow[];
  top_cost_centers: Ranked[];
  top_accounts: Ranked[];
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
