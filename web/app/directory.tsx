"use client";

import { useEffect, useState } from "react";
import { api, ApiError, errorMessage, type DirectoryPage, type Employee, type FilterOptions, type Session } from "./employee-api";
import { DeleteEmployee, EmployeeForm } from "./employee-dialogs";

type Query = { search: string; country: string; level: string; department: string; sort: string; direction: string; page: number; page_size: number };
const initialQuery: Query = { search: "", country: "", level: "", department: "", sort: "employee_id", direction: "asc", page: 1, page_size: 25 };
const columns = [["employee_id", "Employee ID"], ["name", "Name"], ["email", "Email"], ["department", "Department"],
  ["country", "Country"], ["level", "Level"], ["salary", "Annual salary"]];

export default function Directory({ session, onExpired, active = true, refreshKey = 0 }: { session: Session; onExpired: () => void; active?: boolean; refreshKey?: number }) {
  const [query, setQuery] = useState(initialQuery);
  const [version, setVersion] = useState(0);
  const [result, setResult] = useState<{ key: string; data: DirectoryPage } | null>(null);
  const [failure, setFailure] = useState<{ key: string; message: string } | null>(null);
  const [options, setOptions] = useState<FilterOptions | null>(null);
  const [optionsError, setOptionsError] = useState("");
  const [editor, setEditor] = useState<{ employee: Employee | null } | null>(null);
  const [deleting, setDeleting] = useState<Employee | null>(null);
  const [notice, setNotice] = useState("");
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState("");
  const params = new URLSearchParams(Object.entries(query).map(([key, value]) => [key, String(value)])).toString();
  const requestKey = `${params}:${version}:${refreshKey}`;
  const data = result?.key === requestKey ? result.data : null;
  const error = failure?.key === requestKey ? failure.message : "";
  const loading = !data && !error;

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    void api<FilterOptions>("/employees/filter-options", { signal: controller.signal })
      .then((value) => { setOptions(value); setOptionsError(""); })
      .catch((cause) => {
        if (controller.signal.aborted) return;
        if (cause instanceof ApiError && cause.status === 401) onExpired();
        else setOptionsError(errorMessage(cause));
      });
    return () => controller.abort();
  }, [version, onExpired, active]);

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    void api<DirectoryPage>(`/employees?${params}`, { signal: controller.signal })
      .then((value) => {
        // A deletion (including one by another session) can empty the last page.
        if (value.page > 1 && value.items.length === 0) {
          setQuery((previous) => ({ ...previous, page: Math.max(1, Math.min(value.total_pages, value.page - 1)) }));
        } else setResult({ key: requestKey, data: value });
      })
      .catch((cause) => {
        if (controller.signal.aborted) return;
        if (cause instanceof ApiError && cause.status === 401) onExpired();
        else setFailure({ key: requestKey, message: errorMessage(cause) });
      });
    return () => controller.abort();
  }, [params, requestKey, onExpired, active]);

  function filter(field: string, value: string) {
    setQuery((previous) => ({ ...previous, [field]: value, page: 1 }));
  }
  function saved(message: string) {
    setNotice(message); setEditor(null); setDeleting(null); setVersion((previous) => previous + 1);
  }
  async function download() {
    setExporting(true); setExportError("");
    try {
      const exportParams = new URLSearchParams(params);
      exportParams.delete("page"); exportParams.delete("page_size");
      const response = await fetch(`/api/employees/export.csv?${exportParams}`, { cache: "no-store", signal: AbortSignal.timeout(20_000) });
      if (!response.ok) { const body = await response.json(); throw new ApiError(response.status, body.detail); }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a"); link.href = url; link.download = "employees.csv";
      document.body.append(link); link.click(); link.remove();
      // Let the browser begin consuming the blob before revoking it.
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401) onExpired();
      else setExportError(errorMessage(cause));
    } finally { setExporting(false); }
  }

  if (!active) return null;
  return <section className="directory" aria-labelledby="directory-heading">
    <div className="section-heading"><div><p className="eyebrow">People & compensation</p><h2 id="directory-heading">Employee directory</h2></div>
      <div className="actions"><button className="secondary" disabled={exporting || loading || !!error} onClick={() => void download()}>{exporting ? "Exporting…" : "Download CSV"}</button>
        <button disabled={!options || !!optionsError} onClick={() => setEditor({ employee: null })}>Add employee</button></div></div>
    {notice && <p className="notice" role="status">{notice}</p>}
    {exportError && <div className="error" role="alert"><p>{exportError}</p><button className="secondary" onClick={() => void download()} disabled={exporting}>Retry CSV download</button></div>}
    {optionsError && <div className="error" role="alert"><p>Unable to load filter options. {optionsError}</p><button className="secondary" onClick={() => setVersion((value) => value + 1)}>Retry filter options</button></div>}
    <div className="filters">
      <div className="search"><label htmlFor="directory-search">Search employees</label><input id="directory-search" type="search" placeholder="Employee ID, name or email" value={query.search} onChange={(event) => filter("search", event.target.value)} /></div>
      {(["country", "level", "department"] as const).map((field) => <div key={field}><label htmlFor={`filter-${field}`}>{field[0].toUpperCase() + field.slice(1)}</label>
        <select id={`filter-${field}`} value={query[field]} disabled={!options || !!optionsError} onChange={(event) => filter(field, event.target.value)}>
          <option value="">All {field === "country" ? "countries" : `${field}s`}</option>
          {(field === "department" ? options?.departments || [] : (field === "country" ? options?.countries : options?.levels)?.map((value) => ({ value, label: value })) || [])
            .map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select></div>)}
      <button className="secondary" onClick={() => setQuery((previous) => ({ ...previous, search: "", country: "", level: "", department: "", page: 1 }))}>Clear filters</button>
    </div>
    <div className="results-toolbar"><p role="status" aria-live="polite">{loading ? "Loading employees…" : error ? "Directory unavailable" : `${data!.total} matching employees`}</p>
      <div className="page-size"><label htmlFor="page-size">Rows per page</label><select id="page-size" value={query.page_size} onChange={(event) => setQuery((previous) => ({ ...previous, page_size: Number(event.target.value), page: 1 }))}>
        {[25, 50, 100].map((size) => <option key={size}>{size}</option>)}</select></div></div>
    {loading ? <div className="state" aria-busy="true"><div className="loading-bar" /><p>Loading your directory…</p></div> : error ? <div className="state error" role="alert"><h3>Unable to load employees</h3><p>{error}</p>
      <button onClick={() => setVersion((value) => value + 1)}>Retry directory</button></div> : <>
      <p className="ordering">Order: {data!.ordering}</p>
      {data!.items.length === 0 ? <div className="state"><h3>No employees found</h3><p>Try another search or clear the filters. You can also add an employee.</p></div> : <div className="table-scroll" tabIndex={0} role="region" aria-label="Employee results">
        <table><caption className="sr-only">Employee directory. Use the column headings to sort results.</caption><thead><tr>
          {columns.map(([field, label]) => <th key={field} scope="col" aria-sort={query.sort === field ? query.direction === "asc" ? "ascending" : "descending" : undefined}>
            <button className="sort" aria-label={`Sort by ${label}`} onClick={() => setQuery((previous) => ({ ...previous, sort: field, direction: previous.sort === field && previous.direction === "asc" ? "desc" : "asc", page: 1 }))}>
              {label} <span aria-hidden="true">{query.sort === field ? query.direction === "asc" ? "↑" : "↓" : "↕"}</span></button></th>)}
          <th scope="col">Band status</th><th scope="col">Actions</th></tr></thead>
          <tbody>{data!.items.map((employee) => <tr key={employee.employee_id}>
            <td className="identifier">{employee.employee_id}</td><td className="name">{employee.name}</td><td>{employee.email}</td><td>{employee.department}</td><td>{employee.country}</td><td>{employee.level}</td>
            <td className="money"><span className="currency">{employee.currency}</span> {employee.salary_amount}</td><td><span className="badge">{employee.band_status}</span></td>
            <td><div className="row-actions"><button className="text-button" aria-label={`Edit ${employee.employee_id}`} onClick={() => setEditor({ employee })}>Edit</button>
              <button className="text-button delete-text" aria-label={`Delete ${employee.employee_id}`} onClick={() => setDeleting(employee)}>Delete</button></div></td>
          </tr>)}</tbody></table>
      </div>}
      <nav className="pagination" aria-label="Directory pages"><p>{data!.total === 0 ? "0–0 of 0" : `${(data!.page - 1) * data!.page_size + 1}–${(data!.page - 1) * data!.page_size + data!.items.length} of ${data!.total}`}</p>
        <div className="actions"><button className="secondary" disabled={data!.page <= 1} onClick={() => setQuery((previous) => ({ ...previous, page: previous.page - 1 }))}>Previous</button>
          <span>Page {data!.page} of {data!.total_pages || 1}</span><button className="secondary" disabled={data!.page >= data!.total_pages} onClick={() => setQuery((previous) => ({ ...previous, page: previous.page + 1 }))}>Next</button></div></nav>
    </>}
    {editor && options && <EmployeeForm key={editor.employee?.etag || "create"} employee={editor.employee} options={options} session={session} onClose={() => setEditor(null)} onSaved={saved} onReload={(employee) => setEditor({ employee })} onExpired={onExpired} />}
    {deleting && <DeleteEmployee key={deleting.etag} employee={deleting} session={session} onClose={() => setDeleting(null)} onSaved={saved} onReload={setDeleting} onExpired={onExpired} />}
  </section>;
}
