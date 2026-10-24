export type Session = { email: string; csrf_token: string };
export type Employee = {
  employee_id: string; name: string; email: string; department: string;
  country: string; level: string; salary_amount: string; salary_minor: string;
  currency: string; band_status: string; revision: number; etag: string;
};
export type FilterOptions = {
  countries: string[]; levels: string[]; departments: { value: string; label: string }[];
};
export type DirectoryPage = {
  items: Employee[]; total: number; page: number; page_size: number; total_pages: number; ordering: string;
};
type ValidationDetail = { loc?: (string | number)[]; field?: string; msg?: string; message?: string };
type ErrorDetail = { code?: string; message?: string; errors?: ValidationDetail[] };

export class ApiError extends Error {
  fields: Record<string, string> = {};
  constructor(public status: number, public detail: string | ErrorDetail | ValidationDetail[]) {
    super(status === 401 ? "Your session expired. Sign in again." :
      typeof detail === "string" ? detail : Array.isArray(detail) ? "Please check the highlighted fields." :
      detail.message || "The request failed. Please try again.");
    const errors = Array.isArray(detail) ? detail : typeof detail === "object" ? detail.errors || [] : [];
    for (const error of errors) {
      const field = error.field || error.loc?.at(-1);
      if (typeof field === "string") this.fields[field] = error.msg || error.message || "Invalid value";
    }
  }
}

export async function api<T>(path: string, init: RequestInit = {}, timeoutMs = 20_000): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init, cache: "no-store", signal: init.signal ? AbortSignal.any([init.signal, AbortSignal.timeout(timeoutMs)]) : AbortSignal.timeout(timeoutMs),
  });
  const body = await response.json();
  if (!response.ok) throw new ApiError(response.status, body.detail || "The request failed. Please try again.");
  return body as T;
}
export function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Unable to reach the API. Please try again.";
}
export function recordPath(employee: Employee) {
  return `/employees/records/${encodeURIComponent(employee.employee_id)}`;
}
export function mutationHeaders(session: Session, employee?: Employee) {
  return { "content-type": "application/json", "x-csrf-token": session.csrf_token,
    ...(employee ? { "if-match": employee.etag } : {}) };
}
