"use client";

import { useState } from "react";
import { loadSummary, useAnalytics, type AnalyticsProps } from "./analytics-api";
import AnalyticsFilters from "./analytics-filters";
import { AnalyticsSkeleton, ErrorAlert } from "./ui-feedback";

export default function SalarySummary(props: AnalyticsProps) {
  const { filters, setFilters, options, optionsError, data, error, retry } = useAnalytics(props, loadSummary);
  const [grouping, setGrouping] = useState<"countries" | "departments" | "levels">("countries");
  if (!props.active) return null;
  return <section aria-labelledby="summary-heading"><div className="section-heading"><div><p className="eyebrow">Full population insights</p><h2 id="summary-heading">Salary summaries</h2></div>
    <button className="secondary" onClick={retry}>Refresh summaries</button></div>
    <p>These filters are independent of the directory. Summaries include Unbanded employees and keep each country and currency separate.</p>
    <AnalyticsFilters prefix="summary" filters={filters} options={options} error={optionsError} onChange={setFilters} onRetry={retry} />
    {error ? <ErrorAlert message={error}><button onClick={retry}>Retry salary summaries</button></ErrorAlert> : !data ? <AnalyticsSkeleton columns={grouping === "countries" ? 9 : 10} /> : <>
      <p className="population-count" role="status">Selected employees: <strong>{data.summaries.total_count}</strong></p>
      <div role="region" aria-labelledby="compa-heading" className="compa"><h3 id="compa-heading">Compa-ratio</h3>
        <dl className="metric-grid"><div className="metric-card"><dt>Total employees</dt><dd>{data.compa.total_count}</dd></div><div className="metric-card"><dt>Eligible employees</dt><dd>{data.compa.eligible_count}</dd></div>
          <div className="metric-card"><dt>Excluded Unbanded employees</dt><dd>{data.compa.excluded_unbanded_count}</dd></div></dl>
        {data.compa.state === "no_eligible_data" && <p className="notice">No eligible employees for compa-ratio.</p>}
        <p className="muted">Percentages use eligible employees only. Bucket membership uses the unrounded ratio.</p>
        <div className="metric-grid">{data.compa.buckets.map((bucket) => <article className="metric-card" key={bucket.key} aria-label={bucket.label}><h4>{bucket.label}</h4>
          <p className="metric-value">{bucket.count}</p><p>{bucket.percentage === null ? "Percentage unavailable" : `${bucket.percentage}%`}</p></article>)}</div>
      </div>
      <div className="results-toolbar"><h3>Salary statistics</h3><div><label htmlFor="summary-grouping">Group by</label><select id="summary-grouping" value={grouping} onChange={(event) => setGrouping(event.target.value as typeof grouping)}>
        <option value="countries">Country</option><option value="departments">Department within country</option><option value="levels">Level within country</option></select></div></div>
      {data.summaries.total_count === 0 && <p className="notice">No employees match these summary filters.</p>}
      {data.summaries[grouping].length === 0 ? <p>No groups available.</p> : <div className="table-scroll" role="region" aria-label="Salary summary results" tabIndex={0}>
        <table><caption className="sr-only">Salary statistics by {grouping}, with separate country and currency columns</caption><thead><tr>
          <th scope="col">Country</th>{grouping !== "countries" && <th scope="col">{grouping === "departments" ? "Department" : "Level"}</th>}<th scope="col">Currency</th><th scope="col">Headcount</th>
          {["Average", "Median", "Minimum", "Maximum", "Spread"].map((label) => <th scope="col" key={label}>{label}</th>)}<th scope="col">State</th>
        </tr></thead><tbody>{data.summaries[grouping].map((row) => <tr key={`${row.country}:${row.department_key || row.level || ""}`}>
          <td>{row.country}</td>{grouping !== "countries" && <td>{grouping === "departments" ? row.department : row.level}</td>}<td>{row.currency}</td><td>{row.count}</td>
          {(["average", "median", "minimum", "maximum", "spread"] as const).map((field) => <td key={field} className="money">{row[field] === null ? "—" : row[field].amount}</td>)}
          <td>{row.state === "no_data" ? "No data" : "Available"}</td></tr>)}</tbody></table>
      </div>}
    </>}
  </section>;
}
