import { ApiError } from "./employee-api";

export type Issue = { sheet: string; row: number | null; field: string; code: string; message: string };
export type Worksheet = { name: string; header_row: number | null; data_rows: number; candidate: boolean; header_errors: Issue[]; formula_count: number };
export type Inspection = { worksheets: Worksheet[]; selected_worksheet: string | null; selection_required: boolean; source_digest: string };
export type Preview = { preview_id: string; worksheet: string; owner: string; status: "clean" | "invalid"; created_at: number; expires_at: number;
  total_rows: number; valid_count: number; invalid_count: number; error_count: number; unbanded_count: number; valid_unbanded_count: number };
export type PreviewRow = { sheet: string; row_number: number; valid: boolean; values: { employee_id: string | null; name: string | null; email: string | null; department: string | null; country: string | null; level: string | null };
  salary_amount: string | null; salary_input: string | null; currency: string | null; band_status: string | null; rounding_applied: boolean | null;
  errors: Issue[]; raw_cells: Record<string, unknown> };
export type PageData<T> = { preview_id: string; status: string; items: T[]; total: number; page: number; page_size: number; total_pages: number };
export type Receipt = { preview_id: string; actor: string; completed_at: number; inserted_count: number; employee_ids: string[] };
export function codeOf(cause: unknown): string | undefined {
  return cause instanceof ApiError && typeof cause.detail === "object" && !Array.isArray(cause.detail) ? cause.detail.code : undefined;
}
export function previewPath(id: string) { return `/imports/previews/${encodeURIComponent(id)}`; }
export function dateText(seconds: number) { return new Date(seconds * 1000).toLocaleString(); }
