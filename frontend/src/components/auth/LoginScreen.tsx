import { KeyRound } from "lucide-react";
import { useState } from "react";
import { errorMessage, post } from "../../services/api";

export function LoginScreen({ onLogin }: { onLogin: () => void }) {
  const [token, setToken] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await post("/api/auth/login", { token });
      onLogin();
    } catch (e2) {
      setErr(errorMessage(e2));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="grid-bg flex min-h-full items-center justify-center p-4">
      <form onSubmit={submit} className="panel corner relative w-full max-w-sm space-y-3 p-5">
        <div className="flex items-center gap-2">
          <svg viewBox="0 0 64 64" className="h-8 w-8" aria-hidden><path d="M32 8 L56 30 L45 30 L32 54 L19 30 L8 30 Z" fill="none" stroke="#38d6ff" strokeWidth="4" strokeLinejoin="round" /></svg>
          <div><div className="text-lg font-semibold tracking-[0.3em]">STRIX-1</div><div className="label">Ground station access</div></div>
        </div>
        <p className="text-[12px] text-muted">Enter the access token from <code>STRIX_AUTH_TOKEN</code> in the Pi's <code>.env</code> (printed by the installer).</p>
        <label className="block">
          <span className="label">Access token</span>
          <input className="input mt-1 w-full" type="password" autoComplete="current-password" autoFocus value={token} onChange={(e) => setToken(e.target.value)} />
        </label>
        {err && <div className="text-[12px] text-crit">{err}</div>}
        <button className="btn btn-primary w-full justify-center" disabled={!token || busy}><KeyRound size={13} /> {busy ? "Verifying…" : "Sign in"}</button>
        <p className="text-[10.5px] text-dim">Session cookie is HttpOnly and local-network only. No credentials are stored in the browser.</p>
      </form>
    </div>
  );
}
