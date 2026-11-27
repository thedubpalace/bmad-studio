import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SkillDetail as Detail, SkillOverride } from "../api/types";
import { clearPending, getPending } from "../changes/changeStore";
import { clearConfigDrafts } from "./configDraftStore";
import { SkillDetail } from "./SkillDetail";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const detail: Detail = {
  id: "bmad-build", description: "Build", group: "bmad", overrideTeam: false, overrideUser: false, customizable: true,
  flowCount: 1, usageKnown: true, usedIn: [], skillMd: "x", skillMdTruncated: false, skillMdLimit: 262144,
  skillMdPath: ".claude/skills/bmad-build/SKILL.md", unreadable: [],
};
const has = (team: boolean, user: boolean, effective: boolean) => ({ team, user, effective });
const view: SkillOverride = {
  skill: "bmad-build",
  targets: { team: "_bmad/custom/bmad-build.toml", user: "_bmad/custom/bmad-build.user.toml" },
  layers: { team: { exists: true, text: "[workflow]\nmax_rounds = 3\n", sha256: "a".repeat(64), issue: null },
            user: { exists: false, text: "", sha256: null, issue: null } },
  fields: [
    { key: "workflow.activation_steps_append", id: '["workflow","activation_steps_append"]', path: ["workflow", "activation_steps_append"],
      rule: "append", type: "list", default: [], instruction: true, team: null, user: null, effective: [], has: has(false, false, true) },
    { key: "workflow.max_rounds", id: '["workflow","max_rounds"]', path: ["workflow", "max_rounds"], rule: "replace",
      type: "number", default: 2, instruction: false, team: 3, user: null, effective: 3, has: has(true, false, true) },
    { key: "workflow.review_layers", id: '["workflow","review_layers"]', path: ["workflow", "review_layers"], rule: "keyed",
      keyedBy: "id", type: "complex", default: [{ id: "blind" }], instruction: false, team: null, user: null,
      effective: [{ id: "blind" }], has: has(false, false, true) },
  ],
  outside: { team: [], user: [] },
  effectiveAvailable: true,
  issues: [],
};
const basePreview = {
  preview_id: "p", path: "_bmad/custom/bmad-build.toml", kind: "override", op: "modify", impact: "high",
  confirm_phrase: "bmad-build.toml", base_sha256: "a".repeat(64), proposed_sha256: "b".repeat(64),
  diff: { hunks: [], added: 1, removed: 0, truncated: false, old_eof_newline: true, new_eof_newline: true },
  warnings: [], errors: [], confirm_token: "t", expires_at: "x", view: null,
};

function json(data: unknown) {
  return new Response(JSON.stringify({ ok: true, data }), { status: 200, headers: { "Content-Type": "application/json" } });
}

describe("OverrideEditor (tab override of /bmad/skills/<id>)", () => {
  let container: HTMLDivElement;
  let root: Root;
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
    clearConfigDrafts();
    clearPending();
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  async function flush() {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }

  async function render(previews: unknown[] = []) {
    const queue = [...previews];
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/override")) return Promise.resolve(json(view));
      if (url.includes("/api/changes/preview")) return Promise.resolve(json(queue.shift()));
      return Promise.resolve(json(detail));
    });
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={["/bmad/skills/bmad-build?tab=override"]}>
          <Routes>
            <Route path="/bmad/skills/:id" element={<SkillDetail />} />
            <Route path="/changes/review" element={<p id="review-page">review</p>} />
          </Routes>
        </MemoryRouter>,
      );
    });
    await flush();
    await flush();
  }

  const button = (t: string) => Array.from(container.querySelectorAll("button")).find((b) => b.textContent?.trim().startsWith(t))!;
  const previewBodies = () => fetchMock.mock.calls.filter(([u]) => String(u).includes("/api/changes/preview")).map(([, i]) => JSON.parse(i.body));
  function type(el: HTMLInputElement | HTMLTextAreaElement, value: string) {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    act(() => {
      Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(el, value);
      el.dispatchEvent(new Event("input", { bubbles: true }));
    });
  }

  it("builds the form only from the customize surface with merge-rule badges and a high-impact tag", async () => {
    await render();
    const fields = container.querySelectorAll(".override-field");
    expect(fields).toHaveLength(3);
    expect(fields[0].textContent).toContain("ต่อท้าย");
    expect(fields[0].textContent).toContain("คำสั่งที่ agent รัน — ผลสูง");
    expect(fields[1].textContent).toContain("แทนที่");
    expect(fields[2].textContent).toContain("แทนที่ตาม key");
    expect(fields[2].querySelector("input, textarea, select")).toBeNull();
    expect(container.querySelector(".override-effective")?.textContent).toContain("ผลหลัง merge");
    expect(container.querySelector("[role=tab][aria-selected=true]")?.textContent).toBe("Override");
  });

  it("form edit of an instruction list sends kind override with the array value", async () => {
    await render([basePreview]);
    type(container.querySelector<HTMLTextAreaElement>(".override-field textarea")!, "first step\n\nsecond step");
    type(container.querySelectorAll<HTMLInputElement>(".override-field input")[0], "5");
    await act(async () => button("ตรวจสอบและดูการเปลี่ยนแปลง").click());
    await flush();
    expect(previewBodies()[0]).toEqual({
      kind: "override", target: "_bmad/custom/bmad-build.toml", base_sha256: "a".repeat(64),
      payload: { set: [{ path: ["workflow", "activation_steps_append"], value: ["first step", "second step"] },
                       { path: ["workflow", "max_rounds"], value: 5 }], unset: [] },
    });
    expect(container.querySelector("#review-page")).not.toBeNull();
  });

  it("keys outside the surface need an explicit acknowledgement before the review opens", async () => {
    const unconfirmed = { ...basePreview,
      warnings: [{ code: "override_outside_surface", error: "นอก surface", keys: ["workflow.extra"] }],
      errors: [{ code: "override_outside_surface_unconfirmed", error: "ยืนยันก่อน" }] };
    await render([unconfirmed, { ...basePreview, warnings: unconfirmed.warnings }]);
    act(() => button("แก้เป็นข้อความ TOML").click());
    type(container.querySelector<HTMLTextAreaElement>("textarea")!, "[workflow]\nextra = 1\n");
    await act(async () => button("ตรวจสอบและดูการเปลี่ยนแปลง").click());
    await flush();
    expect(container.querySelector("#review-page")).toBeNull();
    expect(container.textContent).toContain("workflow.extra");
    expect(getPending()).toBeNull();
    await act(async () => button("ตรวจสอบและดูการเปลี่ยนแปลง").click());
    expect(previewBodies()).toHaveLength(1); // ยังไม่ติ๊กยืนยัน = ไม่ส่ง
    const box = container.querySelector<HTMLInputElement>(".issue-box input[type=checkbox]")!;
    act(() => box.click());
    await act(async () => button("ตรวจสอบและดูการเปลี่ยนแปลง").click());
    await flush();
    expect(previewBodies()[1].payload).toEqual({ text: "[workflow]\nextra = 1\n", acknowledged: ["override_outside_surface"] });
    expect(container.querySelector("#review-page")).not.toBeNull();
  });

  it("the detail tab hides the editor", async () => {
    await render();
    act(() => button("รายละเอียด").click());
    expect(container.querySelector(".override-editor")).toBeNull();
  });
});
