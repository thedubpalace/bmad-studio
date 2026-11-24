import { useCallback, useEffect, useState } from "react";

import { api, ApiError } from "../api/client";
import type { Inventory } from "../api/types";
import { Icon } from "../components/Icon";
import { th } from "../i18n/th";

type State = { kind: "loading"; slow: boolean } | { kind: "ready"; data: Inventory } | { kind: "error"; message: string };

function StatusLine({ ok, okText, badText }: { ok: boolean; okText: string; badText: string }) {
  return (
    <li className={ok ? "status status--ok" : "status status--bad"}>
      <Icon name={ok ? "check" : "cross"} size={18} />
      <span>{ok ? okText : badText}</span>
    </li>
  );
}

/** หน้า /bmad — ภาพรวม BMAD ที่ติดตั้ง (REQ-039) */
export function BmadOverview() {
  const [state, setState] = useState<State>({ kind: "loading", slow: false });

  const load = useCallback(async () => {
    setState({ kind: "loading", slow: false });
    const slowTimer = window.setTimeout(() => setState((s) => (s.kind === "loading" ? { kind: "loading", slow: true } : s)), 400);
    try {
      const data = await api.get<Inventory>("/api/bmad/inventory");
      setState({ kind: "ready", data });
    } catch (error) {
      setState({ kind: "error", message: error instanceof ApiError ? error.message : th.bmad.loadFailed });
    } finally {
      window.clearTimeout(slowTimer);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (state.kind === "loading") {
    return (
      <section className="panel glass" aria-busy="true" aria-live="polite">
        {state.slow ? <p className="muted">{th.bmad.loading}</p> : null}
      </section>
    );
  }

  if (state.kind === "error") {
    return (
      <section className="panel surface-solid error-panel" role="alert">
        <p className="error-panel__title">
          <Icon name="alert" />
          <span>{th.bmad.loadFailed}</span>
        </p>
        <p>{state.message}</p>
        <button type="button" className="button button--secondary" onClick={() => void load()}>
          {th.errors.retry}
        </button>
      </section>
    );
  }

  const { data } = state;
  const agents = data.config.agents ?? [];
  const configError = data.config.error;

  return (
    <>
      <section className="panel glass overview" aria-labelledby="bmad-version">
        <div className="overview__version">
          <p className="overview__label">{th.bmad.versionLabel}</p>
          <p id="bmad-version" className="overview__number mono">
            {data.version ?? th.bmad.versionUnknown}
          </p>
          <p className="overview__counts">
            <span>{th.bmad.skillCount(data.skills.length)}</span>
            <span aria-hidden="true">·</span>
            <span>{data.config.ok ? th.bmad.agentCount(agents.length) : th.bmad.agentCountUnknown}</span>
          </p>
        </div>
        <div className="overview__tools">
          <h2 className="section-title">{th.bmad.toolsTitle}</h2>
          <ul className="status-list">
            <StatusLine ok={data.tools.uv} okText={th.bmad.uvOk} badText={th.bmad.uvMissing} />
            <StatusLine ok={data.config.ok} okText={th.bmad.scriptOk} badText={th.bmad.scriptFailed} />
          </ul>
        </div>
      </section>

      {configError ? (
        <section className="panel surface-solid error-panel" role="alert">
          <p className="error-panel__title">
            <Icon name="alert" />
            <span>{th.bmad.configErrorTitle}</span>
          </p>
          <p>{configError.error}</p>
          <p className="muted">{th.bmad.configErrorHint}</p>
          <button type="button" className="button button--secondary" onClick={() => void load()}>
            {th.errors.retry}
          </button>
        </section>
      ) : null}

      <section className="panel glass" aria-labelledby="bmad-skills">
        <h2 id="bmad-skills" className="section-title">
          {th.bmad.skillsTitle}
        </h2>
        {data.skills.length === 0 ? (
          <p className="muted">{th.bmad.noSkills}</p>
        ) : (
          <ul className="item-list">
            {data.skills.map((skill) => (
              <li key={skill.id} className="item">
                <p className="item__name mono">{skill.id}</p>
                <p className="item__desc">{skill.description || th.bmad.noDescription}</p>
                <p className="item__tags">
                  {skill.override_team ? <span className="tag tag--warn">{th.bmad.overrideTeam}</span> : null}
                  {skill.override_user ? <span className="tag tag--warn">{th.bmad.overrideUser}</span> : null}
                  {!skill.override_team && !skill.override_user ? <span className="tag">{th.bmad.noOverride}</span> : null}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>

      {data.config.ok ? (
        <section className="panel glass" aria-labelledby="bmad-agents">
          <h2 id="bmad-agents" className="section-title">
            {th.bmad.agentsTitle}
          </h2>
          {agents.length === 0 ? (
            <p className="muted">{th.bmad.noAgents}</p>
          ) : (
            <ul className="item-list">
              {agents.map((agent) => (
                <li key={agent.id} className="item">
                  <p className="item__name">
                    <span className="mono">{agent.id}</span>
                    {agent.name ? <span className="muted"> · {agent.name}</span> : null}
                    {agent.title ? <span className="muted"> · {agent.title}</span> : null}
                  </p>
                  {agent.description ? <p className="item__desc">{agent.description}</p> : null}
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : null}
    </>
  );
}
