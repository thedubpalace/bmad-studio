import { describe, expect, it } from "vitest";

import type { Diff } from "../api/types";
import { segments, splitRows, unifiedRows, visibleWindow } from "./diffModel";

const diff: Diff = {
  added: 2,
  removed: 1,
  hunks: [
    {
      old_start: 10,
      old_lines: 3,
      new_start: 10,
      new_lines: 4,
      lines: [
        { type: "context", old: 10, new: 10, text: "a", spans: [] },
        { type: "del", old: 11, new: null, text: "b old", spans: [[2, 5]] },
        { type: "add", old: null, new: 11, text: "b new", spans: [[2, 5]] },
        { type: "add", old: null, new: 12, text: "c", spans: [[0, 1]] },
        { type: "context", old: 12, new: 13, text: "d", spans: [] },
      ],
    },
    {
      old_start: 40,
      old_lines: 1,
      new_start: 41,
      new_lines: 1,
      lines: [{ type: "context", old: 40, new: 41, text: "z", spans: [] }],
    },
  ],
};

describe("segments", () => {
  it("splits on spans", () => {
    expect(segments("b new", [[2, 5]])).toEqual([
      { text: "b ", changed: false },
      { text: "new", changed: true },
    ]);
  });
  it("ignores malformed, overlapping and out-of-range spans", () => {
    expect(segments("abc", [[1, 99], [0, 2], [5, 1], [1]])).toEqual([
      { text: "ab", changed: true },
      { text: "c", changed: false },
    ]);
    expect(segments("", [])).toEqual([{ text: "", changed: false }]);
  });
});

describe("rows", () => {
  it("folds unchanged lines before and between hunks", () => {
    const rows = unifiedRows(diff);
    expect(rows[0]).toMatchObject({ kind: "fold", count: 9 });
    const folds = rows.filter((r) => r.kind === "fold").map((r) => (r.kind === "fold" ? r.count : 0));
    expect(folds).toEqual([9, 27]);
  });
  it("pairs del/add in split mode and pads the shorter side", () => {
    const rows = splitRows(diff).filter((r) => r.kind === "pair");
    expect(rows).toHaveLength(5);
    const pair = rows[1];
    const extra = rows[2];
    expect(pair.kind === "pair" && pair.left?.text).toBe("b old");
    expect(pair.kind === "pair" && pair.right?.text).toBe("b new");
    expect(extra.kind === "pair" && extra.left).toBeNull();
  });
  it("computes a bounded render window", () => {
    expect(visibleWindow(0, 600, 21, 5000)).toEqual({ start: 0, end: 59 });
    const mid = visibleWindow(21_000, 600, 21, 5000);
    expect(mid.start).toBe(970);
    expect(mid.end - mid.start).toBeLessThan(100);
  });
});

describe("hardening", () => {
  it("counts spans in code points like the server (emoji before the change)", () => {
    expect(segments("🙂 ab", [[2, 4]])).toEqual([
      { text: "🙂 ", changed: false },
      { text: "ab", changed: true },
    ]);
  });
  it("does not loop on an unknown line type", () => {
    const weird = {
      added: 0,
      removed: 0,
      hunks: [{ old_start: 1, old_lines: 1, new_start: 1, new_lines: 1, lines: [{ type: "weird", old: 1, new: 1, text: "x" }] }],
    } as unknown as Diff;
    expect(splitRows(weird)).toHaveLength(1);
  });
  it("widens the line-number column for big files", async () => {
    const { numberWidth } = await import("./diffModel");
    expect(numberWidth({ ...diff })).toBe(4);
    expect(numberWidth({ added: 1, removed: 0, hunks: [{ old_start: 12000, old_lines: 1, new_start: 12000, new_lines: 2, lines: [] }] })).toBe(6);
  });
});
