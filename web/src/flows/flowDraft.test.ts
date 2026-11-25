import { describe, expect, it } from "vitest";

import { displayOrder, moveBlock, moveStage, nextEnabledAfter, sameDraft, type Draft, type Meta, type Stage } from "./flowDraft";

function s(id: string, after: string[], over: Partial<Stage> = {}): Stage {
  return { id, agent: "ba", skill: null, enabled: true, optional: false, after, gate: "none", produces: [], condition: null, ...over };
}

const chain = [s("a", []), s("b", ["a"]), s("gate", ["b"]), s("c", ["gate"]), s("d", ["c"]), s("e", ["d"])];
const meta = (id: string): Meta | undefined =>
  ({ locked: id === "gate", lockReason: id === "gate" ? "ล็อก" : null, mustFollowLocked: false, overridden: false, default: null });

describe("flowDraft", () => {
  it("orders by after, not by list position", () => {
    expect(displayOrder([...chain].reverse())).toEqual(["a", "b", "gate", "c", "d", "e"]);
  });

  it("keeps stages that are in a cycle instead of dropping them", () => {
    expect(displayOrder([s("x", ["y"]), s("y", ["x"]), s("z", [])]).sort()).toEqual(["x", "y", "z"]);
  });

  it("moves a stage up by rewriting after only around it", () => {
    const next = moveStage(chain, "d", -1) as Stage[];
    expect(next.map((x) => x.id)).toEqual(["a", "b", "gate", "d", "c", "e"]);
    const by = Object.fromEntries(next.map((x) => [x.id, x.after]));
    expect(by).toMatchObject({ d: ["gate"], c: ["d"], e: ["c"], a: [], b: ["a"], gate: ["b"] });
  });

  it("moves down = moving the next stage up; round trip restores the original", () => {
    const down = moveStage(chain, "c", 1) as Stage[];
    expect(down.map((x) => x.id)).toEqual(["a", "b", "gate", "d", "c", "e"]);
    const back = moveStage(down, "c", -1) as Stage[];
    expect(Object.fromEntries(back.map((x) => [x.id, x.after]))).toEqual(Object.fromEntries(chain.map((x) => [x.id, x.after])));
  });

  it("never swaps with a locked stage (reasons come from `locked` only)", () => {
    expect(moveBlock(chain, meta, "c", -1)).toBe("locked-prev");
    expect(moveBlock(chain, meta, "b", 1)).toBe("locked-next");
    expect(moveBlock(chain, meta, "gate", -1)).toBe("locked-self");
    expect(moveBlock(chain, meta, "a", -1)).toBe("first");
    expect(moveBlock(chain, meta, "e", 1)).toBe("last");
    expect(moveBlock(chain, meta, "d", -1)).toBeNull();
    const unlocked = () => undefined;
    expect(moveBlock(chain, unlocked, "c", -1)).toBeNull(); // ไม่มีชื่อขั้นที่ hard-code
  });

  it("parallel branches become ordered after a move without creating a cycle", () => {
    const fork = [s("a", []), s("x", ["a"]), s("y", ["a"]), s("z", ["x", "y"])];
    const next = moveStage(fork, "y", -1) as Stage[];
    expect(next.map((t) => t.id)).toEqual(["a", "y", "x", "z"]);
    expect(displayOrder(next)).toHaveLength(4);
  });

  it("finds the next enabled stage after a disabled one", () => {
    const off = chain.map((x) => (x.id === "c" || x.id === "d" ? { ...x, enabled: false } : x));
    expect(nextEnabledAfter(off, "c")).toBe("e");
  });

  it("compares drafts ignoring list order", () => {
    const a: Draft = { version: 1, tracks: { full: { entry_condition: null, stages: chain } } };
    const b: Draft = { version: 1, tracks: { full: { entry_condition: null, stages: [...chain].reverse() } } };
    expect(sameDraft(a, b)).toBe(true);
    expect(sameDraft(a, { ...b, tracks: { full: { entry_condition: null, stages: moveStage(chain, "d", -1) as Stage[] } } })).toBe(false);
  });
});
