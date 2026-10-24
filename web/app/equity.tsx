"use client";

import { useState } from "react";
import { loadEquity, useAnalytics, type AnalyticsProps } from "./analytics-api";
import AnalyticsFilters from "./analytics-filters";
import { AnalyticsSkeleton, ErrorAlert } from "./ui-feedback";

export default function Equity(props: AnalyticsProps) {
  const { filters, setFilters, options, optionsError, data, error, retry } = useAnalytics(props, loadEquity);
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  if (!props.active) return null;
  // Pagination slices presentation only. Counts and all comparisons come from
  // the API's complete filtered response; no salary calculation happens here.
  const pages = data ? Math.ceil(data.items.length / size) : 0;
  const currentPage = Math.min(page, pages || 1);
  const items = data?.items.slice((currentPage - 1) * size, currentPage * size) || [];
  return <section aria-labelledby="equity-heading"><div className="section-heading"><div><p className="eyebrow">Peer median comparisons</p><h2 id="equity-heading">Pay equity</h2></div>
    <button className="secondary" onClick={retry}>Refresh equity</button></div>
    <p>Peers span the full saved country and level group, excluding the employee themselves. Department filters select targets only and do not shrink their peers.</p>
    <AnalyticsFilters prefix="equity" filters={filters} options={options} error={optionsError} onChange={(value) => { setFilters(value); setPage(1); }} onRetry={retry} />
    {error ? <ErrorAlert message={error}><button onClick={retry}>Retry pay equity</button></ErrorAlert> : !data ? <AnalyticsSkeleton equity /> : <>
      <dl className="metric-grid equity-counts"><div className="metric-card"><dt>Selected targets</dt><dd>{data.total_count}</dd></div><div className="metric-card"><dt>Available comparisons</dt><dd>{data.available_count}</dd></div>
        <div className="metric-card"><dt>Excluded Unbanded targets</dt><dd>{data.excluded_unbanded_count}</dd></div><div className="metric-card"><dt>Insufficient peers</dt><dd>{data.insufficient_peers_count}</dd></div>
        <div className="metric-card"><dt>Zero peer median</dt><dd>{data.zero_peer_median_count}</dd></div></dl>
      <div className="results-toolbar"><p>All totals describe the full filtered target population.</p><div className="page-size"><label htmlFor="equity-size">Equity rows per page</label>
        <select id="equity-size" value={size} onChange={(event) => { setSize(Number(event.target.value)); setPage(1); }}>{[25, 50, 100].map((value) => <option key={value}>{value}</option>)}</select></div></div>
      {data.total_count === 0 ? <div className="state"><h3>No equity targets</h3><p>Try another filter selection.</p></div> : <div className="table-scroll" role="region" aria-label="Equity comparison results" tabIndex={0}><table>
        <caption className="sr-only">Each target compared with other eligible employees in their full country and level group</caption><thead><tr>
          {["Employee ID", "Name", "Department", "Country", "Level", "Currency", "Annual salary", "Band status", "Peers", "Peer median", "Percentage difference", "State"].map((label) => <th scope="col" key={label}>{label}</th>)}
        </tr></thead><tbody>{items.map((row) => <tr key={row.employee_id}><td>{row.employee_id}</td><td>{row.name}</td><td>{row.department}</td><td>{row.country}</td><td>{row.level}</td><td>{row.currency}</td>
          <td className="money">{row.salary_amount}</td><td>{row.band_status}</td><td>{row.peer_count}</td><td className="money">{row.peer_median === null ? "—" : row.peer_median.amount}</td>
          <td className="money">{row.percentage_difference === null ? "—" : `${row.percentage_difference}%`}</td><td>{row.state_label}</td></tr>)}</tbody></table></div>}
      <nav className="pagination" aria-label="Equity pages"><span>{pages ? `Page ${currentPage} of ${pages}` : "No pages"}</span><div className="actions">
        <button className="secondary" disabled={currentPage <= 1} onClick={() => setPage(currentPage - 1)}>Previous equity page</button>
        <button className="secondary" disabled={currentPage >= pages} onClick={() => setPage(currentPage + 1)}>Next equity page</button></div></nav>
    </>}
  </section>;
}
