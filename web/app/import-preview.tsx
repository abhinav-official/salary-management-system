"use client";

import { useEffect, useState } from "react";
import { api, ApiError, errorMessage } from "./employee-api";
import { previewPath, type Issue, type PageData, type Preview, type PreviewRow } from "./import-api";

export function IssueList({ errors }: { errors: Issue[] }) {
  return <ul className="issue-list">{errors.map((error, index) => <li key={index}>
    <strong>{error.sheet}{error.row === null ? " · Workbook" : ` · Excel row ${error.row}`} · {error.field}</strong>: {error.message} <span className="muted">({error.code})</span>
  </li>)}</ul>;
}

export default function PreviewPages({ preview, onExpired, onUnavailable }: {
  preview: Preview; onExpired: () => void; onUnavailable: (cause: unknown) => void;
}) {
  const [kind, setKind] = useState<"rows" | "errors">("rows");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [version, setVersion] = useState(0);
  const [result, setResult] = useState<{ key: string; data: PageData<PreviewRow | Issue> } | null>(null);
  const [failure, setFailure] = useState<{ key: string; message: string } | null>(null);
  const key = `${preview.preview_id}:${kind}:${page}:${size}:${version}`;
  const data = result?.key === key ? result.data : null;
  const error = failure?.key === key ? failure.message : "";
  useEffect(() => {
    const controller = new AbortController();
    void api<PageData<PreviewRow | Issue>>(`${previewPath(preview.preview_id)}/${kind}?page=${page}&page_size=${size}`, { signal: controller.signal })
      .then((data) => setResult({ key, data }))
      .catch((cause) => {
        if (controller.signal.aborted) return;
        if (cause instanceof ApiError && cause.status === 401) onExpired();
        else {
          setFailure({ key, message: errorMessage(cause) });
          if (cause instanceof ApiError && [404, 410].includes(cause.status)) onUnavailable(cause);
        }
      });
    return () => controller.abort();
  }, [preview.preview_id, key, kind, page, size, onExpired, onUnavailable]);
  return <div className="preview-pages">
    <div className="results-toolbar"><div className="actions"><button className={kind === "rows" ? "" : "secondary"} aria-pressed={kind === "rows"} onClick={() => { setKind("rows"); setPage(1); }}>Preview rows</button>
      <button className={kind === "errors" ? "" : "secondary"} aria-pressed={kind === "errors"} onClick={() => { setKind("errors"); setPage(1); }}>All errors</button></div>
      <label className="page-size">Preview rows per page<select value={size} onChange={(event) => { setSize(Number(event.target.value)); setPage(1); }}>{[25, 50, 100].map((value) => <option key={value}>{value}</option>)}</select></label></div>
    {error ? <div className="error" role="alert"><p>{error}</p><button onClick={() => setVersion((value) => value + 1)}>Retry preview page</button></div> : !data ? <p role="status">Loading preview {kind}…</p> : <>
      <p role="status">{data.total} {kind === "rows" ? "preview rows" : "validation errors"}</p>
      {kind === "errors" ? data.items.length ? <IssueList errors={data.items as Issue[]} /> : <p>No validation errors.</p> : data.items.length ?
        <div className="table-scroll" tabIndex={0} role="region" aria-label="Import preview rows"><table><caption className="sr-only">Original Excel rows and API validation results</caption><thead><tr>
          {["Excel row", "Employee ID", "Name", "Email", "Department", "Country", "Level", "Annual salary", "Band status", "Validation"].map((label) => <th scope="col" key={label}>{label}</th>)}
        </tr></thead><tbody>{(data.items as PreviewRow[]).map((row) => <tr key={row.row_number}>
          <td>{row.row_number}</td><td>{row.values.employee_id}</td><td>{row.values.name}</td><td>{row.values.email}</td><td>{row.values.department}</td><td>{row.values.country}</td><td>{row.values.level}</td>
          <td className="money">{row.currency} {row.salary_amount ?? row.salary_input}{row.rounding_applied && <p className="muted">API applied rounding</p>}</td><td>{row.band_status ?? "Unavailable"}</td>
          <td>{row.valid ? "Valid" : "Invalid"}{row.errors.length > 0 && <IssueList errors={row.errors} />}<details><summary>Original cells</summary><pre>{JSON.stringify(row.raw_cells, null, 2)}</pre></details></td>
        </tr>)}</tbody></table></div> : <p>No employee rows were interpreted. Review all errors.</p>}
      <nav className="pagination" aria-label="Preview pages"><span>{data.total_pages ? `Page ${data.page} of ${data.total_pages}` : "No pages"}</span><div className="actions">
        <button className="secondary" disabled={data.page <= 1} onClick={() => setPage(data.page - 1)}>Previous preview page</button>
        <button className="secondary" disabled={data.page >= data.total_pages} onClick={() => setPage(data.page + 1)}>Next preview page</button></div></nav>
    </>}
  </div>;
}
