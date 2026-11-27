import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { EditorStage, FlowEditor as EditorView } from "../api/types";
import { getPending, clearPending } from "../changes/changeStore";
import { dropDraft } from "./draftStore";
import { ProjectFlowEditorPage } from "./ProjectFlowEditorPage";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("../auth/SessionContext", () => ({
  useSession: () => ({ session: { authenticated: true, username: "admin", role: "admin", csrf: "c", review_url: "https://example.test/review" } }),
}));

const GATE_REASON = "ขั้นอนุมัติ requirement โดยคนถูกล็อก — อนุมัติที่ /review ของ app-dashboard เท่านั้น";

function stage(id: string, after: string[], over: Partial<EditorStage> = {}): EditorStage {
  const base = { id, agent: "ba", skill: null, enabled: true, optional: false, after, gate: "none" as const, produces: [], condition: null };
  const merged = { ...base, ...over };
  const { id: _id, ...def } = { ...base, ...(over.default ?? {}) };
  return { locked: false, lockReason: null, overridden: false, overriddenFields: [], mustFollowLocked: false,
           ...merged, default: { ...def, ...(over.default ?? {}) } as EditorStage["default"] };
}

function makeView(over: Partial<EditorView> = {}, lockedId = "requirements-review"): EditorView {
  const full = [
    stage("bootstrap", [], { agent: "project-bootstrapper" }),
    stage("requirements", ["bootstrap"]),
    stage("requirements-review", ["requirements"], { agent: "human", gate: "human", produces: ["requirementsReady"] }),
    stage("ux-design", ["requirements-review"], { agent: "ux-designer", produces: ["designReady"], condition: "hasFrontend", mustFollowLocked: true }),
    stage("architecture", ["ux-design"], { agent: "architect", skill: "bmad-architecture", produces: ["planReady"], mustFollowLocked: true }),
    stage("develop", ["architecture"], { agent: "developer", skill: "bmad-build", mustFollowLocked: true }),
    stage("devops", ["develop"], { agent: "devops", optional: true, enabled: false, overridden: true, overriddenFields: ["enabled"],
                                   mustFollowLocked: true, default: { agent: "devops", optional: true, enabled: true, after: ["develop"], gate: "none", produces: [], skill: null, condition: null } }),
  ].map((s) => (s.id === lockedId ? { ...s, locked: true, lockReason: GATE_REASON } : s));
  const quick = [stage("develop", [], { agent: "developer", skill: "bmad-build" }), stage("review", ["develop"], { agent: "reviewer" })];
  const strip = (s: EditorStage) => ({ id: s.id, agent: s.agent, skill: s.skill, enabled: s.id === "devops" ? true : s.enabled,
                                        optional: s.optional, after: s.after, gate: s.gate, produces: s.produces, condition: s.condition });
  return {
    project: "demo", target: ".veda/projects/demo/flow.yaml", hasFrontend: true, base_sha256: "a".repeat(64), editable: true,
    notEditableReason: null, exists: true, flow: { default: "packaged", overlay: ".veda/projects/demo/flow.yaml" }, version: 1,
    tracks: {
      full: { entry_condition: null, stages: full, lockedFields: [] },
      quick: { entry_condition: { requirementsReady: true, status: ["developing"] }, stages: quick,
               lockedFields: [{ field: "entry_condition", reason: "เงื่อนไขเข้า quick track ถูกล็อก" }] },
    },
    defaultFlow: { version: 1, tracks: { full: { entry_condition: null, stages: full.map(strip) },
                                         quick: { entry_condition: { requirementsReady: true, status: ["developing"] }, stages: quick.map(strip) } } },
    issues: [], loadError: false, policyFix: null, overrides: 1, warnings: [],
    options: { gates: ["none", "human", "agent-confirm"], conditions: ["requirementsReady", "designReady", "planReady", "hasFrontend"],
               reservedAgents: ["human"] },
    ...over,
  };
}

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

type Draft = { tracks: Record<string, { stages: Array<{ id: string; enabled: boolean }> }> };

function Where() {
  const location = useLocation();
  return <p data-testid="where">{location.pathname + location.search}</p>;
}

describe("FlowEditor (story 4-2)", () => {
  let container: HTMLDivElement;
  let root: Root;
  const fetchMock = vi.fn();
  let view: EditorView;
  let validateBodies: Draft[];
  let previewBodies: Array<{ kind: string; target: string; base_sha256: string | null; payload: { flow: Draft; acknowledged: string[] } }>;
  let failValidate: boolean;

  beforeEach(() => {
    view = makeView();
    validateBodies = [];
    previewBodies = [];
    failValidate = false;
    dropDraft("project:demo");
    clearPending();
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: q.includes("960") ? wide : false, addEventListener() {}, removeEventListener() {} }));
    fetchMock.mockReset();
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === "/api/flows/demo") return json(200, { ok: true, data: view });
      if (url === "/api/agents/names") {
        return json(200, { ok: true, data: ["ba", "project-bootstrapper", "ux-designer", "architect", "developer", "devops", "reviewer"].map((name) => ({ name, description: "" })) });
      }
      if (url === "/api/skills?usage=0") return json(200, { ok: true, data: [{ id: "bmad-build", description: "" }, { id: "bmad-architecture", description: "" }] });
      if (url === "/api/flows/demo/validate") {
        if (failValidate) return json(503, { ok: false, code: "x", error: "x" });
        const draft = JSON.parse(String(init?.body)).flow as Draft;
        validateBodies.push(draft);
        const ux = draft.tracks.full.stages.find((s) => s.id === "ux-design");
        const gate = draft.tracks.full.stages.find((s) => s.id === "requirements-review");
        const uxOff = ux && !ux.enabled && view.hasFrontend !== false;
        return json(200, { ok: true, data: {
          errors: gate && !gate.enabled ? [{ code: "locked_gate_violation", error: "ข้าม gate", track: "full", stage: "requirements-review" }] : [],
          warnings: uxOff ? [{ code: "ux_disabled_frontend", error: "ปิด ux", track: "full" }] : [],
          ackRequired: uxOff ? ["ux_disabled_frontend"] : [], overrides: 2, resetsToDefault: false, overlay: {}, tracks: view.tracks,
        } });
      }
      if (url === "/api/changes/preview") {
        previewBodies.push(JSON.parse(String(init?.body)));
        return json(200, { ok: true, data: { preview_id: "p", path: view.target, kind: "flow", op: "modify", impact: "high",
          confirm_phrase: "flow.yaml", base_sha256: view.base_sha256, proposed_sha256: "b".repeat(64),
          diff: { hunks: [], added: 1, removed: 0 }, warnings: [], errors: [], confirm_token: "t", expires_at: "x" } });
      }
      return json(404, { ok: false, code: "not_found", error: "x" });
    });
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  let wide = true;

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
    wide = true;
  });

  async function render(url = "/p/demo/flow") {
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={[url]}>
          <Routes>
            <Route path="/p/:name/flow" element={<ProjectFlowEditorPage />} />
            <Route path="/changes/review" element={<Where />} />
          </Routes>
        </MemoryRouter>,
      );
    });
    await settle();
  }

  async function settle(ms = 260) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, ms));
    });
  }

  const row = (id: string) => container.querySelector<HTMLLIElement>(`[data-stage="${id}"]`) as HTMLLIElement;
  const byText = (text: string) =>
    Array.from(container.querySelectorAll<HTMLElement>("button, a")).find((b) => b.textContent?.includes(text)) as HTMLElement;
  const click = async (el: Element) => {
    await act(async () => {
      (el as HTMLElement).click();
    });
  };

  it("shows tracks, source label and a locked gate rendered from the API flag", async () => {
    await render();
    expect(byText("full")).toBeTruthy();
    expect(byText("quick")).toBeTruthy();
    expect(container.textContent).toContain("สืบทอดจาก default · ปรับเอง");
    const gate = row("requirements-review");
    expect(gate.className).toContain("flow-row--locked");
    expect(gate.querySelector("input[type=checkbox]")).toBeNull(); // ไม่มีสวิตช์
    expect(gate.querySelector(".flow-row__handle")).toBeNull(); // ไม่มีที่จับ
    expect(gate.querySelectorAll("svg").length).toBeGreaterThan(0);
    expect(gate.textContent).toContain("ล็อก");
    expect(gate.textContent).toContain(GATE_REASON);
    // คอนโทรลที่ถูกตัดแทนด้วยกุญแจ: aria-disabled + aria-describedby ชี้เหตุผล (ยัง focus ได้)
    const control = gate.querySelector("[role=switch]") as HTMLElement;
    expect(control.getAttribute("aria-disabled")).toBe("true");
    expect(control.tabIndex).toBe(0);
    expect(document.getElementById(String(control.getAttribute("aria-describedby")))?.textContent).toContain(GATE_REASON);
    const body = gate.querySelector(".flow-row__body") as HTMLElement;
    expect(document.getElementById(String(body.getAttribute("aria-describedby")).split(" ")[0])?.textContent).toContain(GATE_REASON);
    expect(gate.querySelector("a[href='https://example.test/review']")).toBeTruthy();
  });

  it("deep link from the skill catalog selects the track and opens the stage (story 6-2)", async () => {
    await render("/p/demo/flow?track=quick&stage=develop");
    expect(byText("quick").getAttribute("aria-pressed")).toBe("true");
    expect(container.querySelector(".inspector")?.textContent).toContain("develop");
  });

  it("skill picker lists /api/skills?usage=0 and binds the chosen skill (review 6-2 N1)", async () => {
    await render("/p/demo/flow?track=full&stage=develop");
    const select = Array.from(container.querySelectorAll<HTMLSelectElement>(".inspector select"))
      .find((s) => Array.from(s.options).some((o) => o.value === "bmad-build"))!;
    expect(select).toBeTruthy();
    expect(Array.from(select.options).map((o) => o.value)).toEqual(["", "bmad-build", "bmad-architecture"]);
    expect(select.value).toBe("bmad-build");
    await act(async () => {
      select.value = "bmad-architecture";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(row("develop").textContent).toContain("bmad-architecture");
  });

  it("a stage bound to a skill without a folder shows it as not found", async () => {
    view = makeView();
    view.tracks.full.stages = view.tracks.full.stages.map((s) => (s.id === "develop" ? { ...s, skill: "bmad-ghost" } : s));
    await render("/p/demo/flow?track=full&stage=develop");
    const inspector = container.querySelector(".inspector")!;
    expect(inspector.textContent).toContain("ไม่พบ skill นี้ใน .claude/skills");
    expect(Array.from(inspector.querySelectorAll("option")).map((o) => o.textContent)).toContain("bmad-ghost (ไม่พบ)");
  });

  it("a crafted ?track= (prototype name) falls back instead of crashing", async () => {
    await render("/p/demo/flow?track=constructor&stage=develop");
    expect(byText("full")).toBeTruthy();
    expect(row("requirements-review")).toBeTruthy();
  });

  it("locks whatever stage the API marks locked — no hard-coded list", async () => {
    view = makeView({}, "bootstrap");
    await render();
    expect(row("bootstrap").className).toContain("flow-row--locked");
    expect(row("requirements-review").className).not.toContain("flow-row--locked");
    expect(row("requirements-review").querySelector("input[type=checkbox]")).toBeTruthy();
  });

  it("locked stage inspector is read-only and explains why", async () => {
    await render();
    await click(row("requirements-review").querySelector(".flow-row__body") as Element);
    const inspector = container.querySelector(".inspector--locked") as HTMLElement;
    expect(inspector).toBeTruthy();
    expect(inspector.querySelector("select, input")).toBeNull();
    expect((inspector.querySelector("details") as HTMLDetailsElement).open).toBe(true);
    expect(inspector.textContent).toContain("registry.json");
  });

  it("stage right after the locked gate cannot move up, with a visible reason", async () => {
    await render();
    await click(row("ux-design").querySelector(".flow-row__body") as Element);
    const up = byText("ย้ายขึ้น");
    expect(up.getAttribute("aria-disabled")).toBe("true");
    expect(document.getElementById(String(up.getAttribute("aria-describedby")))?.textContent).toContain("ต้องอยู่หลังขั้นที่ล็อก");
    expect(container.textContent).toContain("ต้องตามหลังขั้นที่ล็อก: requirements-review");
    await click(up);
    expect(Array.from(container.querySelectorAll(".flow-steps > li")).map((li) => li.getAttribute("data-stage"))[3]).toBe("ux-design");
  });

  it("keyboard reorder: Space picks, arrows move with a live announcement, Esc restores", async () => {
    await render();
    const order = () => Array.from(container.querySelectorAll(".flow-steps > li")).map((li) => li.getAttribute("data-stage"));
    const before = order();
    const handle = row("develop").querySelector(".flow-row__handle") as HTMLButtonElement;
    const key = async (target: HTMLElement, k: string) => {
      await act(async () => {
        target.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true }));
      });
    };
    await key(handle, " ");
    expect(container.querySelector("[role=status][aria-live=assertive]")?.textContent).toContain("หยิบขั้น develop");
    await key(row("develop").querySelector(".flow-row__handle") as HTMLElement, "ArrowUp");
    expect(order().indexOf("develop")).toBe(before.indexOf("develop") - 1);
    expect(container.querySelector("[role=status][aria-live=assertive]")?.textContent).toBe("ย้ายไปลำดับที่ 5 จาก 7");
    await key(row("develop").querySelector(".flow-row__handle") as HTMLElement, "Escape");
    expect(order()).toEqual(before);
  });

  it("validates live (debounced) through the API and shows row errors", async () => {
    await render();
    const calls = validateBodies.length;
    await click(row("devops").querySelector("input[type=checkbox]") as Element);
    expect(validateBodies.length).toBe(calls); // ยังไม่ถึง 200ms
    await settle();
    expect(validateBodies.length).toBe(calls + 1);
    expect(container.querySelector(".validation-panel")?.textContent).toContain("ตรวจแล้ว ไม่พบปัญหา");
    expect(container.querySelector(".validation")?.getAttribute("aria-live")).toBe("polite");
  });

  it("disabling ux on a frontend project needs a separate confirm (cancel focused first)", async () => {
    await render();
    await click(row("ux-design").querySelector("input[type=checkbox]") as Element);
    await settle();
    const dialog = document.querySelector("[role=alertdialog]") as HTMLElement;
    expect(dialog).toBeTruthy();
    expect(document.activeElement?.textContent).toBe("ยกเลิก");
    await click(byTextIn(dialog, "ยกเลิก"));
    expect((row("ux-design").querySelector("input[type=checkbox]") as HTMLInputElement).checked).toBe(true);
    expect(document.querySelector("[role=alertdialog]")).toBeNull();

    await click(row("ux-design").querySelector("input[type=checkbox]") as Element);
    await settle();
    await click(byTextIn(document.querySelector("[role=alertdialog]") as HTMLElement, "ปิดขั้นนี้"));
    await settle();
    await click(byText("ตรวจสอบและดูการเปลี่ยนแปลง"));
    await settle(50);
    expect(previewBodies.at(-1)?.payload.acknowledged).toEqual(["ux_disabled_frontend"]);
    // สัญญากับ write pipeline: kind/target จาก API/base_sha256 ของไฟล์ที่โหลด
    expect(previewBodies.at(-1)).toMatchObject({ kind: "flow", target: ".veda/projects/demo/flow.yaml", base_sha256: "a".repeat(64) });
    expect(container.querySelector("[data-testid=where]")?.textContent).toBe("/changes/review?step=review");
    expect(getPending()?.returnTo).toBe("/p/demo/flow");
  });

  it("dialog trap: focus starts on cancel and is pulled back from anywhere outside (B2)", async () => {
    await render();
    await click(row("ux-design").querySelector("input[type=checkbox]") as Element);
    await settle();
    // ปิด prompt แรก (จากการแก้) ด้วย Esc แล้วค่อยเปิดจากปุ่มตรวจสอบด้วยคีย์บอร์ดจริง
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(document.querySelector("[role=alertdialog]")).toBeNull();
    await click(row("ux-design").querySelector("input[type=checkbox]") as Element);
    await settle();
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    // declined แล้ว (ยังปิด ux อยู่ไม่ได้ — ยกเลิกคืน draft) → เปิด ux อีกครั้งเพื่อให้มีงานบันทึกจริง
    expect((row("ux-design").querySelector("input[type=checkbox]") as HTMLInputElement).checked).toBe(true);
    await click(row("ux-design").querySelector("input[type=checkbox]") as Element);
    await settle();
    const dialog0 = document.querySelector("[role=alertdialog]") as HTMLElement;
    expect(dialog0).toBeTruthy();
    expect(document.activeElement?.textContent).toBe("ยกเลิก");
    // focus ถูกย้ายไปนอก dialog (เช่น event ของเมาส์จริงที่มาทีหลัง) → ถูกดึงกลับ
    const outside = byText("รีเซ็ตเป็น default");
    await act(async () => {
      outside.focus();
    });
    expect(dialog0.contains(document.activeElement)).toBe(true);
    // Tab ตอน focus อยู่นอก (เช่น body) → กลับเข้า dialog
    await act(async () => {
      (document.activeElement as HTMLElement).blur();
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true }));
    });
    expect(dialog0.contains(document.activeElement)).toBe(true);
    // focus หลุดไป body โดยไม่มี focusin/Tab (เช่น mouseup ของคลิกจริง) → re-check ในเฟรมถัดไปดึงกลับ
    await act(async () => {
      (document.activeElement as HTMLElement).blur();
    });
    expect(document.activeElement).toBe(document.body);
    await settle(150);
    expect(document.activeElement?.textContent).toBe("ยกเลิก");
  });

  it("cancel on the review-button prompt keeps the draft as it is", async () => {
    view = makeView({ warnings: [{ code: "ux_disabled_frontend", error: "ปิด ux อยู่แล้ว", track: "full" }] });
    view.tracks.full.stages = view.tracks.full.stages.map((s) => (s.id === "ux-design" ? { ...s, enabled: false } : s));
    await render();
    await click(row("devops").querySelector("input[type=checkbox]") as Element);
    await settle();
    await click(byText("ตรวจสอบและดูการเปลี่ยนแปลง"));
    await settle(50);
    await click(byTextIn(document.querySelector("[role=alertdialog]") as HTMLElement, "ยกเลิก"));
    expect((row("devops").querySelector("input[type=checkbox]") as HTMLInputElement).checked).toBe(true); // การแก้ยังอยู่
    expect(previewBodies.length).toBe(0);
  });

  it("confirming from the review button goes straight to DiffPreview (N2)", async () => {
    view = makeView({ warnings: [{ code: "ux_disabled_frontend", error: "โปรเจกต์ demo: ปิด ux อยู่แล้ว", track: "full" }] });
    view.tracks.full.stages = view.tracks.full.stages.map((s) => (s.id === "ux-design" ? { ...s, enabled: false } : s));
    await render();
    expect(container.textContent).toContain("สถานะปัจจุบันมีคำเตือน"); // ไม่ยืนยันแทน — แสดงเป็นแบนเนอร์
    await click(row("devops").querySelector("input[type=checkbox]") as Element);
    await settle();
    expect(document.querySelector("[role=alertdialog]")).toBeNull(); // แก้ขั้นอื่นไม่ถาม
    const primary = byText("ตรวจสอบและดูการเปลี่ยนแปลง");
    await act(async () => {
      primary.focus();
      primary.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      primary.click(); // Enter บนปุ่ม = click ในเบราว์เซอร์
    });
    await settle(50);
    const dialog = document.querySelector("[role=alertdialog]") as HTMLElement;
    expect(dialog).toBeTruthy();
    expect(document.activeElement?.textContent).toBe("ยกเลิก");
    await click(byTextIn(dialog, "ปิดขั้นนี้"));
    await settle(80);
    expect(previewBodies.at(-1)?.payload.acknowledged).toEqual(["ux_disabled_frontend"]);
    expect(container.querySelector("[data-testid=where]")?.textContent).toBe("/changes/review?step=review");
  });

  it("no frontend: ux closes immediately with the 'fits' label", async () => {
    view = makeView({ hasFrontend: false });
    await render();
    await click(row("ux-design").querySelector("input[type=checkbox]") as Element);
    await settle();
    expect(document.querySelector("[role=alertdialog]")).toBeNull();
    expect(row("ux-design").textContent).toContain("เหมาะสม: โปรเจกต์ไม่มี frontend");
  });

  it("hand-edited flow that skips the lock: red banner, blocked review, fix to policy", async () => {
    const broken = makeView();
    broken.tracks.full.stages = broken.tracks.full.stages.map((s) => (s.id === "requirements-review" ? { ...s, enabled: false } : s));
    view = { ...broken, issues: [{ code: "locked_gate_violation", error: "ข้าม gate", stage: "requirements-review", track: "full" }],
             policyFix: makeView().defaultFlow };
    await render();
    expect(container.textContent).toContain("flow นี้ข้ามขั้นที่ล็อก — ใช้งานไม่ได้");
    const primary = byText("ตรวจสอบและดูการเปลี่ยนแปลง");
    expect(primary.getAttribute("aria-disabled")).toBe("true");
    expect(document.getElementById(String(primary.getAttribute("aria-describedby")))?.textContent).toContain("แก้ให้ตรงนโยบาย");
    expect(row("requirements-review").querySelector(".flow-row__errors")?.textContent).toContain("ข้าม gate");
    await click(byText("แก้ให้ตรงนโยบาย"));
    await settle();
    expect(byText("ตรวจสอบและดูการเปลี่ยนแปลง").getAttribute("aria-disabled")).toBe("false");
  });

  it("reset to default sends the default flow; going back keeps the draft in memory", async () => {
    await render();
    await click(byText("รีเซ็ตเป็น default"));
    await settle();
    await click(byText("ตรวจสอบและดูการเปลี่ยนแปลง"));
    await settle(50);
    const sent = previewBodies.at(-1)?.payload.flow as Draft;
    expect(sent.tracks.full.stages.find((s) => s.id === "devops")?.enabled).toBe(true);
    act(() => root.unmount());
    root = createRoot(container);
    await render();
    expect((row("devops").querySelector("input[type=checkbox]") as HTMLInputElement).checked).toBe(true); // draft เดิม ไม่ใช่ไฟล์
  });

  it("mobile: inspector opens as a bottom sheet and validation is a summary bar", async () => {
    wide = false;
    await render();
    expect(container.querySelector(".validation-bar")).toBeTruthy();
    expect(row("develop").querySelector(".flow-row__handle")?.getAttribute("draggable")).toBe("false");
    await click(row("develop").querySelector(".flow-row__body") as Element);
    const sheet = container.querySelector(".sheet[role=dialog]") as HTMLElement;
    expect(sheet).toBeTruthy();
    expect(sheet.querySelector("select")).toBeTruthy();
    await click(sheet.querySelector(".sheet__close") as Element);
    expect(container.querySelector(".sheet")).toBeNull();
  });
});

function byTextIn(scope: HTMLElement, text: string): HTMLElement {
  return Array.from(scope.querySelectorAll<HTMLElement>("button")).find((b) => b.textContent?.includes(text)) as HTMLElement;
}

describe("FlowEditor inspector and edge cases (review 4-2)", () => {
  let container: HTMLDivElement;
  let root: Root;
  let view: EditorView;
  let validateBodies: Array<{ tracks: Record<string, { stages: Array<Record<string, unknown>> }> }>;
  let failValidate = false;

  beforeEach(() => {
    view = makeView();
    validateBodies = [];
    failValidate = false;
    dropDraft("project:demo");
    clearPending();
    vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/flows/demo") return json(200, { ok: true, data: view });
      if (url === "/api/agents/names") return json(200, { ok: true, data: [{ name: "ba", description: "" }, { name: "devops", description: "" }] });
      if (url === "/api/skills?usage=0") return json(200, { ok: true, data: [{ id: "bmad-build", description: "" }] });
      if (url === "/api/flows/demo/validate") {
        if (failValidate) return json(503, { ok: false, code: "x", error: "x" });
        validateBodies.push(JSON.parse(String(init?.body)).flow);
        const warn = view.warnings ?? [];
        return json(200, { ok: true, data: { errors: [], warnings: warn, ackRequired: warn.map((w) => w.code), overrides: 1,
                                             resetsToDefault: false, overlay: {}, tracks: view.tracks } });
      }
      return json(404, { ok: false, code: "not_found", error: "x" });
    }));
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  async function settle(ms = 260) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, ms));
    });
  }

  async function render() {
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={["/p/demo/flow"]}>
          <Routes>
            <Route path="/p/:name/flow" element={<ProjectFlowEditorPage />} />
          </Routes>
        </MemoryRouter>,
      );
    });
    await settle();
  }

  const row = (id: string) => container.querySelector<HTMLLIElement>(`[data-stage="${id}"]`) as HTMLLIElement;
  const select = async (id: string) => {
    await act(async () => (row(id).querySelector(".flow-row__body") as HTMLElement).click());
  };
  const lastStage = (id: string) => validateBodies.at(-1)?.tracks.full.stages.find((x) => x.id === id);

  it("shows the default value and restores it per field", async () => {
    await render();
    await select("devops");
    const inspector = container.querySelector(".inspector") as HTMLElement;
    expect(inspector.textContent).toContain("ค่า default: เปิด");
    const restore = Array.from(inspector.querySelectorAll("button")).find((b) => b.textContent?.includes("คืนค่า default")) as HTMLElement;
    await act(async () => restore.click());
    await settle();
    expect(lastStage("devops")?.enabled).toBe(true);
  });

  it("gate segment edits the draft that is validated", async () => {
    await render();
    await select("develop");
    const btn = Array.from(container.querySelectorAll(".inspector .segmented__item")).find((b) => b.textContent === "agent ยืนยัน") as HTMLElement;
    await act(async () => btn.click());
    await settle();
    expect(lastStage("develop")?.gate).toBe("agent-confirm");
  });

  it("unknown agent shows 'not found'; reserved agents from the API do not", async () => {
    await render();
    await select("develop"); // agent developer ไม่อยู่ในรายชื่อ mock
    expect(container.querySelector(".inspector")?.textContent).toContain("developer (ไม่พบ)");
    view = makeView({}, "bootstrap"); // gate ไม่ล็อก → inspector แก้ได้ แต่ human เป็น agent สงวน
    act(() => root.unmount());
    root = createRoot(container);
    await render();
    await select("requirements-review");
    expect(container.querySelector(".inspector")?.textContent).not.toContain("ไม่พบ");
  });

  it("a stored draft is dropped when the file changed on disk", async () => {
    await render();
    await act(async () => (row("devops").querySelector("input[type=checkbox]") as HTMLElement).click());
    await settle();
    act(() => root.unmount());
    view = { ...makeView(), base_sha256: "c".repeat(64) };
    root = createRoot(container);
    await render();
    expect((row("devops").querySelector("input[type=checkbox]") as HTMLInputElement).checked).toBe(false);
  });

  it("a saved file that already disables ux does not prompt on unrelated edits, only on review", async () => {
    view = makeView({ warnings: [{ code: "ux_disabled_frontend", error: "ปิด ux อยู่แล้ว", track: "full" }] });
    await render();
    await act(async () => (row("devops").querySelector("input[type=checkbox]") as HTMLElement).click());
    await settle();
    expect(document.querySelector("[role=alertdialog]")).toBeNull();
    const primary = Array.from(container.querySelectorAll("button")).find((b) => b.textContent?.includes("ตรวจสอบและดูการเปลี่ยนแปลง")) as HTMLElement;
    await act(async () => primary.click());
    await settle(50);
    expect(document.querySelector("[role=alertdialog]")).toBeTruthy(); // ไม่ยืนยันแทนผู้ใช้
  });

  it("failed validation offers a retry and says why review is blocked", async () => {
    failValidate = true;
    await render();
    expect(container.textContent).toContain("ตรวจ flow ไม่ได้");
    failValidate = false;
    const retry = Array.from(container.querySelectorAll(".validation button")).find((b) => b.textContent === "ลองใหม่") as HTMLElement;
    await act(async () => retry.click());
    await settle();
    expect(container.querySelector(".validation-panel")?.textContent).toContain("ตรวจแล้ว ไม่พบปัญหา");
  });

  it("Esc after a keyboard move keeps other edits made while picked", async () => {
    await render();
    const key = async (id: string, k: string) => {
      await act(async () => {
        (row(id).querySelector(".flow-row__handle") as HTMLElement).dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true }));
      });
    };
    await key("develop", " ");
    await key("develop", "ArrowUp");
    await act(async () => (row("devops").querySelector("input[type=checkbox]") as HTMLElement).click());
    await key("develop", "Escape");
    await settle();
    expect(lastStage("develop")?.after).toEqual(["architecture"]);
    expect(lastStage("devops")?.enabled).toBe(true);
  });

  it("drag and drop moves step by step and stops at a locked stage", async () => {
    await render();
    const drop = async (target: string, id: string) => {
      await act(async () => {
        const event = new Event("drop", { bubbles: true, cancelable: true }) as Event & { dataTransfer: unknown };
        Object.defineProperty(event, "dataTransfer", { value: { getData: () => id } });
        row(target).dispatchEvent(event);
      });
    };
    const order = () => Array.from(container.querySelectorAll(".flow-steps > li")).map((li) => li.getAttribute("data-stage"));
    await drop("ux-design", "develop");
    expect(order().slice(3, 6)).toEqual(["develop", "ux-design", "architecture"]);
    await drop("bootstrap", "develop"); // ข้าม gate ไม่ได้ — หยุดใต้ขั้นที่ล็อก
    expect(order().indexOf("develop")).toBe(order().indexOf("requirements-review") + 1);
    expect(container.querySelector("[aria-live=assertive]")?.textContent).toContain("ต้องอยู่หลังขั้นที่ล็อก");
    await drop("architecture", "not-a-stage");
    expect(order().indexOf("develop")).toBe(3);
  });
});
