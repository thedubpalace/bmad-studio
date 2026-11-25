import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";

import { api, ApiError } from "../api/client";
import type { FlowStatus, StageRow } from "../api/types";
import { Icon } from "../components/Icon";
import { th } from "../i18n/th";
import { gateText, nextText, StatusBadge } from "./stageView";

type FlowIssue = { code: string; error: string; stage?: string | null };
type State =
  | { kind: "loading"; slow: boolean }
  | { kind: "ready"; data: FlowStatus }
  | { kind: "invalid"; issues: FlowIssue[]; source: string | null }
  | { kind: "error"; message: string; notFound: boolean };

function ReviewLink({ href }: { href: string }) {
  return (
    <a className="button button--secondary stage-row__review" href={href} target="_blank" rel="noopener noreferrer"
       title={th.flow.goReviewHint}>
      <Icon name="external" size={18} />
      <span>{th.flow.goReview}</span>
      <span className="sr-only"> — {th.flow.goReviewHint}</span>
    </a>
  );
}

function StageItem({ stage, reviewUrl }: { stage: StageRow; reviewUrl: string | null }) {
  const status = stage.status;
  const reason = stage.skipReason ?? stage.note;
  const waitingHuman = stage.gate === "human" && status === "active";
  return (
    <li className={`stage-row stage-row--${status}${stage.locked ? " stage-row--locked" : ""}`}>
      <div className="stage-row__main">
        <StatusBadge status={status} />
        <span className="stage-row__id mono">{stage.id}</span>
        <span className="stage-row__agent">{stage.agent === "human" ? th.flow.gate.human : stage.agent}</span>
        {stage.skill ? <span className="tag mono">{stage.skill}</span> : null}
        {stage.gate !== "none" ? (
          <span className="tag">
            {stage.gate === "human" ? <Icon name="person" size={14} /> : null} {th.flow.gateLabel}: {gateText(stage.gate)}
          </span>
        ) : null}
        {stage.optional ? <span className="tag">{th.flow.optional}</span> : null}
        {stage.locked ? (
          <span className="tag tag--lock">
            <Icon name="lock" size={14} /> {th.flow.locked}
          </span>
        ) : null}
        {stage.overridden ? <span className="tag tag--warn">{th.flow.overridden}</span> : null}
      </div>
      {reason || stage.lockReason ? (
        <p className="stage-row__reason">
          {reason}
          {stage.locked && stage.lockReason ? <span className="stage-row__lock-reason">{stage.lockReason}</span> : null}
        </p>
      ) : null}
      {waitingHuman && reviewUrl ? <ReviewLink href={reviewUrl} /> : null}
    </li>
  );
}

/** หน้า /p/<name> — สถานะ flow ของโปรเจกต์ (REQ-050, UX-DR8): อ่านอย่างเดียว ไม่มีปุ่มอนุมัติ */
export function ProjectFlowPage() {
  const { name = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const track = params.get("track") === "quick" ? "quick" : "full";
  const [state, setState] = useState<State>({ kind: "loading", slow: false });
  const requestId = useRef(0);

  const load = useCallback(async () => {
    // คำขอที่ช้ากว่าและเก่ากว่า (สลับ track/โปรเจกต์เร็ว ๆ) ต้องไม่ทับผลของคำขอล่าสุด
    const id = ++requestId.current;
    const set = (next: State | ((s: State) => State)) => {
      if (id === requestId.current) setState(next);
    };
    set({ kind: "loading", slow: false });
    const slowTimer = window.setTimeout(() => set((s) => (s.kind === "loading" ? { kind: "loading", slow: true } : s)), 400);
    try {
      const data = await api.get<FlowStatus>(`/api/projects/${encodeURIComponent(name)}/flow-status?track=${track}`);
      set({ kind: "ready", data });
    } catch (error) {
      if (error instanceof ApiError && error.code === "flow_invalid") {
        const details = (error.details ?? {}) as { issues?: FlowIssue[]; source?: string };
        set({ kind: "invalid", issues: details.issues ?? [], source: details.source ?? null });
      } else {
        set({
          kind: "error",
          message: error instanceof ApiError ? error.message : th.flow.loadFailed,
          notFound: error instanceof ApiError && error.status === 404,
        });
      }
    } finally {
      window.clearTimeout(slowTimer);
    }
  }, [name, track]);

  useEffect(() => {
    void load();
  }, [load]);

  const header = (
    <div className="flow-head">
      <Link className="flow-head__back" to="/">
        ← {th.flow.back}
      </Link>
      <h2 id="flow-project-name" className="flow-head__name mono">
        {name}
      </h2>
      <div className="segmented" role="group" aria-label={th.flow.track}>
        {(["full", "quick"] as const).map((t) => (
          <button key={t} type="button" className="segmented__item" aria-pressed={track === t}
                  onClick={() => setParams(t === "full" ? {} : { track: t })}>
            {t === "full" ? th.flow.trackFull : th.flow.trackQuick}
          </button>
        ))}
      </div>
    </div>
  );

  if (state.kind === "loading") {
    return (
      <section className="panel glass" aria-busy="true" aria-live="polite" aria-labelledby="flow-project-name">
        {header}
        {state.slow ? <p className="muted">{th.flow.loading}</p> : null}
      </section>
    );
  }
  if (state.kind === "error") {
    return (
      <section className="panel surface-solid error-panel" role="alert" aria-labelledby="flow-project-name">
        {header}
        <p className="error-panel__title">
          <Icon name="alert" />
          <span>{state.notFound ? th.flow.notFound : th.flow.loadFailed}</span>
        </p>
        {!state.notFound ? (
          <>
            <p>{state.message}</p>
            <button type="button" className="button button--secondary" onClick={() => void load()}>
              {th.errors.retry}
            </button>
          </>
        ) : null}
      </section>
    );
  }
  if (state.kind === "invalid") {
    return (
      <section className="panel surface-solid" role="alert" aria-labelledby="flow-project-name">
        {header}
        <div className="issue-box issue-box--error">
          <p className="issue-box__title">
            <Icon name="alert" /> {th.flow.invalidTitle}
          </p>
          <p>
            {th.flow.invalidBody}
            {state.source ? <span className="path mono"> ({state.source})</span> : null}
          </p>
          <ul>
            {state.issues.map((issue, index) => (
              <li key={index}>
                <span className="mono">{issue.code}</span>
                {issue.stage ? <span className="mono"> · {issue.stage}</span> : null}: {issue.error}
              </li>
            ))}
          </ul>
        </div>
      </section>
    );
  }

  const { data } = state;
  const reviewUrl = data.reviewUrl ?? null; // ลิงก์ /review มาจาก server (BMAD_STUDIO_REVIEW_URL)
  return (
    <section className="panel glass flow-status" aria-labelledby="flow-project-name">
      {header}
      <p className="muted flow-status__source mono">{th.flow.flowSource(data.flow.default, data.flow.overlay ?? null)}</p>
      <div className="callout" id="flow-next" aria-live="polite">
        <strong>{th.flow.nextStep}:</strong>{" "}
        {data.entry && !data.entry.ok ? (
          <span>
            {th.flow.entryBlocked} — {data.entry.reason}
          </span>
        ) : data.next ? (
          <span className="mono-mix">{nextText(data.next)}</span>
        ) : data.complete ? (
          <span>{th.flow.nextNone}</span>
        ) : (
          <span>{th.flow.nextBlocked}</span>
        )}
        {data.next?.gate === "human" && reviewUrl ? <ReviewLink href={reviewUrl} /> : null}
      </div>
      {data.locked
        .filter((item) => !item.stage)
        .map((item) => (
          <p key={item.field ?? item.reason} className="banner flow-status__lock">
            <Icon name="lock" size={18} />
            <span>
              {th.flow.locked}
              {item.field ? <span className="mono"> {item.field}</span> : null}: {item.reason}
            </span>
          </p>
        ))}
      {!data.hasSprint && track === "full" ? <p className="muted">{th.flow.noSprint}</p> : null}
      {data.hints?.map((hint) => (
        <p key={hint} className="banner">
          <Icon name="flow" size={18} /> <span>{th.flow.hint}: {hint}</span>
        </p>
      ))}
      {data.warnings?.map((warning, index) => (
        <p key={index} className="banner banner--warn">
          <Icon name="alert" size={18} /> <span>{th.flow.warning}: {warning.error}</span>
        </p>
      ))}
      <ol className="stage-list" aria-label={th.flow.stagesLabel}>
        {data.stages.map((stage) => (
          <StageItem key={stage.id} stage={stage} reviewUrl={reviewUrl} />
        ))}
      </ol>
    </section>
  );
}
