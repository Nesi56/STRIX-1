import { Delete, KeyRound, Lock } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { ApiError, errorMessage, get, post } from "../../services/api";
import { Wordmark, useUtcClock } from "../common/Brand";

interface AuthStatus { pin_enabled?: boolean; locked_for_s?: number }

/** Recovery / drone login: the long access token. */
function TokenForm({ hub, onLogin, onBack }: { hub: boolean; onLogin: () => void; onBack?: () => void }) {
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
    <form onSubmit={submit} className="space-y-3">
      <p className="text-[13px] text-muted">
        {hub ? <>Enter the hub's access token. On the hub computer, run <code className="num text-text">strix-hub token hub.toml</code>.</>
          : <>Enter the access token from <code className="num text-text">STRIX_AUTH_TOKEN</code> in the Pi's <code className="num text-text">.env</code>.</>}
      </p>
      <label className="block">
        <span className="tick-label">Access token</span>
        <input className="input num mt-1 w-full" type="password" autoComplete="current-password" autoFocus value={token} onChange={(e) => setToken(e.target.value)} />
      </label>
      {err && <div role="alert" className="text-[12.5px] text-crit">{err}</div>}
      <button className="btn btn-primary h-10 w-full justify-center" disabled={!token || busy}><KeyRound size={14} /> {busy ? "Verifying…" : "Sign in"}</button>
      {onBack && <button type="button" className="w-full text-center text-[12px] text-muted hover:text-text" onClick={onBack}>Back to PIN</button>}
    </form>
  );
}

/** Operator PIN pad. Keyboard digits, Backspace and Enter work too. */
function PinPad({ onLogin, initialLock, onToken }: { onLogin: () => void; initialLock: number; onToken: () => void }) {
  const [pin, setPin] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [shake, setShake] = useState(0);
  const [lockedUntil, setLockedUntil] = useState(initialLock ? Date.now() + initialLock * 1000 : 0);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (lockedUntil <= Date.now()) return;
    const t = window.setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(t);
  }, [lockedUntil]);
  const lockLeft = Math.max(0, Math.ceil((lockedUntil - now) / 1000));
  const locked = lockLeft > 0;

  const submit = useCallback(async (code: string) => {
    if (code.length < 6 || busy || locked) return;
    setBusy(true);
    setMsg(null);
    try {
      await post("/api/auth/login", { pin: code });
      onLogin();
    } catch (e) {
      setPin("");
      setShake((s) => s + 1);
      const d = e instanceof ApiError ? (e as ApiError & { raw?: unknown }) : null;
      if (d?.code === "locked") {
        const s = Number(/(\d+) s/.exec(d.message)?.[1] ?? 60);
        setLockedUntil(Date.now() + s * 1000);
        setNow(Date.now());
        setMsg(null);
      } else setMsg(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }, [busy, locked, onLogin]);

  const press = useCallback((k: string) => {
    if (locked || busy) return;
    if (k === "clr") return setPin("");
    if (k === "del") return setPin((p) => p.slice(0, -1));
    if (k === "ok") return void submit(pin);
    setPin((p) => (p.length >= 8 ? p : p + k));
  }, [busy, locked, pin, submit]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (/^[0-9]$/.test(e.key)) press(e.key);
      else if (e.key === "Backspace") press("del");
      else if (e.key === "Escape") press("clr");
      else if (e.key === "Enter") press("ok");
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [press]);

  return (
    <div>
      <div className="tick-label mb-2">Operator PIN</div>
      <div key={shake} className={`mb-3 flex justify-center gap-2 ${shake ? "shake" : ""}`} aria-label={`${pin.length} digits entered`}>
        {Array.from({ length: 8 }, (_, i) => (
          <span key={i} className={`flex h-11 w-9 items-center justify-center border ${i < pin.length ? "border-accent bg-accent/10" : i < 6 ? "border-line-2" : "border-line border-dashed"}`}>
            {i < pin.length && <span className="h-2.5 w-2.5 rounded-full bg-accent" />}
          </span>
        ))}
      </div>
      <div className="mb-3 min-h-[20px] text-center text-[12.5px]" role="status">
        {locked ? <span className="text-crit"><Lock size={12} className="mr-1 inline" />Locked after wrong PINs · try again in <span className="num">{lockLeft}s</span></span>
          : msg ? <span className="text-crit">{msg}</span>
            : <span className="text-dim">6–8 digits · keyboard works too</span>}
      </div>
      <div className="grid grid-cols-3 gap-1.5">
        {["1", "2", "3", "4", "5", "6", "7", "8", "9"].map((k) => (
          <button key={k} type="button" className="pad-key" disabled={locked || busy} onClick={() => press(k)}>{k}</button>
        ))}
        <button type="button" className="pad-key !text-[12px] tracking-[0.14em] text-muted" disabled={locked || busy} onClick={() => press("clr")}>CLR</button>
        <button type="button" className="pad-key" disabled={locked || busy} onClick={() => press("0")}>0</button>
        <button type="button" className="pad-key" aria-label="Delete" disabled={locked || busy} onClick={() => press("del")}><Delete size={18} /></button>
      </div>
      <button type="button" className="btn btn-primary mt-2 h-11 w-full justify-center text-[13px] tracking-[0.2em]" disabled={pin.length < 6 || locked || busy}
        onClick={() => press("ok")}>{busy ? "VERIFYING…" : "AUTHENTICATE"}</button>
      <button type="button" className="mt-3 w-full text-center text-[12px] text-muted hover:text-text" onClick={onToken}>Use recovery token instead</button>
    </div>
  );
}

export function LoginScreen({ onLogin, hub = false }: { onLogin: () => void; hub?: boolean }) {
  const clock = useUtcClock();
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [useToken, setUseToken] = useState(false);
  useEffect(() => {
    get<AuthStatus>("/api/auth/status").then(setStatus, () => setStatus({}));
  }, []);
  const pinMode = hub && status?.pin_enabled && !useToken;

  return (
    <div className="grid-bg scan relative flex min-h-full flex-col">
      <div className="flex items-center justify-between border-b border-line px-4 py-2 text-[11px] tracking-[0.16em] text-dim">
        <span>{hub ? "HUB" : "DRONE"} · LOCAL NETWORK · NO INTERNET REQUIRED</span>
        <span className="num text-muted">{clock}</span>
      </div>
      <div className="flex flex-1 items-center justify-center p-4">
        <div className="panel corner relative w-full max-w-[360px] p-5">
          <div className="mb-5"><Wordmark sub={hub ? "OPERATOR ACCESS · ALL DRONES" : "OPERATOR ACCESS"} /></div>
          {status === null ? <div className="h-40" />
            : pinMode ? <PinPad onLogin={onLogin} initialLock={status.locked_for_s ?? 0} onToken={() => setUseToken(true)} />
              : <TokenForm hub={hub} onLogin={onLogin} onBack={hub && status.pin_enabled ? () => setUseToken(false) : undefined} />}
          {hub && status && !status.pin_enabled && (
            <p className="mt-4 border-t border-line pt-3 text-[11.5px] text-dim">Tip: set a short operator PIN on the hub computer with <code className="num text-muted">strix-hub set-pin hub.toml</code>.</p>
          )}
        </div>
      </div>
      <div className="px-4 pb-3 text-center text-[11px] tracking-[0.12em] text-dim">AUTHORIZED OPERATORS ONLY · SESSION COOKIE IS HTTPONLY · WRONG PINS LOCK SIGN-IN</div>
    </div>
  );
}
