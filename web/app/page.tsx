"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";

import Directory from "./directory";
import type { Session } from "./employee-api";

async function readHealth(): Promise<string> {
  try {
    const response = await fetch("/api/health/ready", { cache: "no-store" });
    const result = await response.json();
    return response.ok ? result.status : "unavailable";
  } catch { return "unavailable"; }
}

export default function Home() {
  const [health, setHealth] = useState("checking");
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    void readHealth().then(setHealth);
    void fetch("/api/auth/session", { cache: "no-store" })
      .then(async (response) => {
        if (response.ok) setSession(await response.json());
        else if (response.status !== 401) setError("Unable to check your session. Try again.");
      })
      .catch(() => setError("Unable to reach the API. Try again."))
      .finally(() => setLoading(false));
  }, []);

  const expired = useCallback(() => { setSession(null); setError("Your session expired. Sign in again."); }, []);

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ email: data.get("email"), password: data.get("password") }),
      });
      const result = await response.json();
      if (response.ok) { setSession(result); form.reset(); }
      else setError(typeof result.detail === "string" ? result.detail : "Check your sign-in details.");
    } catch { setError("Unable to reach the API. Try again."); }
    finally { setBusy(false); }
  }

  async function signOut() {
    if (!session) return;
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/auth/logout", {
        method: "POST", headers: { "x-csrf-token": session.csrf_token },
      });
      if (response.ok || response.status === 401) setSession(null);
      else setError("Unable to sign out. Try again.");
    } catch { setError("Unable to reach the API. Try again."); }
    finally { setBusy(false); }
  }

  return <main>
    <header><p className="eyebrow">HR workspace</p><h1>Salary management</h1>
      <p>Manage your people and their annual compensation.</p></header>
    <section aria-labelledby="health-heading" className="health">
      <div><h2 id="health-heading">Service status</h2><p role="status">API health: {health}</p></div>
      <button className="secondary" onClick={() => void readHealth().then(setHealth)}>Refresh status</button>
    </section>
    <section aria-labelledby="session-heading">
      <h2 id="session-heading">{session ? "Your session" : "HR sign-in"}</h2>
      {loading ? <p role="status">Checking your session…</p> : session ? <>
        <p>Signed in as <strong>{session.email}</strong>.</p>

        <button onClick={() => void signOut()} disabled={busy}>{busy ? "Signing out…" : "Sign out"}</button>
      </> : <form onSubmit={(event) => void signIn(event)}>
        <label htmlFor="email">Email</label>
        <input id="email" name="email" type="email" autoComplete="username" required maxLength={254} />
        <label htmlFor="password">Password</label>
        <input id="password" name="password" type="password" autoComplete="current-password" required maxLength={1024} />
        <button disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>}
      {error && <p role="alert" className="error">{error}</p>}
    </section>
    {session && <Directory session={session} onExpired={expired} />}
  </main>;
}
