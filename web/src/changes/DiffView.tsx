// แสดง diff ที่ server คำนวณ (unified / side-by-side, word highlight, พับส่วนไม่เปลี่ยน, virtualize > 2,000 บรรทัด)
import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";

import type { Diff, DiffLine } from "../api/types";
import { th } from "../i18n/th";
import { lineCount, numberWidth, segments, splitRows, unifiedRows, VIRTUALIZE_OVER, visibleWindow } from "./diffModel";

export type DiffMode = "unified" | "split";

export const ROW_HEIGHT = 21; // mono 14px × line-height 1.5 — ตรงกับ --diff-row ใน app.css

function Code({ line }: { line: DiffLine }) {
  const parts = segments(line.text, line.spans);
  const mark = line.type === "add" || line.type === "del" ? line.type : null;
  return (
    <span className="diff__code">
      {parts.map((part, i) =>
        part.changed && mark ? (
          <mark key={i} className={`diff__word diff__word--${mark}`}>
            {part.text}
          </mark>
        ) : (
          <span key={i}>{part.text}</span>
        ),
      )}
      {line.text === "" ? " " : null}
    </span>
  );
}

function Sign({ type }: { type: DiffLine["type"] }) {
  if (type === "add" || type === "del") {
    return (
      <span className="diff__sign">
        <span aria-hidden="true">{type === "add" ? "+" : "−"}</span>
        <span className="sr-only">{type === "add" ? th.review.added : th.review.removed}</span>
      </span>
    );
  }
  return <span className="diff__sign" aria-hidden="true" />;
}

function Num({ value }: { value: number | null | undefined }) {
  return <span className="diff__num">{value ?? ""}</span>;
}

function Fold({ count }: { count: number }) {
  return <div className="diff__row diff__fold">{th.review.fold(count)}</div>;
}

function UnifiedLine({ line }: { line: DiffLine }) {
  return (
    <div className={`diff__row diff__row--${line.type}`}>
      <Num value={line.old} />
      <Num value={line.new} />
      <Sign type={line.type} />
      <Code line={line} />
    </div>
  );
}

function SplitSide({ line, side }: { line: DiffLine | null; side: "old" | "new" }) {
  if (!line) return <span className="diff__half diff__half--empty" />;
  return (
    <span className={`diff__half diff__half--${line.type}`}>
      <Num value={side === "old" ? line.old : line.new} />
      <Sign type={line.type} />
      <Code line={line} />
    </span>
  );
}

export interface DiffViewProps {
  diff: Diff;
  path: string;
  mode: DiffMode;
  wrap: boolean;
}

export function DiffView({ diff, path, mode, wrap }: DiffViewProps) {
  const rows = useMemo(() => (mode === "split" ? splitRows(diff) : unifiedRows(diff)), [diff, mode]);
  const virtual = lineCount(diff) > VIRTUALIZE_OVER;
  const scrollRef = useRef<HTMLDivElement>(null);
  const [view, setView] = useState({ top: 0, height: 600 });

  useEffect(() => {
    if (!virtual) return;
    const el = scrollRef.current;
    if (!el) return;
    const update = () => setView({ top: el.scrollTop, height: el.clientHeight || 600 });
    update();
    el.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(update) : null;
    observer?.observe(el);
    return () => {
      el.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
      observer?.disconnect();
    };
  }, [virtual]);

  if (diff.truncated) {
    return (
      <p className="diff__empty" role="note">
        {th.review.diffTruncated(diff.added, diff.removed)}
      </p>
    );
  }
  if (diff.hunks.length === 0) {
    return <p className="diff__empty">{th.review.noHunks}</p>;
  }

  const win = virtual ? visibleWindow(view.top, view.height, ROW_HEIGHT, rows.length) : { start: 0, end: rows.length };
  const rendered: ReactNode[] = rows.slice(win.start, win.end).map((row) => {
    if (row.kind === "fold") return <Fold key={row.key} count={row.count} />;
    if ("line" in row) return <UnifiedLine key={row.key} line={row.line} />;
    return (
      <div key={row.key} className="diff__row diff__row--split">
        <SplitSide line={row.left} side="old" />
        <SplitSide line={row.right} side="new" />
      </div>
    );
  });

  const className = ["diff", `diff--${mode}`, wrap && !virtual ? "diff--wrap" : "", virtual ? "diff--virtual" : ""]
    .filter(Boolean)
    .join(" ");
  const style = { "--num": `${numberWidth(diff)}ch` } as CSSProperties;
  return (
    <div className={className} style={style}>
      {mode === "split" ? (
        <div className="diff__sides" aria-hidden="true">
          <span>{th.review.oldSide}</span>
          <span>{th.review.newSide}</span>
        </div>
      ) : null}
      <div ref={scrollRef} className="diff__scroll" role="region" aria-label={th.review.diffLabel(path)} tabIndex={0}>
        {virtual ? (
          <div style={{ height: rows.length * ROW_HEIGHT, position: "relative" }}>
            <div style={{ position: "absolute", top: win.start * ROW_HEIGHT, left: 0, right: 0 }}>{rendered}</div>
          </div>
        ) : (
          rendered
        )}
      </div>
      {diff.old_eof_newline !== diff.new_eof_newline ? (
        <p className="diff__eof">{diff.new_eof_newline ? th.review.eofAdded : th.review.eofRemoved}</p>
      ) : null}
    </div>
  );
}
