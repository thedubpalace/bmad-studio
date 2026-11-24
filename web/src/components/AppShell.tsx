import type { ReactNode } from "react";
import { NavLink } from "react-router";

import { useSession } from "../auth/SessionContext";
import { th } from "../i18n/th";
import { Icon, type IconName } from "./Icon";
import { CsrfBanner } from "./SessionDialogs";

export interface NavItem {
  to: string;
  label: string;
  icon: IconName;
  end?: boolean;
}

// 5 กลุ่มหลักตาม design.md (Layout & Navigation)
export const NAV_ITEMS: NavItem[] = [
  { to: "/", label: th.nav.projects, icon: "projects", end: true },
  { to: "/flow/default", label: th.nav.defaultFlow, icon: "flow" },
  { to: "/agents", label: th.nav.agents, icon: "agent" },
  { to: "/bmad", label: th.nav.bmad, icon: "bmad" },
  { to: "/changes", label: th.nav.changes, icon: "changes" },
];

function navClass({ isActive }: { isActive: boolean }) {
  return isActive ? "nav-item nav-item--active" : "nav-item";
}

function ReviewLink({ compact = false }: { compact?: boolean }) {
  const { session } = useSession();
  if (!session?.review_url) return null;
  return (
    <a
      className={compact ? "icon-button" : "nav-item nav-item--external"}
      href={session.review_url}
      target="_blank"
      rel="noopener noreferrer"
      title={th.nav.openReviewHint}
      aria-label={compact ? `${th.nav.openReview} — ${th.nav.openReviewHint}` : undefined}
    >
      {compact ? null : <span>{th.nav.openReview}</span>}
      <Icon name="external" size={18} />
    </a>
  );
}

function UserBlock() {
  const { session, logout } = useSession();
  if (!session) return null;
  return (
    <div className="user-block">
      <p className="user-block__name">
        <span className="user-block__label">{th.nav.signedInAs}</span> <span className="mono">{session.username}</span>
      </p>
      <p className="user-block__role">{session.role === "admin" ? th.nav.roleAdmin : th.nav.roleViewer}</p>
      <button type="button" className="nav-item nav-item--button" onClick={() => void logout()}>
        <Icon name="logout" />
        <span>{th.nav.logout}</span>
      </button>
    </div>
  );
}

export function AppShell({ title, children }: { title: string; children: ReactNode }) {
  const { logout } = useSession();
  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        {th.nav.skipToContent}
      </a>
      <aside className="sidenav glass" aria-label={th.nav.label}>
        <p className="sidenav__brand">{th.appName}</p>
        <nav className="sidenav__nav">
          {NAV_ITEMS.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end} className={navClass}>
              <Icon name={item.icon} />
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidenav__footer">
          <ReviewLink />
          <UserBlock />
        </div>
      </aside>

      <header className="topbar glass">
        <h1 className="topbar__title">{title}</h1>
        <div className="topbar__actions">
          <ReviewLink compact />
          <button type="button" className="icon-button" onClick={() => void logout()} aria-label={th.nav.logout}>
            <Icon name="logout" />
          </button>
        </div>
      </header>

      <main id="main" className="content" tabIndex={-1}>
        <CsrfBanner />
        <h1 className="page-title">{title}</h1>
        {children}
      </main>

      <nav className="tabbar glass" aria-label={th.nav.label}>
        {NAV_ITEMS.map((item) => (
          <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => (isActive ? "tab tab--active" : "tab")}>
            <Icon name={item.icon} />
            <span className="tab__label">{item.label}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
