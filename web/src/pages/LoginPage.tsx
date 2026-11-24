import { useNavigate, useSearchParams } from "react-router";

import { LoginForm } from "../components/LoginForm";
import { th } from "../i18n/th";

/** ยอมให้กลับเฉพาะ path ภายในแอป (กัน open redirect) */
export function safeNext(next: string | null): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\") || next.startsWith("/login")) return "/";
  return next;
}

export function LoginPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  return (
    <main className="login-page">
      <section className="login-card glass" aria-labelledby="login-title">
        <h1 id="login-title" className="login-card__title">
          {th.login.title}
        </h1>
        <p className="login-card__subtitle">{th.login.subtitle}</p>
        <LoginForm onSuccess={() => navigate(safeNext(params.get("next")), { replace: true })} />
      </section>
    </main>
  );
}
