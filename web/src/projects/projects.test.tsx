import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { FlowStatus, ProjectSummary, StageRow } from "../api/types";
import { ProjectFlowPage } from "./ProjectFlowPage";
import { groupOf, ProjectsPage } from "./ProjectsPage";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const chips = [
  { id: "requirements", agent: "ba", status: "done", skipReason: null, gate: "none" },
  { id: "requirements-review", agent: "human", status: "active", skipReason: null, gate: "human" },
  { id: "ux-design", agent: "ux-designer", status: "skipped", skipReason: "condition hasFrontend ไม่เป็นจริงใน registry", gate: "none" },
] as ProjectSummary["stages"];

const projects: ProjectSummary[] = [
  { project: "alpha", description: "UI app", status: "developing", error: null, complete: false, overridden: true,
    next: { stage: "review", agent: "reviewer", skill: "bmad-code-review", gate: "agent-confirm", optional: false, status: "active" },
    stages: chips },
  { project: "gatey", description: "รอคน", status: "bootstrapped", error: null, complete: false, overridden: false,
    next: { stage: "requirements-review", agent: "human", gate: "human", optional: false, status: "active" }, stages: chips },
  { project: "shipped", status: "deployed", error: null, complete: true, overridden: false, next: null, stages: chips },
  { project: "broken", status: null, complete: false, overridden: false, next: null, stages: [],
    error: { code: "flow_invalid", source: ".veda/projects/broken/flow.yaml", issues: [{ code: "locked_gate_violation", error: "x" }] } },
];

function row(over: Partial<StageRow>): StageRow {
  return { id: "x", agent: "ba", skill: null, gate: "none", after: [], enabled: true, optional: false, condition: null,
           status: "pending", skipReason: null, note: null, locked: false, lockReason: null, overridden: false, default: null, ...over };
}

const status: FlowStatus = {
  project: "gatey", hasSprint: false, flow: { default: "packaged", overlay: ".veda/projects/gatey/flow.yaml" },
  registry: {}, track: "full", complete: false, hints: [], warnings: [{ code: "condition_unknown", error: "registry ไม่มี hasFrontend" }],
  reviewUrl: "https://example.test/review",
  next: { stage: "requirements-review", agent: "human", gate: "human", optional: false, status: "active", note: "รอคน" },
  locked: [{ stage: "requirements-review", reason: "ล็อกโดยนโยบาย" }],
  stages: [
    row({ id: "requirements", agent: "ba", status: "done" }),
    row({ id: "requirements-review", agent: "human", gate: "human", status: "active", locked: true, lockReason: "ล็อกโดยนโยบาย" }),
    row({ id: "ux-design", agent: "ux-designer", status: "skipped", skipReason: "ปิดไว้ใน flow (enabled: false)", overridden: true }),
  ],
};

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("projects views", () => {
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
            <Route path="/" element={<ProjectsPage />} />
            <Route path="/p/:name" element={<ProjectFlowPage />} />
          </Routes>
        </MemoryRouter>,
      );
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
  const text = () => container.textContent ?? "";

  it("groups projects (not a card grid) with stage chips and override badge", async () => {
    fetchMock.mockResolvedValue(json(200, { ok: true, data: projects }));
    await render("/");
    const headings = [...container.querySelectorAll("h2")].map((h) => h.textContent ?? "");
    expect(headings).toEqual(["รอคนอนุมัติ (1)", "กำลังดำเนินการ (1)", "flow ผิดกฎ (1)", "เสร็จแล้ว (1)"]);
    expect(text()).toContain(".veda/projects/broken/flow.yaml");
    expect(text()).toContain("review · reviewer (gate: agent ยืนยัน)");
    expect(text()).toContain("ปรับเอง");
    expect(text()).toContain("flow ผิดกฎ — resolver ไม่เดาขั้นถัดไป");
    const chip = container.querySelector(".stage-chip--skipped");
    expect(chip?.textContent).toContain("hasFrontend");
    expect(container.querySelectorAll(".stage-chip").length).toBe(9);
    expect(groupOf(projects[1])).toBe("human");
  });

  it("a project with no next step that is not complete is not grouped as done", async () => {
    const stuck: ProjectSummary = { ...projects[0], project: "stuck", next: null, complete: false, overridden: false };
    expect(groupOf(stuck)).toBe("working");
    fetchMock.mockResolvedValue(json(200, { ok: true, data: [stuck] }));
    await render("/");
    await act(async () => {
      container.querySelector<HTMLInputElement>("input[type=checkbox]")!.click();
    });
    expect(text()).toContain("stuck");
    expect(text()).toContain("ยังไม่มีขั้นถัดไปที่ทำได้");
  });

  it("filters by search and hides done projects", async () => {
    fetchMock.mockResolvedValue(json(200, { ok: true, data: projects }));
    await render("/");
    const input = container.querySelector<HTMLInputElement>("input[type=search]")!;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
      setter.call(input, "รอ");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(container.querySelectorAll(".project-row").length).toBe(1);
    expect(text()).toContain("แสดง 1 จาก 4 โปรเจกต์");
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
      setter.call(input, "");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
      container.querySelector<HTMLInputElement>("input[type=checkbox]")!.click();
    });
    expect(text()).not.toContain("shipped");
    expect(container.querySelectorAll(".project-row").length).toBe(3);
  });

  it("shows next-step callout, skip reason, lock from API and an external /review link without approve button", async () => {
    fetchMock.mockResolvedValue(json(200, { ok: true, data: status }));
    await render("/p/gatey");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/projects/gatey/flow-status?track=full");
    expect(container.querySelector(".callout")?.textContent).toContain("ขั้นถัดไป:");
    expect(container.querySelector(".callout")?.textContent).toContain("requirements-review · คนอนุมัติ (gate: คนอนุมัติ)");
    expect(text()).toContain("ปิดไว้ใน flow (enabled: false)");
    expect(container.querySelector(".stage-row--skipped")).not.toBeNull();
    expect(container.querySelector(".stage-row--locked")?.textContent).toContain("ล็อกโดยนโยบาย");
    const links = [...container.querySelectorAll<HTMLAnchorElement>("a[target=_blank]")];
    expect(links.length).toBeGreaterThan(0);
    expect(links.every((a) => a.href === "https://example.test/review" && a.rel.includes("noopener"))).toBe(true);
    const buttons = [...container.querySelectorAll("button")].map((b) => b.textContent ?? "");
    expect(buttons.some((b) => b.includes("อนุมัติ"))).toBe(false);
    expect(buttons.filter((b) => !["full", "quick"].includes(b))).toEqual([]); // ไม่มีปุ่มที่เขียนอะไร
    expect(text()).toContain("ยังไม่มีแผน sprint");
    expect(text()).toContain("registry ไม่มี hasFrontend");
    expect(fetchMock.mock.calls.every(([, init]) => !init || !init.method || init.method === "GET")).toBe(true);
  });

  it("switches track via the query string", async () => {
    fetchMock.mockResolvedValue(json(200, { ok: true, data: { ...status, track: "quick", next: null, entry: { ok: false, requirementsReady: true, status: ["running"], reason: "เข้า track quick ไม่ได้" } } }));
    await render("/p/gatey?track=quick");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/projects/gatey/flow-status?track=quick");
    expect(text()).toContain("เข้า track นี้ไม่ได้ — เข้า track quick ไม่ได้");
  });

  it("clicking the track toggle refetches and ignores stale responses", async () => {
    let resolveFirst: (r: Response) => void = () => undefined;
    fetchMock.mockImplementationOnce(() => new Promise<Response>((r) => { resolveFirst = r; }));
    fetchMock.mockResolvedValueOnce(json(200, { ok: true, data: { ...status, track: "quick", next: null,
      entry: { ok: false, requirementsReady: true, status: ["running"], reason: "QUICK-BLOCKED" } } }));
    await render("/p/gatey");
    const quick = [...container.querySelectorAll("button")].find((b) => b.textContent === "quick")!;
    await act(async () => {
      quick.click();
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(fetchMock.mock.calls[1][0]).toBe("/api/projects/gatey/flow-status?track=quick");
    expect(quick.getAttribute("aria-pressed")).toBe("true");
    expect(text()).toContain("QUICK-BLOCKED");
    await act(async () => {
      resolveFirst(json(200, { ok: true, data: status })); // คำตอบของ full ที่มาช้า ต้องไม่ทับ
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(text()).toContain("QUICK-BLOCKED");
  });

  it("shows field-level locks from the API", async () => {
    fetchMock.mockResolvedValue(json(200, { ok: true, data: { ...status, locked: [...status.locked, { field: "entry_condition", reason: "ล็อกเงื่อนไขเข้า quick" }] } }));
    await render("/p/gatey");
    expect(container.querySelector(".flow-status__lock")?.textContent).toContain("entry_condition");
  });

  it("renders flow_invalid issues and not-found", async () => {
    fetchMock.mockResolvedValueOnce(json(422, { ok: false, code: "flow_invalid", error: "x",
      details: { source: ".veda/projects/broken/flow.yaml", issues: [{ code: "locked_gate_violation", stage: "develop", error: "ข้าม gate ไม่ได้" }] } }));
    await render("/p/broken");
    expect(text()).toContain("flow ของโปรเจกต์นี้ผิดกฎ");
    expect(text()).toContain("locked_gate_violation · develop: ข้าม gate ไม่ได้");
    act(() => root.unmount());
    root = createRoot(container);
    fetchMock.mockResolvedValueOnce(json(404, { ok: false, code: "project_not_found", error: "ไม่พบ" }));
    await render("/p/ghost");
    expect(text()).toContain("ไม่พบโปรเจกต์นี้ใน registry");
  });

  it("empty registry", async () => {
    fetchMock.mockResolvedValue(json(200, { ok: true, data: [] }));
    await render("/");
    expect(text()).toContain("ยังไม่มีโปรเจกต์ใน registry");
  });
});
