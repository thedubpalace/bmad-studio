import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SkillDetail as Detail, SkillName, SkillReferences } from "../api/types";
import { SkillCatalog, stageLink } from "./SkillCatalog";
import { SkillDetail } from "./SkillDetail";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const skills: SkillName[] = [
  { id: "bmad-build", description: "Turns work into code", group: "bmad", overrideTeam: true, overrideUser: false,
    customizable: true, flowCount: 2, usageKnown: true,
    usedIn: [{ flow: "default", kind: "default", track: "full", stage: "develop", enabled: true },
             { flow: "default", kind: "project", track: "full", stage: "develop", enabled: false }] },
  { id: "bmad-help", description: "Answers", group: "bmad", overrideTeam: false, overrideUser: false,
    customizable: true, flowCount: 0, usageKnown: true, usedIn: [] },
  { id: "veda", description: "orchestrator", group: "veda", overrideTeam: false, overrideUser: true,
    customizable: false, flowCount: 0, usageKnown: true, usedIn: [] },
];
const refs: SkillReferences = {
  missing: [{ id: "bmad-ghost", flowCount: 1, usedIn: [{ flow: "alpha", kind: "project", track: "full", stage: "architecture", enabled: true }] }],
  unreadable: [], flows: 2,
};
const detail: Detail = { ...skills[0], skillMd: "---\nname: bmad-build\n---\nbody", skillMdTruncated: false,
                         skillMdLimit: 262144, skillMdPath: ".claude/skills/bmad-build/SKILL.md", unreadable: [] };

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(status === 200 ? { ok: true, data } : data), { status, headers: { "Content-Type": "application/json" } });
}

describe("SkillCatalog / SkillDetail", () => {
  let container: HTMLDivElement;
  let root: Root;
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  async function render(url: string) {
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={[url]}>
          <Routes>
            <Route path="/bmad/skills" element={<SkillCatalog />} />
            <Route path="/bmad/skills/:id" element={<SkillDetail />} />
          </Routes>
        </MemoryRouter>,
      );
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }

  const button = (t: string) => Array.from(container.querySelectorAll("button")).find((b) => b.textContent?.trim().startsWith(t))!;

  it("lists skills grouped (not a card grid) with override/usage tags and a red missing badge", async () => {
    fetchMock.mockImplementation((url: string) => Promise.resolve(json(url.includes("references") ? refs : skills)));
    await render("/bmad/skills");
    const titles = Array.from(container.querySelectorAll(".skill-group__title")).map((h) => h.textContent);
    expect(titles).toEqual(["ถูกอ้างแต่ไม่พบ", "BMAD (2)", "skill อื่นของ Veda (1)"]);
    expect(container.querySelectorAll("ul.item-list > li")).toHaveLength(4);
    const build = Array.from(container.querySelectorAll(".skill-row")).find((r) => r.textContent?.includes("bmad-build"))!;
    expect(build.textContent).toContain("มี override (team)");
    expect(build.textContent).toContain("ถูกอ้างใน 2 flow");
    expect(container.textContent).toContain("ไม่ถูกใช้ใน flow");
    const missing = container.querySelector(".skill-row--missing")!;
    expect(missing.querySelector(".tag--danger")?.textContent).toContain("ไม่พบ");
    expect(missing.querySelector("a")?.getAttribute("href")).toBe("/p/alpha/flow?track=full&stage=architecture");
  });

  it("search and filters narrow the list", async () => {
    fetchMock.mockImplementation((url: string) => Promise.resolve(json(url.includes("references") ? refs : skills)));
    await render("/bmad/skills");
    act(() => button("ไม่ถูกใช้ใน flow").click());
    expect(Array.from(container.querySelectorAll(".skill-row .mono")).map((e) => e.textContent)).toEqual(["bmad-help", "veda"]);
    act(() => button("มี override").click());
    expect(Array.from(container.querySelectorAll(".skill-row .mono")).map((e) => e.textContent)).toEqual(["bmad-build", "veda"]);
    act(() => button("ไม่พบ").click());
    expect(container.querySelectorAll(".skill-row--missing")).toHaveLength(1);
    expect(container.querySelectorAll(".skill-row:not(.skill-row--missing)")).toHaveLength(0);
    act(() => button("ทั้งหมด").click());
    const input = container.querySelector<HTMLInputElement>("input[type=search]")!;
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    act(() => {
      setter.call(input, "orches");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(Array.from(container.querySelectorAll(".skill-row .mono")).map((e) => e.textContent)).toEqual(["veda"]);
  });

  it("detail shows used-in links and SKILL.md read-only, with no edit or delete controls", async () => {
    fetchMock.mockResolvedValueOnce(json(detail));
    await render("/bmad/skills/bmad-build");
    const links = Array.from(container.querySelectorAll(".skill-uses a")).map((a) => a.getAttribute("href"));
    // ชื่อโปรเจกต์ "default" ต้องไปที่ flow ของโปรเจกต์ ไม่ใช่ flow เริ่มต้น (review 6-2)
    expect(links).toEqual(["/flow/default?track=full&stage=develop", "/p/default/flow?track=full&stage=develop"]);
    expect(container.querySelector(".skill-uses")?.textContent).toContain("ขั้นนี้ปิดอยู่");
    expect(container.querySelector("pre")).toBeNull();
    act(() => button("ดู SKILL.md").click());
    expect(container.querySelector("pre")?.textContent).toContain("name: bmad-build");
    expect(container.querySelectorAll("textarea, input")).toHaveLength(0);
    const labels = Array.from(container.querySelectorAll("button")).map((b) => b.textContent ?? "");
    expect(labels.some((l) => /แก้|ลบ|บันทึก/.test(l))).toBe(false);
  });

  it("unknown usage is never shown as unused, and unreadable flows are announced", async () => {
    const unknown = skills.map((s) => ({ ...s, usageKnown: false, flowCount: 0, usedIn: [] }));
    const refs2 = { ...refs, known: false, unreadable: [{ flow: "default", kind: "default" as const }] };
    fetchMock.mockImplementation((url: string) => Promise.resolve(json(url.includes("references") ? refs2 : unknown)));
    await render("/bmad/skills");
    expect(container.textContent).toContain("อ่าน flow เริ่มต้นไม่ได้");
    expect(container.textContent).not.toContain("ถูกอ้างใน");
    act(() => button("ไม่ถูกใช้ใน flow").click());
    expect(container.querySelectorAll(".skill-row:not(.skill-row--missing)")).toHaveLength(0);
  });

  it("a SKILL.md that is a link is not shown", async () => {
    fetchMock.mockResolvedValueOnce(json({ ...detail, skillMd: "", skillMdError: "not_contained" }));
    await render("/bmad/skills/bmad-build");
    act(() => button("ดู SKILL.md").click());
    expect(container.querySelector("pre")).toBeNull();
    expect(container.textContent).toContain("junction");
  });

  it("unknown skill shows not found", async () => {
    fetchMock.mockResolvedValueOnce(json({ ok: false, code: "skill_not_found", error: "ไม่พบ skill นี้ใน .claude/skills" }, 404));
    await render("/bmad/skills/nope");
    expect(container.textContent).toContain("ไม่พบ skill นี้");
  });

  it("stage links encode names", () => {
    expect(stageLink({ flow: "a b", kind: "project", track: "full", stage: "x", enabled: true })).toBe("/p/a%20b/flow?track=full&stage=x");
  });
});
