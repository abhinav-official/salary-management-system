import { useEffect, useState } from "react";
import { api, ApiError, errorMessage, type FilterOptions } from "./employee-api";

export type AnalyticsProps = { active: boolean; refreshKey: number; onExpired: () => void };
export type Filters = { country: string; department: string; level: string };
type Statistic = { minor_numerator: string; minor_denominator: string; amount: string };
export type SummaryGroup = { country: string; currency: string; count: number; state: string; department?: string; department_key?: string; level?: string;
  average: Statistic | null; median: Statistic | null; minimum: Statistic | null; maximum: Statistic | null; spread: Statistic | null };
export type Summaries = { total_count: number; countries: SummaryGroup[]; departments: SummaryGroup[]; levels: SummaryGroup[] };
export type Compa = { total_count: number; eligible_count: number; excluded_unbanded_count: number; state: string;
  buckets: { key: string; label: string; count: number; percentage: string | null }[] };
export type SummaryData = { summaries: Summaries; compa: Compa };
export type EquityItem = { employee_id: string; name: string; department: string; country: string; level: string; currency: string;
  salary_amount: string; band_status: string; state: string; state_label: string; peer_count: number; peer_median: Statistic | null; percentage_difference: string | null };
export type EquityData = { total_count: number; available_count: number; excluded_unbanded_count: number; insufficient_peers_count: number;
  zero_peer_median_count: number; peer_scope: string; items: EquityItem[] };

export async function loadSummary(params: string, signal: AbortSignal): Promise<SummaryData> {
  const [summaries, compa] = await Promise.all([api<Summaries>(`/analytics/summaries?${params}`, { signal }), api<Compa>(`/analytics/compa-ratio?${params}`, { signal })]);
  return { summaries, compa };
}
export function loadEquity(params: string, signal: AbortSignal) { return api<EquityData>(`/analytics/equity?${params}`, { signal }, 120_000); }

export function useAnalytics<T>({ active, refreshKey, onExpired }: AnalyticsProps, load: (params: string, signal: AbortSignal) => Promise<T>) {
  const [filters, setFilters] = useState<Filters>({ country: "", department: "", level: "" });
  const [version, setVersion] = useState(0);
  const [result, setResult] = useState<{ key: string; value: T } | null>(null);
  const [failure, setFailure] = useState<{ key: string; message: string } | null>(null);
  const [options, setOptions] = useState<FilterOptions | null>(null);
  const [optionsError, setOptionsError] = useState("");
  const params = new URLSearchParams(Object.entries(filters).filter(([, value]) => value !== "")).toString();
  const key = `${params}:${refreshKey}:${version}`;
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    void load(params, controller.signal).then((value) => { if (!controller.signal.aborted) { setResult({ key, value }); setFailure(null); } }).catch((cause) => {
      if (controller.signal.aborted) return;
      if (cause instanceof ApiError && cause.status === 401) onExpired(); else setFailure({ key, message: errorMessage(cause) });
    });
    return () => controller.abort();
  }, [active, params, key, load, onExpired]);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    void api<FilterOptions>("/employees/filter-options", { signal: controller.signal }).then((value) => {
      if (!controller.signal.aborted) { setOptions(value); setOptionsError(""); }
    }).catch((cause) => {
      if (controller.signal.aborted) return;
      if (cause instanceof ApiError && cause.status === 401) onExpired(); else setOptionsError(errorMessage(cause));
    });
    return () => controller.abort();
  }, [active, refreshKey, version, onExpired]);
  return { filters, setFilters, options, optionsError, data: result?.key === key ? result.value : null,
    error: failure?.key === key ? failure.message : "", retry: () => setVersion((value) => value + 1) };
}
