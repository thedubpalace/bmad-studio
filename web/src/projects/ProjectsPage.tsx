import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router";

import { api, ApiError } from "../api/client";
import type { ProjectSummary } from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { Icon } from "../components/Icon";
import { th } from "../i18n/th";
import { nextText, STATUS_ICON, statusText } from "./stageView";

type State = { kind: "loading"; slow: boolean } | { kind: "ready"; data: ProjectSummary[] } | { kind: "error"; message: string };
type Group = "human" | "working" | "broken" | "done";
const GROUP_ORDER: Group[] = ["human", "working", "broken", "done"];

export function groupOf(item: ProjectSummary): Group {
  if (item.error) return "broken";
  if (item.complete) return "done";
  // ไม่มีขั้นถัดไปแต่ยังไม่จบ (เช่น ติด gate) = ต้องดู ไม่ใช่เสร็จ
  return item.next?.gate === "human" ? "human" : "working";
}

function StageChips({ item }: { item: ProjectSummary }) {
  return (
    <ol className="stage-chips" aria-label={th.projects.stagesLabel(item.project)}>
      {item.stages.map((stage) => {
        const status = stage.status;
        const label = `${stage.id}: ${statusText(status)}${stage.skipReason ? ` — ${stage.skipReason}` : ""}`;
        return (
          <li key={stage.id} className={`stage-chip stage-chip--${status}`} title={label}>
            <Icon name={STATUS_ICON[status]} size={14} />
            <span className="sr-only">{label}</span>
            <span aria-hidden="true" className="stage-chip__id mono">
              {stage.id}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function ProjectRow({ item }: { item: ProjectSummary }) {
  return (
    <li className="project-row">
      <div className="project-row__head">
        <Link className="project-row__name" to={`/p/${encodeURIComponent(item.project)}`}>
          {item.project}
        </Link>
        {item.overridden ? (
          <span className="tag tag--warn" title={th.projects.overriddenHint}>
            {th.projects.overridden}
          </span>
        ) : null}
        {item.status ? <span className="tag mono">{item.status}</span> : null}
      </div>
      <p className="project-row__next">
        {item.error ? (
          <>
            <Icon name="alert" size={16} />
            <span>{th.projects.flowInvalid}</span>
            <span className="path mono">({item.error.source})</span>
          </>
        ) : item.next ? (
          <>
            <span className="muted">{th.projects.nextLabel}:</span> <span>{nextText(item.next)}</span>
          </>
        ) : item.complete ? (
          <span className="muted">{th.projects.complete}</span>
        ) : (
          <span>{th.projects.noNext}</span>
        )}
      </p>
      {item.stages.length ? <StageChips item={item} /> : null}
    </li>
  );
}

/** หน้า / — รายการโปรเจกต์จัดกลุ่มตามสถานะ flow (REQ-050, UX-DR8) อ่านอย่างเดียว */
export function ProjectsPage() {
  const [state, setState] = useState<State>({ kind: "loading", slow: false });
  const [query, setQuery] = useState("");
  const [hideDone, setHideDone] = useState(false);

  const load = useCallback(async () => {
    setState({ kind: "loading", slow: false });
    const slowTimer = window.setTimeout(() => setState((s) => (s.kind === "loading" ? { kind: "loading", slow: true } : s)), 400);
    try {
      setState({ kind: "ready", data: await api.get<ProjectSummary[]>("/api/projects") });
    } catch (error) {
      setState({ kind: "error", message: error instanceof ApiError ? error.message : th.projects.loadFailed });
    } finally {
      window.clearTimeout(slowTimer);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = useMemo(() => {
    if (state.kind !== "ready") return [];
    const q = query.trim().toLowerCase();
    return state.data.filter((item) => {
      if (hideDone && groupOf(item) === "done") return false;
      if (!q) return true;
      return item.project.toLowerCase().includes(q) || (item.description ?? "").toLowerCase().includes(q);
    });
  }, [state, query, hideDone]);

  if (state.kind === "loading") {
    return (
      <section className="panel glass" aria-busy="true" aria-live="polite">
        {state.slow ? <p className="muted">{th.projects.loading}</p> : null}
      </section>
    );
  }
  if (state.kind === "error") {
    return (
      <section className="panel surface-solid error-panel" role="alert">
        <p className="error-panel__title">
          <Icon name="alert" />
          <span>{th.projects.loadFailed}</span>
        </p>
        <p>{state.message}</p>
        <button type="button" className="button button--secondary" onClick={() => void load()}>
          {th.errors.retry}
        </button>
      </section>
    );
  }
  if (state.data.length === 0) {
    return (
      <section className="panel glass">
        <EmptyState title={th.projects.emptyRegistry.title} body={th.projects.emptyRegistry.body} />
      </section>
    );
  }

  return (
    <section className="panel glass projects" aria-labelledby="projects-count">
      <div className="projects__filters">
        <label className="field projects__search">
          <span className="field__label">{th.projects.search}</span>
          <input
            className="input"
            type="search"
            value={query}
            placeholder={th.projects.searchPlaceholder}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <label className="check">
          <input type="checkbox" checked={hideDone} onChange={(event) => setHideDone(event.target.checked)} />
          <span>{th.projects.hideDone}</span>
        </label>
      </div>
      <p id="projects-count" className="muted" aria-live="polite">
        {th.projects.count(filtered.length, state.data.length)}
      </p>
      {filtered.length === 0 ? (
        <EmptyState title={th.projects.none.title} body={th.projects.none.body} />
      ) : (
        GROUP_ORDER.map((group) => {
          const items = filtered.filter((item) => groupOf(item) === group);
          if (!items.length) return null;
          return (
            <div key={group} className="project-group">
              <h2 className="section-title">
                {th.projects.groups[group]} <span className="muted">({items.length})</span>
              </h2>
              <ul className="project-list">
                {items.map((item) => (
                  <ProjectRow key={item.project} item={item} />
                ))}
              </ul>
            </div>
          );
        })
      )}
    </section>
  );
}
