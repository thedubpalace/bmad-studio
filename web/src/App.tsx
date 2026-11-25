import { Navigate, Route, Routes, useLocation } from "react-router";

import { useSession } from "./auth/SessionContext";
import { ChangeReview } from "./changes/ChangeReview";
import { ChangeDetail, ChangesPage } from "./changes/ChangesPage";
import { AppShell } from "./components/AppShell";
import { EmptyState } from "./components/EmptyState";
import { SessionExpiredModal } from "./components/SessionDialogs";
import { th } from "./i18n/th";
import { BmadOverview } from "./pages/BmadOverview";
import { LoginPage } from "./pages/LoginPage";
import { DefaultFlowEditorPage } from "./flows/DefaultFlowEditorPage";
import { ProjectFlowEditorPage } from "./flows/ProjectFlowEditorPage";
import { ProjectFlowPage } from "./projects/ProjectFlowPage";
import { ProjectsPage } from "./projects/ProjectsPage";

function Section({ title, empty }: { title: string; empty: { title: string; body: string } }) {
  return (
    <AppShell title={title}>
      <section className="panel glass">
        <EmptyState title={empty.title} body={empty.body} />
      </section>
    </AppShell>
  );
}

function Splash() {
  const { status, refresh } = useSession();
  return (
    <main className="login-page">
      <section className="login-card glass" aria-live="polite">
        {status === "error" ? (
          <>
            <p className="form-error">{th.errors.offline}</p>
            <button type="button" className="button button--primary button--block" onClick={() => void refresh()}>
              {th.errors.retry}
            </button>
          </>
        ) : (
          <p>{th.session.loading}</p>
        )}
      </section>
    </main>
  );
}

export function App() {
  const { status } = useSession();
  const location = useLocation();

  if (status === "loading" || status === "error") return <Splash />;

  if (status === "anonymous") {
    if (location.pathname === "/login") return <LoginPage />;
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?next=${next}`} replace />;
  }

  return (
    <>
      <Routes>
        <Route path="/login" element={<Navigate to="/" replace />} />
        <Route
          path="/"
          element={
            <AppShell title={th.nav.projects}>
              <ProjectsPage />
            </AppShell>
          }
        />
        <Route
          path="/p/:name"
          element={
            <AppShell title={th.nav.projects}>
              <ProjectFlowPage />
            </AppShell>
          }
        />
        <Route
          path="/p/:name/flow"
          element={
            <AppShell title={th.nav.projects}>
              <ProjectFlowEditorPage />
            </AppShell>
          }
        />
        <Route path="/p/*" element={<Section title={th.empty.notFound.title} empty={th.empty.notFound} />} />
        <Route
          path="/flow/default"
          element={
            <AppShell title={th.nav.defaultFlow}>
              <DefaultFlowEditorPage />
            </AppShell>
          }
        />
        <Route path="/agents/*" element={<Section title={th.nav.agents} empty={th.empty.agents} />} />
        <Route
          path="/bmad"
          element={
            <AppShell title={th.nav.bmad}>
              <BmadOverview />
            </AppShell>
          }
        />
        <Route path="/bmad/*" element={<Section title={th.nav.bmad} empty={th.empty.bmad} />} />
        <Route
          path="/changes/review"
          element={
            <AppShell title={th.nav.changes}>
              <ChangeReview />
            </AppShell>
          }
        />
        <Route
          path="/changes/:changeId"
          element={
            <AppShell title={th.nav.changes}>
              <ChangeDetail />
            </AppShell>
          }
        />
        <Route
          path="/changes"
          element={
            <AppShell title={th.nav.changes}>
              <ChangesPage />
            </AppShell>
          }
        />
        <Route path="*" element={<Section title={th.empty.notFound.title} empty={th.empty.notFound} />} />
      </Routes>
      <SessionExpiredModal />
    </>
  );
}
