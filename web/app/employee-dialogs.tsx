"use client";

import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { api, ApiError, errorMessage, mutationHeaders, recordPath, type Employee, type FilterOptions, type Session } from "./employee-api";

export function Modal({ title, children, busy, onClose }: {
  title: string; children: ReactNode; busy: boolean; onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const current = dialog.current;
    current?.showModal();
    return () => current?.close();
  }, []);
  return <dialog ref={dialog} aria-labelledby="dialog-title" onCancel={(event) => {
    event.preventDefault(); if (!busy) onClose();
  }}>
    <div className="dialog-heading"><h2 id="dialog-title">{title}</h2>
      <button type="button" className="secondary" onClick={onClose} disabled={busy} aria-label="Close dialog">×</button></div>
    {children}
  </dialog>;
}

type FormValues = { employee_id: string; name: string; email: string; department: string; country: string; level: string; salary_amount: string };
const labels: Record<keyof FormValues, string> = {
  employee_id: "Employee ID", name: "Name", email: "Email", department: "Department",
  country: "Country", level: "Level", salary_amount: "Annual salary",
};

type Common = { session: Session; onClose: () => void; onSaved: (message: string) => void; onExpired: () => void };
export function EmployeeForm({ employee, options, onReload, ...props }: Common & {
  employee: Employee | null; options: FilterOptions; onReload: (employee: Employee) => void;
}) {
  const [form, setForm] = useState<FormValues>(() => employee ? {
    employee_id: employee.employee_id, name: employee.name, email: employee.email, department: employee.department,
    country: employee.country, level: employee.level, salary_amount: employee.salary_amount,
  } : { employee_id: "", name: "", email: "", department: "", country: options.countries[0], level: options.levels[0], salary_amount: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [fields, setFields] = useState<Record<string, string>>({});
  const [conflict, setConflict] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const changedCountry = employee !== null && employee.country !== form.country;

  function failure(cause: unknown) {
    if (cause instanceof ApiError && cause.status === 401) { props.onExpired(); return; }
    setError(errorMessage(cause));
    setFields(cause instanceof ApiError ? cause.fields : {});
    setConflict(cause instanceof ApiError && cause.status === 412);
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setError(""); setFields({});
    const { employee_id, ...changes } = form;
    try {
      const saved = await api<Employee>(employee ? recordPath(employee) + (changedCountry && confirmed ? "?country_change_confirmed=true" : "") : "/employees", {
        method: employee ? "PUT" : "POST", headers: mutationHeaders(props.session, employee || undefined),
        body: JSON.stringify(employee ? changes : { employee_id, ...changes }),
      });
      props.onSaved(`${saved.employee_id} ${employee ? "updated" : "created"}. Salary: ${saved.currency} ${saved.salary_amount}. ${saved.band_status}.`);
    } catch (cause) { failure(cause); }
    finally { setBusy(false); }
  }
  async function reload() {
    if (!employee) return;
    setBusy(true);
    try { onReload(await api<Employee>(recordPath(employee))); }
    catch (cause) { failure(cause); }
    finally { setBusy(false); }
  }
  return <Modal title={employee ? `Edit ${employee.employee_id}` : "Add employee"} busy={busy} onClose={props.onClose}>
    <form onSubmit={(event) => void save(event)} className="employee-form">
      <div className="form-grid">
        {(Object.keys(labels) as (keyof FormValues)[]).map((field) => <div key={field} className={field === "salary_amount" ? "wide" : ""}>
          <label htmlFor={`employee-${field}`}>{labels[field]}</label>
          {field === "country" || field === "level" ? <select id={`employee-${field}`} value={form[field]} disabled={busy}
            aria-invalid={!!fields[field]} aria-describedby={fields[field] ? `${field}-error` : undefined}
            onChange={(event) => { setForm({ ...form, [field]: event.target.value }); if (field === "country") setConfirmed(false); }}>
            {(field === "country" ? options.countries : options.levels).map((value) => <option key={value}>{value}</option>)}
          </select> : <input id={`employee-${field}`} value={form[field]} type={field === "email" ? "email" : "text"}
            inputMode={field === "salary_amount" ? "decimal" : undefined} required disabled={busy}
            readOnly={field === "employee_id" && !!employee} autoComplete="off"
            aria-invalid={!!fields[field]} aria-describedby={[fields[field] ? `${field}-error` : "", field === "salary_amount" ? "salary-context" : ""].filter(Boolean).join(" ") || undefined}
            onChange={(event) => setForm({ ...form, [field]: event.target.value })} />}
          {fields[field] && <p className="field-error" id={`${field}-error`}>{fields[field]}</p>}
        </div>)}
      </div>
      <p className="muted" id="salary-context">Annual amount for {form.country}{employee && !changedCountry ? ` (${employee.currency})` : ""}. Enter a decimal amount without currency symbols or separators.</p>
      {changedCountry && <div className="confirmation"><p>Changing country changes the salary&apos;s currency context. Review the amount for {form.country}; no currency conversion is performed.</p>
        <label className="checkbox"><input type="checkbox" checked={confirmed} required disabled={busy} onChange={(event) => setConfirmed(event.target.checked)} />
          I confirm the salary amount for {form.country}.</label></div>}
      {error && <div role="alert" className="error"><p>{error}</p>
        {conflict && <button type="button" className="secondary" onClick={() => void reload()} disabled={busy}>Reload current employee</button>}
      </div>}
      <div className="dialog-actions"><button type="button" className="secondary" onClick={props.onClose} disabled={busy}>Cancel</button>
        <button disabled={busy || conflict}>{busy ? "Saving…" : employee ? "Save changes" : "Create employee"}</button></div>
    </form>
  </Modal>;
}

export function DeleteEmployee({ employee, onReload, ...props }: Common & { employee: Employee; onReload: (employee: Employee) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [conflict, setConflict] = useState(false);
  async function perform(reload = false) {
    setBusy(true); setError("");
    try {
      if (reload) { onReload(await api<Employee>(recordPath(employee))); return; }
      await api(recordPath(employee) + "?confirmed=true", { method: "DELETE", headers: mutationHeaders(props.session, employee) });
      props.onSaved(`${employee.employee_id} deleted.`);
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401) { props.onExpired(); return; }
      setError(errorMessage(cause)); setConflict(cause instanceof ApiError && cause.status === 412);
    } finally { setBusy(false); }
  }
  return <Modal title={`Delete ${employee.employee_id}?`} busy={busy} onClose={props.onClose}>
    <p>This will permanently delete <strong>{employee.name}</strong> ({employee.employee_id}).</p>
    <p>{employee.currency} {employee.salary_amount} · {employee.band_status}</p>
    {error && <div className="error" role="alert"><p>{error}</p>{conflict && <button className="secondary" onClick={() => void perform(true)} disabled={busy}>Reload current employee</button>}</div>}
    <div className="dialog-actions"><button className="secondary" onClick={props.onClose} disabled={busy}>Cancel</button>
      <button className="danger" onClick={() => void perform()} disabled={busy || conflict}>{busy ? "Deleting…" : "Delete employee"}</button></div>
  </Modal>;
}
