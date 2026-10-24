"use client";

import { useEffect, useRef, type ReactNode } from "react";

export function ErrorAlert({ message, children }: { message: string; children?: ReactNode }) {
  const alert = useRef<HTMLDivElement>(null);
  useEffect(() => { alert.current?.focus(); }, [message]);
  return <div ref={alert} role="alert" tabIndex={-1} className="error"><p>{message}</p>{children}</div>;
}

export function AnalyticsSkeleton({ equity = false, columns = equity ? 12 : 9 }: { equity?: boolean; columns?: number }) {
  return <div className="analytics-skeleton" role="status" aria-label={equity ? "Loading equity comparisons" : "Loading salary summaries"} aria-busy="true">
    <span className="sr-only">{equity ? "Loading equity comparisons…" : "Loading salary summaries…"}</span>
    <div className="metric-grid" aria-hidden="true">{Array.from({ length: equity ? 5 : 3 }, (_, index) => <div className="metric-card" key={index}><span className="skeleton skeleton-label" /><span className="skeleton skeleton-number" /></div>)}</div>
    {!equity && <div className="metric-grid" aria-hidden="true">{[0, 1, 2].map((index) => <div className="metric-card" key={index}><span className="skeleton skeleton-label" /><span className="skeleton skeleton-number" /><span className="skeleton skeleton-label" /></div>)}</div>}
    <div className="table-scroll" aria-hidden="true"><table><thead><tr>{Array.from({ length: columns }, (_, index) => <th key={index}><span className="skeleton skeleton-label" /></th>)}</tr></thead>
      <tbody>{Array.from({ length: 6 }, (_, row) => <tr key={row}>{Array.from({ length: columns }, (_, column) => <td key={column}><span className="skeleton skeleton-cell" /></td>)}</tr>)}</tbody></table></div>
  </div>;
}
