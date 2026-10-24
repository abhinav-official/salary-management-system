import type { FilterOptions } from "./employee-api";
import type { Filters } from "./analytics-api";
import { ErrorAlert } from "./ui-feedback";

export default function AnalyticsFilters({ prefix, filters, options, error, onChange, onRetry }: {
  prefix: string; filters: Filters; options: FilterOptions | null; error: string; onChange: (filters: Filters) => void; onRetry: () => void;
}) {
  return <>
    <fieldset className="analytics-filters" disabled={!options || !!error}><legend>{prefix === "summary" ? "Salary summary filters" : "Equity target filters"}</legend>
      {(["country", "department", "level"] as const).map((field) => <div key={field}><label htmlFor={`${prefix}-${field}`}>{field === "country" ? "Country" : field === "department" ? "Department" : "Level"}</label>
        <select id={`${prefix}-${field}`} value={filters[field]} onChange={(event) => onChange({ ...filters, [field]: event.target.value })}>
          <option value="">{field === "country" ? "All countries" : field === "department" ? "All departments" : "All levels"}</option>
          {field === "department" && filters.department && !options?.departments.some((value) => value.value === filters.department) && <option value={filters.department}>{filters.department}</option>}
          {field === "department" ? options?.departments.map((value) => <option key={value.value} value={value.value}>{value.label}</option>) :
            (field === "country" ? options?.countries : options?.levels)?.map((value) => <option key={value}>{value}</option>)}
        </select></div>)}
      <button className="secondary" onClick={() => onChange({ country: "", department: "", level: "" })}>Clear {prefix === "summary" ? "summary" : "equity"} filters</button>
    </fieldset>
    {error && <ErrorAlert message={error}><button onClick={onRetry}>Retry {prefix} filter options</button></ErrorAlert>}
  </>;
}
