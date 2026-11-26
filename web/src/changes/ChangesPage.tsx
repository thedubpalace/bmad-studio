// /changes — ประวัติการเขียนไฟล์ของแอป + ดู diff + คืนค่า (design.md "ChangeLog & RevertPanel", REQ-043)
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { api, ApiError } from "../api/client";
import type { ChangeDiff, ChangeEntry } from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { Icon } from "../components/Icon";
import { th } from "../i18n/th";
import { startChange } from "./changeStore";
import { ClaudeMdProposals } from "./ClaudeMdProposals";
import { DiffView, type DiffMode } from "./DiffView";

export function typeLabel(entry: Pick<ChangeEntry, "kind" | "op">): string {
  if (entry.kind === "revert") return th.changes.typeRevert;
  if (entry.op === "create") return th.changes.typeCreate;
  if (entry.op === "delete") return th.changes.typeDelete;
  return th.changes.typeModify;
}

export function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat("th-TH", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function RevertButton({ entry }: { entry: ChangeEntry }) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="change-item__revert">
      <button
        type="button"
        className="button button--secondary"
        aria-disabled={busy}
        onClick={() => {
          if (busy) return;
          setBusy(true);
          setError(null);
          startChange(
            { kind: "revert", target: entry.path, payload: { change_id: entry.change_id }, base_sha256: entry.current_sha256 ?? null },
            "/changes",
          ).then(
            () => navigate("/changes/review?step=review"),
            (cause: unknown) => {
              setBusy(false);
              setError(cause instanceof ApiError ? cause.message : th.errors.unexpected);
            },
          );
        }}
      >
        {busy ? th.changes.reverting : th.changes.revert}
      </button>
      {error ? (
        <p className="form-error" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function ChangeHistory() {
  const [items, setItems] = useState<ChangeEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    api.get<ChangeEntry[]>("/api/changes").then(setItems, (cause: unknown) => {
      setError(cause instanceof ApiError ? cause.message : th.errors.unexpected);
    });
  }, []);
  useEffect(load, [load]);

  if (error) {
    return (
      <section className="panel glass">
        <div className="error-panel" role="alert">
          <p className="error-panel__title">
            <Icon name="alert" /> {th.changes.loadFailed}
          </p>
          <p>{error}</p>
          <button type="button" className="button button--secondary" onClick={load}>
            {th.errors.retry}
          </button>
        </div>
      </section>
    );
  }
  if (items === null) {
    return (
      <section className="panel glass" aria-busy="true">
        <p className="muted">{th.changes.loading}</p>
      </section>
    );
  }
  if (items.length === 0) {
    return (
      <section className="panel glass">
        <EmptyState title={th.empty.changes.title} body={th.empty.changes.body} />
      </section>
    );
  }
  return (
    <section className="panel glass">
      <h2 className="section-title">{th.changes.historyTitle}</h2>
      <ul className="item-list change-list">
        {items.map((entry) => (
          <li key={entry.change_id} className="item change-item">
            <div className="change-item__main">
              <p className="mono path change-item__path">{entry.path}</p>
              <p className="change-item__meta">
                <time dateTime={entry.at}>{formatTime(entry.at)}</time>
                <span className="tag">{typeLabel(entry)}</span>
                <span className="mono">{th.review.lineStats(entry.added, entry.removed)}</span>
                {entry.impact === "high" ? <span className="tag tag--warn">{th.changes.high}</span> : null}
              </p>
              {entry.reverts ? (
                <p className="hint">
                  <Link to={`/changes/${entry.reverts}`}>{th.changes.revertOf}</Link>
                </p>
              ) : null}
              {entry.changed_since ? <p className="hint">{th.changes.changedSince}</p> : null}
            </div>
            <div className="change-item__actions">
              <Link className="button button--secondary" to={`/changes/${entry.change_id}`}>
                {th.changes.viewDiff}
              </Link>
              <RevertButton entry={entry} />
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function ChangesPage() {
  return (
    <>
      <ClaudeMdProposals />
      <ChangeHistory />
    </>
  );
}

/** /changes/:id — DiffPreview โหมดอ่านอย่างเดียว */
export function ChangeDetail() {
  const { changeId = "" } = useParams();
  const [data, setData] = useState<ChangeDiff | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [mode, setMode] = useState<DiffMode>("unified");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    setData(null);
    setError(null);
    api.get<ChangeDiff>(`/api/changes/${encodeURIComponent(changeId)}`).then(setData, (cause: unknown) => {
      setError(cause instanceof ApiError ? cause : new ApiError(0, "network_error", th.errors.unexpected));
    });
  }, [changeId, attempt]);

  if (error) {
    const notFound = error.status === 404;
    return (
      <section className="panel glass">
        <EmptyState
          title={notFound ? th.changes.notFound : th.changes.loadDiffFailed}
          body={error.message}
          action={
            <div className="banner__actions">
              {notFound ? null : (
                <button type="button" className="button button--secondary" onClick={() => setAttempt((n) => n + 1)}>
                  {th.errors.retry}
                </button>
              )}
              <Link className="button button--secondary" to="/changes">
                {th.changes.back}
              </Link>
            </div>
          }
        />
      </section>
    );
  }
  if (!data) {
    return (
      <section className="panel glass" aria-busy="true">
        <p className="muted">{th.changes.loading}</p>
      </section>
    );
  }
  return (
    <section className="review surface-solid">
      <p className="review__step">{th.changes.readOnly}</p>
      <h2 className="review__title">{th.changes.detailTitle}</h2>
      <div className="review-summary">
        <p className="mono path">{data.path}</p>
        <ul className="review-summary__meta">
          <li>
            <time dateTime={data.at}>{formatTime(data.at)}</time>
          </li>
          <li className="tag">{typeLabel(data)}</li>
          <li className="mono">{th.review.lineStats(data.diff.added, data.diff.removed)}</li>
        </ul>
      </div>
      <div className="diff-toolbar" role="group" aria-label={th.review.viewLabel}>
        <div className="segmented">
          <button type="button" className="segmented__item" aria-pressed={mode === "unified"} onClick={() => setMode("unified")}>
            {th.review.viewUnified}
          </button>
          <button type="button" className="segmented__item" aria-pressed={mode === "split"} onClick={() => setMode("split")}>
            {th.review.viewSplit}
          </button>
        </div>
      </div>
      <DiffView diff={data.diff} path={data.path} mode={mode} wrap={false} />
      <div className="review-actions">
        <Link className="button button--secondary" to="/changes">
          {th.changes.back}
        </Link>
      </div>
    </section>
  );
}
