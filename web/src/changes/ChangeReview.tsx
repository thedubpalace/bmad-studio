// หน้า Review → Confirm ของทุกการเขียนไฟล์ (design.md "DiffPreview + ConfirmWrite", REQ-043, AD-5)
// สองขั้นอยู่คนละ state ใน URL (?step=review|confirm) — back ของเบราว์เซอร์ไม่เขียนซ้ำ
// UI ไม่ใช่กลไกความปลอดภัย: server ตรวจ token, typed confirmation และ errors เองทุกครั้ง
import { useEffect, useId, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";

import { ApiError } from "../api/client";
import type { ChangePreview } from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { Icon } from "../components/Icon";
import { th } from "../i18n/th";
import {
  clearPending,
  commitPending,
  hasUnsavedChange,
  refreshPreview,
  usePendingChange,
  type PendingChange,
} from "./changeStore";
import { DiffView, type DiffMode } from "./DiffView";
import { lineCount, VIRTUALIZE_OVER, WHITESPACE_CODES } from "./diffModel";

export const HOLD_MS = 800;
const WIDE = "(min-width: 960px)";
const REDUCED = "(prefers-reduced-motion: reduce)";

function useMedia(query: string): boolean {
  const get = () => (typeof window !== "undefined" && typeof window.matchMedia === "function" ? window.matchMedia(query).matches : false);
  const [matches, setMatches] = useState(get);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mql = window.matchMedia(query);
    const onChange = () => setMatches(mql.matches);
    onChange();
    mql.addEventListener?.("change", onChange);
    return () => mql.removeEventListener?.("change", onChange);
  }, [query]);
  return matches;
}

/** ชื่อที่ต้องพิมพ์ยืนยัน — มาจาก server เท่านั้น (ไม่เดาจาก path) */
function confirmPhrase(preview: ChangePreview): string | null {
  const phrase = preview.confirm_phrase?.trim();
  return phrase ? phrase : null;
}

function Summary({ preview }: { preview: ChangePreview }) {
  const high = preview.impact === "high";
  return (
    <div className="review-summary">
      <p className="review-summary__label">{th.review.fileLabel}</p>
      <p className="mono path">{preview.path}</p>
      <ul className="review-summary__meta">
        <li>{preview.op === "create" ? th.review.opCreate : preview.op === "delete" ? th.review.opDelete : th.review.opModify}</li>
        <li className="mono">{th.review.lineStats(preview.diff.added, preview.diff.removed)}</li>
        <li className={high ? "tag tag--warn" : "tag"}>
          <Icon name={high ? "alert" : "check"} size={16} />
          <span>{high ? th.review.impactHigh : th.review.impactNormal}</span>
        </li>
      </ul>
      {preview.path === "CLAUDE.md" ? (
        <p className="review-summary__note">
          <Icon name="lock" size={16} /> {th.review.claudeMdUntouched}
        </p>
      ) : null}
    </div>
  );
}

function Issues({ preview }: { preview: ChangePreview }) {
  const errors = preview.errors ?? [];
  const warnings = preview.warnings ?? [];
  const whitespace = errors.some((e) => WHITESPACE_CODES.has(e.code));
  return (
    <>
      {errors.length ? (
        <section className="issue-box issue-box--error" aria-labelledby="review-errors">
          <h3 id="review-errors" className="issue-box__title">
            <Icon name="cross" size={18} /> {th.review.errorsTitle}
          </h3>
          {whitespace ? <p className="issue-box__lead">{th.review.whitespaceOnly}</p> : null}
          <ul>
            {errors.map((e, i) => (
              <li key={`${e.code}-${i}`}>{e.error}</li>
            ))}
          </ul>
        </section>
      ) : null}
      {warnings.length ? (
        <section className="issue-box issue-box--warn" aria-labelledby="review-warnings">
          <h3 id="review-warnings" className="issue-box__title">
            <Icon name="alert" size={18} /> {th.review.warningsTitle}
          </h3>
          <ul>
            {warnings.map((w, i) => (
              <li key={`${w.code}-${i}`}>{w.error}</li>
            ))}
          </ul>
        </section>
      ) : null}
    </>
  );
}

function ReviewStep({ pending, onNext }: { pending: PendingChange; onNext: () => void }) {
  const navigate = useNavigate();
  const wide = useMedia(WIDE);
  const [mode, setMode] = useState<DiffMode>(wide ? "split" : "unified");
  const [wrap, setWrap] = useState(false);
  const blockedId = useId();
  const { preview } = pending;
  const blocked = (preview.errors ?? []).length > 0;
  const long = lineCount(preview.diff) > VIRTUALIZE_OVER;
  const effectiveMode: DiffMode = wide ? mode : "unified";

  return (
    <>
      <Summary preview={preview} />
      <Issues preview={preview} />
      <div className="diff-toolbar" role="group" aria-label={th.review.viewLabel}>
        <div className="segmented">
          <button type="button" className="segmented__item" aria-pressed={effectiveMode === "unified"} onClick={() => setMode("unified")}>
            {th.review.viewUnified}
          </button>
          <button
            type="button"
            className="segmented__item"
            aria-pressed={effectiveMode === "split"}
            aria-disabled={!wide}
            onClick={() => wide && setMode("split")}
          >
            {th.review.viewSplit}
          </button>
        </div>
        <label className="check">
          <input type="checkbox" checked={wrap && !long} disabled={long} onChange={(e) => setWrap(e.target.checked)} />
          <span>{th.review.wrap}</span>
        </label>
        {!wide ? <p className="hint">{th.review.splitNeedsWidth}</p> : null}
        {long ? <p className="hint">{th.review.wrapDisabledLong}</p> : null}
      </div>
      <DiffView diff={preview.diff} path={preview.path} mode={effectiveMode} wrap={wrap} />
      <div className="review-actions">
        <button type="button" className="button button--secondary" onClick={() => navigate(pending.returnTo)}>
          {th.review.back}
        </button>
        <div className="review-actions__primary">
          <button
            type="button"
            className="button button--primary"
            aria-disabled={blocked}
            aria-describedby={blocked ? blockedId : undefined}
            onClick={() => !blocked && onNext()}
          >
            {th.review.next}
          </button>
          {blocked ? (
            <p id={blockedId} className="form-error">
              {th.review.nextBlocked}
            </p>
          ) : null}
        </div>
      </div>
    </>
  );
}

function HoldButton({ enabled, busy, onConfirm, describedBy, label }: { enabled: boolean; busy: boolean; onConfirm: () => void; describedBy?: string; label: string }) {
  const [progress, setProgress] = useState(0);
  const timer = useRef<number | null>(null);
  const start = useRef(0);
  const confirmRef = useRef(onConfirm);
  confirmRef.current = onConfirm;

  const cancel = () => {
    if (timer.current !== null) cancelAnimationFrame(timer.current);
    timer.current = null;
    setProgress(0);
  };
  const tick = () => {
    const p = Math.min(1, (performance.now() - start.current) / HOLD_MS);
    setProgress(p);
    if (p >= 1) {
      timer.current = null;
      setProgress(0);
      confirmRef.current();
      return;
    }
    timer.current = requestAnimationFrame(tick);
  };
  useEffect(() => cancel, []);

  const down = (event: ReactPointerEvent<HTMLButtonElement>) => {
    // ปุ่มหลักเท่านั้น (คลิกขวา/ปุ่มอื่นไม่เริ่มนับ); พิมพ์ตรงแล้ว = คลิกธรรมดา
    if (event.button !== 0 || busy || enabled || timer.current !== null) return;
    try {
      event.currentTarget.setPointerCapture?.(event.pointerId);
    } catch {
      /* jsdom / pointer ที่ไม่รองรับ capture */
    }
    start.current = performance.now();
    timer.current = requestAnimationFrame(tick);
  };

  return (
    <button
      type="button"
      className="button button--primary hold-button"
      aria-disabled={busy}
      aria-describedby={describedBy}
      style={{ ["--hold" as string]: progress }}
      onPointerDown={down}
      onPointerUp={cancel}
      onPointerCancel={cancel}
      onLostPointerCapture={cancel}
      onContextMenu={(e) => e.preventDefault()}
      onClick={() => {
        if (!busy && enabled) onConfirm();
      }}
    >
      <span className="hold-button__ring" aria-hidden="true" />
      {busy ? th.review.writing : label}
    </button>
  );
}

function ConfirmStep({ pending, onBack }: { pending: PendingChange; onBack: () => void }) {
  const { preview } = pending;
  const reduced = useMedia(REDUCED);
  const high = preview.impact === "high";
  const phrase = confirmPhrase(preview);
  const [typed, setTyped] = useState("");
  const inputId = useId();
  const reasonId = useId();
  const busy = pending.outcome.kind === "writing";
  const missingPhrase = high && phrase === null;
  const typedOk = phrase !== null && typed.trim() === phrase;
  // ข้อความปุ่มตรงกับการกระทำ (design.md): เขียนไฟล์ / คืนค่าไฟล์
  const label = pending.request.kind === "revert" ? th.review.writeRevert : th.review.write;
  // กดค้างเป็นทางเลือกที่ design.md อนุญาตแทนการพิมพ์ — ส่งชื่อที่ server กำหนดให้ server ตรวจ
  const submit = () => {
    if (missingPhrase) return;
    void commitPending(high ? phrase : null);
  };

  return (
    <>
      <ul className="confirm-summary">
        <li className="mono path">{preview.path}</li>
        <li className="mono">{th.review.lineStats(preview.diff.added, preview.diff.removed)}</li>
        <li>{preview.op === "delete" ? th.review.willMoveToBackup : th.review.willBackup}</li>
        {(preview.warnings ?? []).map((w, i) => (
          <li key={`${w.code}-${i}`} className="confirm-summary__warn">
            <Icon name="alert" size={16} /> {w.error}
          </li>
        ))}
      </ul>
      {missingPhrase ? (
        <p className="form-error" role="alert">
          {th.review.missingPhrase}
        </p>
      ) : null}
      {high && phrase !== null ? (
        <div className="field confirm-field">
          <label className="field__label" htmlFor={inputId}>
            {th.review.typeToConfirm(phrase)}
          </label>
          <input
            id={inputId}
            className="input mono"
            autoComplete="off"
            spellCheck={false}
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && typedOk && !busy) submit();
            }}
          />
          {reduced ? null : <p className="hint">{th.review.holdHint}</p>}
        </div>
      ) : null}
      <div className="review-actions">
        <button type="button" className="button button--secondary" onClick={onBack} disabled={busy}>
          {th.review.backToReview}
        </button>
        <div className="review-actions__primary">
          {high && !reduced && !missingPhrase ? (
            <HoldButton enabled={typedOk} busy={busy} onConfirm={submit} describedBy={typedOk ? undefined : reasonId} label={label} />
          ) : (
            <button
              type="button"
              className="button button--primary"
              aria-disabled={busy || missingPhrase || (high && !typedOk)}
              aria-describedby={high && !typedOk ? reasonId : undefined}
              onClick={() => {
                if (!busy && (!high || typedOk)) submit();
              }}
            >
              {busy ? th.review.writing : label}
            </button>
          )}
          {high && !typedOk && !missingPhrase ? (
            <p id={reasonId} className="hint">
              {reduced ? th.review.writeBlockedReduced : th.review.writeBlocked}
            </p>
          ) : null}
        </div>
      </div>
    </>
  );
}

/** ชื่อโปรเจกต์จาก details ของ ux_ack_stale (ข้อมูลจาก server — แสดงเป็นข้อความเท่านั้น) */
function staleProjects(details: unknown): string[] {
  if (!details || typeof details !== "object") return [];
  const value = (details as { projects?: unknown }).projects;
  return Array.isArray(value) ? value.filter((p): p is string => typeof p === "string" && p !== "*unchecked*").slice(0, 50) : [];
}

function RefreshButton() {
  const [, setParams] = useSearchParams();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="banner__actions">
      <button
        type="button"
        className="button button--primary"
        aria-disabled={busy}
        onClick={() => {
          if (busy) return;
          setBusy(true);
          setError(null);
          refreshPreview().then(
            () => {
              setBusy(false);
              setParams({ step: "review" }, { replace: true });
            },
            (cause: unknown) => {
              setBusy(false);
              setError(cause instanceof ApiError ? cause.message : th.errors.unexpected);
            },
          );
        }}
      >
        {busy ? th.review.refreshing : th.review.refresh}
      </button>
      {error ? <p className="form-error">{error}</p> : null}
    </div>
  );
}

function Outcome({ pending }: { pending: PendingChange }) {
  const navigate = useNavigate();
  const [copy, setCopy] = useState<"" | "ok" | "fail">("");
  const outcome = pending.outcome;

  if (outcome.kind === "done") {
    return (
      <div className="banner banner--ok" role="status">
        <Icon name="check" />
        <p>{outcome.result.replayed ? th.review.savedReplayed : th.review.savedBanner(outcome.result.backup)}</p>
        <Link className="button button--secondary" to="/changes" onClick={() => clearPending()}>
          {th.review.toChanges}
        </Link>
      </div>
    );
  }
  if (outcome.kind === "conflict") {
    return (
      <div className="banner banner--danger" role="alert">
        <Icon name="alert" />
        <div>
          <p className="banner__title">{th.review.conflictTitle}</p>
          <p>{th.review.conflictBody}</p>
        </div>
        <div className="banner__actions">
          <button
            type="button"
            className="button button--primary"
            onClick={() => {
              const to = pending.returnTo;
              clearPending();
              navigate(to, { replace: true });
            }}
          >
            {th.review.reload}
          </button>
          {pending.draft !== undefined ? (
            <button
              type="button"
              className="button button--secondary"
              onClick={() => {
                const clip = typeof navigator !== "undefined" ? navigator.clipboard : undefined;
                if (!clip) {
                  setCopy("fail");
                  return;
                }
                clip.writeText(pending.draft ?? "").then(
                  () => setCopy("ok"),
                  () => setCopy("fail"),
                );
              }}
            >
              {copy === "ok" ? th.review.copied : copy === "fail" ? th.review.copyFailed : th.review.copyDraft}
            </button>
          ) : null}
        </div>
      </div>
    );
  }
  if (outcome.kind === "expired") {
    return (
      <div className="banner banner--warn" role="alert">
        <Icon name="alert" />
        <div>
          <p className="banner__title">{th.review.expiredTitle}</p>
          <p>{th.review.expiredBody}</p>
        </div>
        <RefreshButton />
      </div>
    );
  }
  if (outcome.kind === "reask") {
    const projects = staleProjects(outcome.error.details);
    return (
      <div className="banner banner--warn" role="alert">
        <Icon name="alert" />
        <div>
          <p className="banner__title">{th.review.reaskTitle}</p>
          <p>{th.review.reaskBody}</p>
          {projects.length > 0 ? <p>{th.review.reaskProjects(projects)}</p> : null}
        </div>
        <RefreshButton />
      </div>
    );
  }
  if (outcome.kind === "unknown") {
    return (
      <div className="banner banner--danger" role="alert">
        <Icon name="alert" />
        <div>
          <p className="banner__title">{th.review.unknownTitle}</p>
          <p>{th.review.unknownBody}</p>
        </div>
        <Link className="button button--secondary" to="/changes">
          {th.review.toChanges}
        </Link>
      </div>
    );
  }
  if (outcome.kind === "failed") {
    return (
      <div className="banner banner--danger" role="alert">
        <Icon name="alert" />
        <div>
          <p className="banner__title">{th.review.failedTitle}</p>
          <p>{outcome.error.message}</p>
          <p>{th.review.nothingChanged}</p>
        </div>
      </div>
    );
  }
  return null;
}

export function ChangeReview() {
  const pending = usePendingChange();
  const [params, setParams] = useSearchParams();
  const heading = useRef<HTMLHeadingElement>(null);
  const requested = params.get("step") === "confirm" ? "confirm" : "review";
  const blocked = (pending?.preview.errors ?? []).length > 0;
  const step = requested === "confirm" && !blocked ? "confirm" : "review";
  const outcome = pending?.outcome.kind ?? "idle";
  const settled =
    outcome === "done" || outcome === "conflict" || outcome === "expired" || outcome === "reask" || outcome === "unknown";
  // ไม่ย้าย focus ตอนเริ่มเขียน (ผู้ใช้ยังอยู่ที่ปุ่ม) — ย้ายเมื่อเปลี่ยนขั้นหรือได้ผล
  const focusKey = outcome === "writing" ? "idle" : outcome;

  useEffect(() => {
    heading.current?.focus();
  }, [step, focusKey, pending?.preview.preview_id]);

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (!hasUnsavedChange()) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, []);

  if (!pending) {
    return (
      <section className="panel glass">
        <EmptyState title={th.review.noPendingTitle} body={th.review.noPendingBody} />
      </section>
    );
  }

  const title = outcome === "done" ? th.review.doneTitle : step === "confirm" ? th.review.confirmTitle : th.review.reviewTitle;
  return (
    <section className={`review surface-solid review--${step}`} key={`${step}-${pending.preview.preview_id}`}>
      <p className="review__step">{th.review.stepLabel(step === "confirm" ? 2 : 1)}</p>
      <h2 className="review__title" tabIndex={-1} ref={heading}>
        {title}
      </h2>
      <Outcome pending={pending} />
      {settled ? null : step === "confirm" ? (
        <ConfirmStep pending={pending} onBack={() => setParams({ step: "review" })} />
      ) : (
        <ReviewStep pending={pending} onNext={() => setParams({ step: "confirm" })} />
      )}
    </section>
  );
}
