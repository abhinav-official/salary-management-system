"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { api, ApiError, errorMessage, mutationHeaders, type Session } from "./employee-api";
import { codeOf, dateText, previewPath, type Inspection, type Preview, type Receipt } from "./import-api";
import PreviewPages, { IssueList } from "./import-preview";

async function loadSaved(id: string, signal?: AbortSignal): Promise<{ receipt: Receipt | null; preview: Preview | null }> {
  try { return { receipt: await api<Receipt>(previewPath(id) + "/receipt", { signal }), preview: null }; }
  catch (cause) { if (codeOf(cause) !== "receipt_not_found") throw cause; }
  return { receipt: null, preview: await api<Preview>(previewPath(id), { signal }) };
}

export default function Imports({ active, session, onExpired, onChanged }: { active: boolean; session: Session; onExpired: () => void; onChanged?: (message: string) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [inspection, setInspection] = useState<Inspection | null>(null);
  const [worksheet, setWorksheet] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [resumeId, setResumeId] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [detail, setDetail] = useState<unknown>(null);
  const [blocked, setBlocked] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [showIds, setShowIds] = useState(false);
  const operation = useRef(0);
  const storageKey = `salary-preview:${session.email}`;

  const failure = useCallback((cause: unknown) => {
    if (cause instanceof ApiError && cause.status === 401) { onExpired(); return; }
    setError(errorMessage(cause));
    setDetail(cause instanceof ApiError ? cause.detail : null);
    if (cause instanceof ApiError && ["preview_expired", "preview_conflict", "preview_invalid", "preview_altered", "preview_stale", "preview_not_found", "import_integrity_conflict"].includes(codeOf(cause) || "")) setBlocked(true);
  }, [onExpired]);

  const remember = useCallback((id: string) => {
    setResumeId(id);
    try { sessionStorage.setItem(storageKey, id); } catch { /* A copied ID still supports manual recovery. */ }
  }, [storageKey]);

  const resume = useCallback(async (id: string) => {
    if (!id) return;
    operation.current++;
    setBusy("Restoring preview…"); setError(""); setDetail(null); setBlocked(false); setUncertain(false); setReceipt(null); setPreview(null); setShowIds(false);
    try {
      const saved = await loadSaved(id); setReceipt(saved.receipt); setPreview(saved.preview);
      if (saved.preview) setWorksheet(saved.preview.worksheet);
      remember(id);
    } catch (cause) { failure(cause); } finally { setBusy(""); }
  }, [remember, failure]);

  useEffect(() => {
    let saved = "";
    try { saved = sessionStorage.getItem(storageKey) || ""; } catch { /* Recovery by ID remains available. */ }
    const controller = new AbortController();
    const version = operation.current;
    if (saved) void loadSaved(saved, controller.signal).then((result) => {
      if (operation.current !== version) return;
      setReceipt(result.receipt); setPreview(result.preview);
      if (result.preview) setWorksheet(result.preview.worksheet);
      remember(saved);
    }).catch((cause) => { if (!controller.signal.aborted && operation.current === version) failure(cause); });
    return () => controller.abort();
  }, [storageKey, remember, failure]);

  const refresh = useCallback(async () => {
    if (!preview) return;
    try { setPreview(await api<Preview>(previewPath(preview.preview_id))); }
    catch (cause) { failure(cause); }
  }, [preview, failure]);
  useEffect(() => {
    if (!active || !preview || receipt || blocked || busy) return;
    const interval = setInterval(() => void refresh(), 15_000);
    return () => clearInterval(interval);
  }, [active, preview, receipt, blocked, busy, refresh]);

  async function inspect() {
    if (!file) return;
    operation.current++; setPreview(null); setReceipt(null); setResumeId(""); setShowIds(false);
    try { sessionStorage.removeItem(storageKey); } catch { /* Storage is optional. */ }
    setBusy("Inspecting workbook…"); setError(""); setDetail(null); setBlocked(false);
    try {
      const result = await api<Inspection>("/imports/inspect", { method: "POST", headers: { "content-type": "application/octet-stream", "x-csrf-token": session.csrf_token }, body: file }, 120_000);
      setInspection(result);
      // Every multi-sheet workbook requires a deliberate choice in this screen.
      setWorksheet(result.worksheets.length === 1 ? result.worksheets[0].name : "");
    } catch (cause) { failure(cause); } finally { setBusy(""); }
  }
  async function createPreview(event?: FormEvent) {
    event?.preventDefault();
    if (!file || !worksheet) return;
    setBusy("Validating every workbook row…"); setError(""); setDetail(null); setPreview(null); setReceipt(null); setBlocked(false); setUncertain(false); setShowIds(false);
    try {
      const result = await api<Preview>(`/imports/previews?worksheet=${encodeURIComponent(worksheet)}`, { method: "POST", headers: { "content-type": "application/octet-stream", "x-csrf-token": session.csrf_token }, body: file }, 120_000);
      setPreview(result); remember(result.preview_id);
    } catch (cause) { failure(cause); } finally { setBusy(""); }
  }
  async function confirm() {
    if (!preview || preview.status !== "clean" || blocked || receipt) return;
    setBusy("Confirming import…"); setError(""); setDetail(null); setUncertain(false);
    try {
      const saved = await api<Receipt>(previewPath(preview.preview_id) + "/confirm", { method: "POST", headers: mutationHeaders(session), body: "{}" }, 120_000);
      setReceipt(saved); onChanged?.(`Import completed. Inserted employees: ${saved.inserted_count}.`);
    }
    catch (cause) {
      failure(cause);
      const code = codeOf(cause);
      // A transport failure can follow a successful commit. Keep the same ID.
      setUncertain(!(cause instanceof ApiError) || cause.status >= 500 || code === "database_busy");
    } finally { setBusy(""); }
  }

  if (!active) return null;
  const selected = inspection?.worksheets.find((sheet) => sheet.name === worksheet);
  return <section aria-labelledby="imports-heading"><div className="section-heading"><div><p className="eyebrow">Review before saving</p><h2 id="imports-heading">Excel import</h2></div></div>
    <p>Select a workbook, review every validation result, then confirm the saved preview. Previewing creates no employees.</p>
    <form className="import-upload" onSubmit={(event) => { event.preventDefault(); void inspect(); }}>
      <label htmlFor="workbook">Workbook (.xlsx)</label><input id="workbook" type="file" accept=".xlsx" disabled={!!busy || uncertain} onChange={(event) => {
        operation.current++;
        setFile(event.target.files?.[0] || null); setInspection(null); setWorksheet(""); setPreview(null); setReceipt(null); setError(""); setDetail(null); setBlocked(false); setUncertain(false); setShowIds(false);
        setResumeId(""); try { sessionStorage.removeItem(storageKey); } catch { /* Storage is optional. */ }
      }} />
      {file && <p>Selected file: <strong>{file.name}</strong></p>}
      <button disabled={!file || !!busy || uncertain}>Inspect workbook</button>
    </form>
    {inspection && <form className="import-upload" onSubmit={(event) => void createPreview(event)}>
      {inspection.worksheets.length > 1 && <p>Choose the worksheet to import. This workbook contains multiple sheets.</p>}
      <label htmlFor="worksheet">Worksheet</label><select id="worksheet" value={worksheet} disabled={!!busy || uncertain} onChange={(event) => {
        operation.current++; setResumeId(""); try { sessionStorage.removeItem(storageKey); } catch { /* Storage is optional. */ }
        setWorksheet(event.target.value); setPreview(null); setReceipt(null); setBlocked(false); setUncertain(false); setError(""); setDetail(null);
      }}><option value="">Choose a worksheet</option>{inspection.worksheets.map((sheet) => <option key={sheet.name} value={sheet.name}>{sheet.name}</option>)}</select>
      {selected && <div><p>Nonblank data rows: {selected.data_rows} · Formulas: {selected.formula_count}</p>{selected.header_errors.length > 0 && <IssueList errors={selected.header_errors} />}</div>}
      <button disabled={!worksheet || !!busy || uncertain}>Create a new preview</button>
    </form>}
    {busy && <p role="status" aria-live="polite">{busy}</p>}
    {error && <div role="alert" className="error"><p>{error}</p>
      {blocked && <p>This preview cannot be confirmed. Keep your selection and create a new preview; reselect the file if it is no longer available.</p>}
      {uncertain && <p>Retry this same preview to resolve the outcome safely. A completed import returns its saved receipt without inserting again.</p>}
      {detail !== null && <details><summary>API error details</summary><pre>{JSON.stringify(detail, null, 2)}</pre></details>}
    </div>}
    {preview && !receipt && <div className="import-review" role="region" aria-label="Import review">
      <h3>Preview: {preview.status}</h3><p>Worksheet: {preview.worksheet}</p><p className="preview-id">Preview ID: <code>{preview.preview_id}</code></p>
      <dl className="import-totals"><div><dt>Total rows</dt><dd>{preview.total_rows}</dd></div><div><dt>Valid rows</dt><dd>{preview.valid_count}</dd></div><div><dt>Invalid rows</dt><dd>{preview.invalid_count}</dd></div>
        <div><dt>Unbanded rows</dt><dd>{preview.unbanded_count}</dd></div><div><dt>Valid Unbanded rows</dt><dd>{preview.valid_unbanded_count}</dd></div><div><dt>Errors</dt><dd>{preview.error_count}</dd></div></dl>
      <p>Expires: <time dateTime={new Date(preview.expires_at * 1000).toISOString()}>{dateText(preview.expires_at)}</time></p>
      <div className="actions"><button className="secondary" disabled={!!busy} onClick={() => void refresh()}>Refresh preview status</button>
        <button disabled={!!busy || preview.status !== "clean" || blocked} onClick={() => void confirm()}>{uncertain ? "Retry same confirmation safely" : "Confirm import"}</button></div>
      <PreviewPages key={preview.preview_id} preview={preview} onExpired={onExpired} onUnavailable={failure} />
    </div>}
    {receipt && <div className="notice receipt" role="region" aria-label="Completion receipt"><h3>Import completed</h3>
      <p>Inserted employees: <strong>{receipt.inserted_count}</strong></p><p>Actor: {receipt.actor}</p><p>Completed: {dateText(receipt.completed_at)}</p><p>Preview ID: <code>{receipt.preview_id}</code></p>
      <details onToggle={(event) => setShowIds(event.currentTarget.open)}><summary>Ordered employee IDs</summary>{showIds && <ol>{receipt.employee_ids.map((id) => <li key={id}>{id}</li>)}</ol>}</details>
      <button className="secondary" disabled={!!busy} onClick={() => void resume(receipt.preview_id)}>Retrieve saved receipt</button>
    </div>}
    <form className="import-upload recovery" onSubmit={(event) => { event.preventDefault(); void resume(resumeId); }}>
      <h3>Resume a preview or retrieve its receipt</h3><label htmlFor="resume-id">Saved preview ID</label><input id="resume-id" value={resumeId} disabled={!!busy} onChange={(event) => setResumeId(event.target.value)} autoComplete="off" />
      <button className="secondary" disabled={!resumeId || !!busy}>Resume saved preview</button>
    </form>
  </section>;
}
