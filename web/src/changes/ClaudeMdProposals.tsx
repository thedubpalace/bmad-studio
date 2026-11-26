// /changes ส่วน "ข้อเสนอผูก flow กับ CLAUDE.md" (design.md "ClaudeMdProposal (049)", REQ-049, story 5-2)
// สถานะทุกตัวมาจาก server (core.claude_md) — หน้านี้แสดงผลอย่างเดียว; การใช้ข้อเสนอ = preview ของ pipeline
import { useCallback, useEffect, useId, useState } from "react";
import { useNavigate } from "react-router";

import { api, ApiError } from "../api/client";
import type { AgentGuardSuggestion, ClaudeMdBlock, ClaudeMdProposals as Proposals } from "../api/types";
import { Icon } from "../components/Icon";
import { th } from "../i18n/th";
import { startChange } from "./changeStore";
import { DiffView } from "./DiffView";

const t = th.claudeMd;

function stateTag(state: ClaudeMdBlock["state"]) {
  const warn = state === "stale" || state === "invalid" || state === "unavailable";
  return <span className={warn ? "tag tag--warn" : "tag"}>{t.state[state]}</span>;
}

function BlockRow({ item, base }: { item: ClaudeMdBlock; base: string | null }) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reasonId = useId();
  const usable = item.state === "missing" || item.state === "stale";
  return (
    <li className="item proposal-item">
      <div className="proposal-item__main">
        <p className="item__name">{t.blocks[item.block].title}</p>
        <p className="item__desc">{t.blocks[item.block].body}</p>
        <p className="mono hint">{`bmad-studio:${item.block}`}</p>
      </div>
      <div className="proposal-item__actions">
        {stateTag(item.state)}
        <button
          type="button"
          className="button button--secondary"
          aria-disabled={busy || !usable}
          aria-describedby={usable ? undefined : reasonId}
          onClick={() => {
            if (busy || !usable) return;
            setBusy(true);
            setError(null);
            startChange({ kind: "claude-md", target: "CLAUDE.md", payload: { block: item.block }, base_sha256: base }, "/changes").then(
              () => navigate("/changes/review?step=review"),
              (cause: unknown) => {
                setBusy(false);
                setError(cause instanceof ApiError ? cause.message : th.errors.unexpected);
              },
            );
          }}
        >
          {busy ? t.opening : t.viewDiff}
        </button>
        {item.state === "stale" ? <p className="hint">{t.staleHint}</p> : null}
        {usable ? null : (
          <p id={reasonId} className="hint">
            {t.disabledReason[item.state]}
          </p>
        )}
        {error ? (
          <p className="form-error" role="alert">
            {error}
          </p>
        ) : null}
      </div>
    </li>
  );
}

function GuardRow({ item }: { item: AgentGuardSuggestion }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  return (
    <li className="item proposal-item">
      <div className="proposal-item__main">
        <p className="item__name">{item.agent}</p>
        <p className="mono path">{item.path}</p>
        <ul className="proposal-item__guards">
          {item.guards.map((g) => (
            <li key={g.line}>
              <span className="mono">{t.guardLine(g.line)}</span> <code>{g.text}</code>
              <span className="hint"> {t.guardStage(g.stage)}</span>
              {g.manual ? <p className="hint">{t.guardManual((g.otherGates ?? []).join(", "))}</p> : null}
            </li>
          ))}
        </ul>
      </div>
      <div className="proposal-item__actions">
        <span className={item.state === "applied" ? "tag" : "tag tag--warn"}>{t.guardState[item.state]}</span>
        {item.diff ? (
          <button type="button" className="button button--secondary" aria-expanded={open} aria-controls={panelId} onClick={() => setOpen(!open)}>
            {open ? t.hideDiff : t.viewDiff}
          </button>
        ) : null}
      </div>
      {item.diff && open ? (
        <div id={panelId} className="proposal-item__diff">
          <p className="hint">{t.guardReadOnly}</p>
          <DiffView diff={item.diff} path={item.path} mode="unified" wrap />
        </div>
      ) : null}
    </li>
  );
}

export function ClaudeMdProposals() {
  const [data, setData] = useState<Proposals | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    setError(null);
    api.get<Proposals>("/api/claude-md/proposals").then(setData, (cause: unknown) => {
      setError(cause instanceof ApiError ? cause.message : th.errors.unexpected);
    });
  }, []);
  useEffect(load, [load]);

  return (
    <section className="panel glass proposals" aria-labelledby="claude-md-proposals">
      <h2 id="claude-md-proposals" className="section-title">
        {t.title}
      </h2>
      <p className="proposals__note">
        <Icon name="lock" size={18} />
        <span>{t.untouched}</span>
      </p>
      {error ? (
        <div className="error-panel" role="alert">
          <p className="error-panel__title">
            <Icon name="alert" /> {t.loadFailed}
          </p>
          <p>{error}</p>
          <button type="button" className="button button--secondary" onClick={load}>
            {th.errors.retry}
          </button>
        </div>
      ) : data === null ? (
        <p className="muted" aria-busy="true">
          {t.loading}
        </p>
      ) : (
        <>
          {data.issues && data.issues.length > 0 ? (
            <section className="issue-box issue-box--error" aria-label={t.issuesTitle}>
              <p className="issue-box__title">{t.issuesTitle}</p>
              <ul>
                {data.issues.map((issue, i) => (
                  <li key={`${issue.code}-${i}`}>{issue.error}</li>
                ))}
              </ul>
            </section>
          ) : null}
          {data.resolverProjectFound === false ? (
            <p className="form-error" role="alert">
              {t.projectMissing(data.command)}
            </p>
          ) : null}
          <ul className="item-list">
            {data.blocks.map((item) => (
              <BlockRow key={item.block} item={item} base={data.base_sha256 ?? null} />
            ))}
          </ul>
          <h3 className="proposals__subtitle">{t.guardsTitle}</h3>
          <p className="hint">{t.guardsIntro}</p>
          {data.agentGuards.length === 0 ? (
            <p className="muted">{t.noGuards}</p>
          ) : (
            <ul className="item-list">
              {data.agentGuards.map((item) => (
                <GuardRow key={item.agent} item={item} />
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
