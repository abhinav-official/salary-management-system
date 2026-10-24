"use client";

import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError, errorMessage, mutationHeaders, type FilterOptions, type Session } from "./employee-api";
import { Modal } from "./employee-dialogs";
import { ErrorAlert } from "./ui-feedback";

type Band = {
  country: string; level: string; currency: string; revision: number; etag: string;
  minimum_amount: string; midpoint_amount: string; maximum_amount: string; affected_employee_count: number;
};
const path = (band: Band) => `/bands/${encodeURIComponent(band.country)}/${encodeURIComponent(band.level)}`;
const headers = (session: Session, band?: Band) => ({ ...mutationHeaders(session), ...(band ? { "if-match": band.etag } : {}) });
type Common = { session: Session; onExpired: () => void; onClose: () => void; onSaved: (message: string) => void; onReload: (band: Band) => void };

function BandForm({ band, options, ...props }: Common & { band: Band | null; options: FilterOptions }) {
  const [form, setForm] = useState(() => ({ country: band?.country || options.countries[0], level: band?.level || options.levels[0],
    minimum_amount: band?.minimum_amount || "", midpoint_amount: band?.midpoint_amount || "", maximum_amount: band?.maximum_amount || "" }));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [fields, setFields] = useState<Record<string, string>>({});
  const [stale, setStale] = useState(false);
  function failed(cause: unknown) {
    if (cause instanceof ApiError && cause.status === 401) { props.onExpired(); return; }
    setFields(cause instanceof ApiError ? cause.fields : {});
    setError(cause instanceof ApiError ? cause.fields.body || cause.message : errorMessage(cause));
    setStale(cause instanceof ApiError && cause.status === 412);
  }
  async function save(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(""); setFields({});
    const { country, level, ...amounts } = form;
    try {
      const saved = await api<Band>(band ? path(band) : "/bands", { method: band ? "PUT" : "POST", headers: headers(props.session, band || undefined),
        body: JSON.stringify(band ? amounts : { country, level, ...amounts }) });
      props.onSaved(`${saved.country} ${saved.level} saved. ${saved.currency} ${saved.minimum_amount} / ${saved.midpoint_amount} / ${saved.maximum_amount}. Affected employees: ${saved.affected_employee_count}.`);
    } catch (cause) { failed(cause); } finally { setBusy(false); }
  }
  async function reload() {
    if (!band) return;
    setBusy(true);
    try { props.onReload(await api<Band>(path(band))); } catch (cause) { failed(cause); } finally { setBusy(false); }
  }
  return <Modal title={band ? `Edit ${band.country} ${band.level}` : "Create salary band"} busy={busy} onClose={props.onClose}>
    <form className="employee-form" noValidate onSubmit={(event) => void save(event)}>
      <div className="form-grid">
        {(["country", "level"] as const).map((field) => <div key={field}><label htmlFor={`band-${field}`}>{field === "country" ? "Country" : "Level"}</label>
          <select id={`band-${field}`} value={form[field]} disabled={busy || !!band} aria-invalid={!!fields[field]} aria-describedby={fields[field] ? `band-${field}-error` : undefined}
            onChange={(event) => setForm({ ...form, [field]: event.target.value })}>
            {(field === "country" ? options.countries : options.levels).map((value) => <option key={value}>{value}</option>)}
          </select>{fields[field] && <p id={`band-${field}-error`} className="field-error">{fields[field]}</p>}</div>)}
        {([["minimum_amount", "Minimum"], ["midpoint_amount", "Midpoint"], ["maximum_amount", "Maximum"]] as const).map(([field, label]) =>
          <div key={field} className="wide"><label htmlFor={`band-${field}`}>{label}</label>
            <input id={`band-${field}`} inputMode="decimal" value={form[field]} disabled={busy} aria-invalid={!!fields[field]} aria-describedby={fields[field] ? `band-${field}-error` : undefined}
              onChange={(event) => setForm({ ...form, [field]: event.target.value })} />
            {fields[field] && <p id={`band-${field}-error`} className="field-error">{fields[field]}</p>}</div>)}
      </div>
      <p className="muted">Annual amounts for {form.country}{band ? ` (${band.currency})` : ""}. Enter decimal amounts without symbols or separators.</p>
      {error && <ErrorAlert message={error}>{stale && <button type="button" className="secondary" disabled={busy} onClick={() => void reload()}>Reload current band</button>}</ErrorAlert>}
      <div className="dialog-actions"><button type="button" className="secondary" onClick={props.onClose} disabled={busy}>Cancel</button>
        <button disabled={busy || stale}>{busy ? "Saving…" : band ? "Save band" : "Create band"}</button></div>
    </form>
  </Modal>;
}

function DeleteBand({ band, ...props }: Common & { band: Band }) {
  const { session, onExpired } = props;
  const [impact, setImpact] = useState<{ message: string; affected_employee_count: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [stale, setStale] = useState(false);
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    // The API's unconfirmed delete reports impact without changing the band.
    void api(path(band), { method: "DELETE", headers: headers(session, band), signal: controller.signal }).catch((cause) => {
      if (controller.signal.aborted) return;
      if (cause instanceof ApiError && cause.status === 401) { onExpired(); return; }
      if (cause instanceof ApiError && typeof cause.detail === "object" && !Array.isArray(cause.detail) && cause.detail.code === "confirmation_required") {
        setImpact(cause.detail as { message: string; affected_employee_count: number }); setError("");
      } else { setError(errorMessage(cause)); setStale(cause instanceof ApiError && cause.status === 412); }
    });
    return () => controller.abort();
  }, [band, session, onExpired, version]);
  async function remove(reload = false) {
    setBusy(true); setError("");
    try {
      if (reload) { props.onReload(await api<Band>(path(band))); return; }
      const result = await api<{ affected_employee_count: number }>(path(band) + "?confirmed=true", { method: "DELETE", headers: headers(props.session, band) });
      props.onSaved(`${band.country} ${band.level} deleted. Affected employees: ${result.affected_employee_count}.`);
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401) { props.onExpired(); return; }
      setError(errorMessage(cause)); setStale(cause instanceof ApiError && cause.status === 412);
    } finally { setBusy(false); }
  }
  return <Modal title={`Delete ${band.country} ${band.level}?`} busy={busy} onClose={props.onClose}>
    {impact ? <div className="confirmation"><p>{impact.message}</p><p>Affected employees: <strong>{impact.affected_employee_count}</strong>.</p></div> : !error && <p role="status">Loading deletion impact…</p>}
    {error && <ErrorAlert message={error}>{stale ? <button className="secondary" disabled={busy} onClick={() => void remove(true)}>Reload current band</button> :
      <button className="secondary" disabled={busy} onClick={() => setVersion((value) => value + 1)}>Retry deletion impact</button>}</ErrorAlert>}
    <div className="dialog-actions"><button className="secondary" disabled={busy} onClick={props.onClose}>Cancel</button>
      <button className="danger" disabled={busy || !impact || stale || !!error} onClick={() => void remove()}>{busy ? "Deleting…" : "Delete band"}</button></div>
  </Modal>;
}

export default function Bands({ session, onExpired, onChanged }: { session: Session; onExpired: () => void; onChanged?: (message: string) => void }) {
  const [data, setData] = useState<{ items: Band[]; total: number } | null>(null);
  const [options, setOptions] = useState<FilterOptions | null>(null);
  const [error, setError] = useState("");
  const [version, setVersion] = useState(0);
  const [editing, setEditing] = useState<{ band: Band | null } | null>(null);
  const [deleting, setDeleting] = useState<Band | null>(null);
  const [notice, setNotice] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([api<{ items: Band[]; total: number }>("/bands", { signal: controller.signal }), api<FilterOptions>("/employees/filter-options", { signal: controller.signal })])
      .then(([bands, filters]) => { setData(bands); setOptions(filters); setError(""); })
      .catch((cause) => { if (!controller.signal.aborted) { if (cause instanceof ApiError && cause.status === 401) onExpired(); else setError(errorMessage(cause)); } });
    return () => controller.abort();
  }, [version, onExpired]);
  function saved(message: string) { setNotice(message); setEditing(null); setDeleting(null); setData(null); setVersion((value) => value + 1); onChanged?.(message); }
  return <section aria-labelledby="bands-heading"><div className="section-heading"><div><p className="eyebrow">Compensation framework</p><h2 id="bands-heading">Salary bands</h2></div>
    <button disabled={!options || !!error} onClick={() => setEditing({ band: null })}>Add salary band</button></div>
    {notice && <p role="status" className="notice">{notice}</p>}
    {error ? <div className="state error" role="alert"><p>{error}</p><button onClick={() => setVersion((value) => value + 1)}>Retry salary bands</button></div> : !data ? <p role="status">Loading salary bands…</p> : <>
      <p role="status">{data.total} salary bands</p>
      {data.items.length === 0 ? <div className="state"><h3>No salary bands</h3><p>Create a band to start your catalog.</p></div> : <div className="table-scroll" role="region" aria-label="Salary band results" tabIndex={0}><table><caption className="sr-only">Salary bands and affected employee counts</caption>
        <thead><tr>{["Country", "Level", "Currency", "Minimum", "Midpoint", "Maximum", "Affected employees", "Actions"].map((label) => <th key={label} scope="col">{label}</th>)}</tr></thead>
        <tbody>{data.items.map((band) => <tr key={`${band.country}:${band.level}`}><td>{band.country}</td><td>{band.level}</td><td>{band.currency}</td>
          <td className="money">{band.minimum_amount}</td><td className="money">{band.midpoint_amount}</td><td className="money">{band.maximum_amount}</td><td>{band.affected_employee_count}</td>
          <td className="row-actions"><button className="text-button" aria-label={`Edit ${band.country} ${band.level}`} onClick={() => setEditing({ band })}>Edit</button>
            <button className="text-button delete-text" aria-label={`Delete ${band.country} ${band.level}`} onClick={() => setDeleting(band)}>Delete</button></td></tr>)}</tbody>
      </table></div>}
    </>}
    {editing && options && <BandForm key={editing.band?.etag || "create"} band={editing.band} options={options} session={session} onExpired={onExpired} onClose={() => setEditing(null)} onSaved={saved} onReload={(band) => setEditing({ band })} />}
    {deleting && <DeleteBand key={deleting.etag} band={deleting} session={session} onExpired={onExpired} onClose={() => setDeleting(null)} onSaved={saved} onReload={setDeleting} />}
  </section>;
}
