import { useEffect, useId, useRef, useState, type FormEvent } from "react";

import { describeLoginError, formatCountdown } from "../auth/loginErrors";
import { useSession } from "../auth/SessionContext";
import { th } from "../i18n/th";
import { Icon } from "./Icon";

/** ฟอร์ม login ใช้ทั้งหน้า /login และ modal "หมดเวลาเข้าสู่ระบบ" (UX-DR3) */
export function LoginForm({ onSuccess, autoFocus = true }: { onSuccess?: () => void; autoFocus?: boolean }) {
  const { login } = useSession();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [blockedUntil, setBlockedUntil] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const errorId = useId();
  const userRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (autoFocus) userRef.current?.focus();
  }, [autoFocus]);

  useEffect(() => {
    if (blockedUntil === null) return;
    const timer = window.setInterval(() => {
      const current = Date.now();
      setNow(current);
      if (current >= blockedUntil) {
        setBlockedUntil(null);
        setMessage(null);
      }
    }, 250);
    return () => window.clearInterval(timer);
  }, [blockedUntil]);

  const remaining = blockedUntil === null ? 0 : Math.max(0, (blockedUntil - now) / 1000);
  const blocked = blockedUntil !== null && remaining > 0;
  const shownMessage = blocked ? th.login.rateLimited(formatCountdown(remaining)) : message;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || blocked) return;
    if (!username.trim() || !password) {
      setMessage(th.login.required);
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      await login(username.trim(), password);
      setPassword("");
      onSuccess?.();
    } catch (error) {
      const failure = describeLoginError(error);
      setMessage(failure.message);
      if (failure.retryAfter !== null) {
        const start = Date.now();
        setNow(start);
        setBlockedUntil(start + failure.retryAfter * 1000);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="login-form" onSubmit={submit} noValidate aria-describedby={shownMessage ? errorId : undefined}>
      <label className="field">
        <span className="field__label">{th.login.username}</span>
        <input
          ref={userRef}
          className="input"
          name="username"
          autoComplete="username"
          autoCapitalize="none"
          spellCheck={false}
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />
      </label>
      <label className="field">
        <span className="field__label">{th.login.password}</span>
        <span className="input-with-button">
          <input
            className="input"
            name="password"
            type={showPassword ? "text" : "password"}
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <button
            type="button"
            className="icon-button"
            onClick={() => setShowPassword((v) => !v)}
            aria-pressed={showPassword}
            aria-label={showPassword ? th.login.hidePassword : th.login.showPassword}
          >
            <Icon name={showPassword ? "eyeOff" : "eye"} />
          </button>
        </span>
      </label>
      <p id={errorId} className="form-error" role="alert" aria-live="assertive">
        {shownMessage ? (
          <>
            <Icon name="alert" size={18} />
            <span>{shownMessage}</span>
          </>
        ) : null}
      </p>
      <button className="button button--primary button--block" type="submit" disabled={busy || blocked} aria-disabled={busy || blocked}>
        {busy ? th.login.submitting : th.login.submit}
      </button>
    </form>
  );
}
