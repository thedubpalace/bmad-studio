import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { api, ApiError, onAuthProblem, setCsrfToken } from "../api/client";
import type { SessionData } from "../api/types";

type Status = "loading" | "anonymous" | "authenticated" | "error";

interface SessionState {
  status: Status;
  session: SessionData | null;
  /** 401 ระหว่างใช้งาน: เปิด modal login ใหม่ โดยไม่ unmount หน้าปัจจุบัน (draft ยังอยู่ใน memory) */
  expired: boolean;
  /** 403 csrf_invalid: ต้องโหลดหน้าใหม่ */
  csrfMismatch: boolean;
  loadError: ApiError | null;
  login: (username: string, password: string) => Promise<SessionData>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const SessionContext = createContext<SessionState | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>("loading");
  const [session, setSession] = useState<SessionData | null>(null);
  const [expired, setExpired] = useState(false);
  const [csrfMismatch, setCsrfMismatch] = useState(false);
  const [loadError, setLoadError] = useState<ApiError | null>(null);

  const apply = useCallback((data: SessionData) => {
    setCsrfToken(data.authenticated ? data.csrf ?? null : null);
    setSession(data.authenticated ? data : null);
    setStatus(data.authenticated ? "authenticated" : "anonymous");
  }, []);

  const refresh = useCallback(async () => {
    try {
      apply(await api.get<SessionData>("/api/session", { quietAuth: true }));
      setLoadError(null);
    } catch (error) {
      setLoadError(error instanceof ApiError ? error : null);
      setStatus("error");
    }
  }, [apply]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(
    () =>
      onAuthProblem((problem) => {
        if (problem === "expired") setExpired(true);
        if (problem === "csrf") setCsrfMismatch(true);
      }),
    [],
  );

  const login = useCallback(
    async (username: string, password: string) => {
      const data = await api.post<SessionData>("/api/login", { username, password }, { quietAuth: true });
      apply(data);
      setExpired(false);
      setCsrfMismatch(false);
      return data;
    },
    [apply],
  );

  const logout = useCallback(async () => {
    try {
      await api.post("/api/logout", {}, { quietAuth: true });
    } finally {
      setCsrfToken(null);
      setSession(null);
      setStatus("anonymous");
    }
  }, []);

  const value = useMemo(
    () => ({ status, session, expired, csrfMismatch, loadError, login, logout, refresh }),
    [status, session, expired, csrfMismatch, loadError, login, logout, refresh],
  );
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionState {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used inside SessionProvider");
  return value;
}
