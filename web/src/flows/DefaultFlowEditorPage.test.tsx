import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { EditorStage, FlowEditor as EditorView } from "../api/types";
import { clearPending } from "../changes/changeStore";
import { DefaultFlowEditorPage } from "./DefaultFlowEditorPage";
import { dropDraft } from "./draftStore";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("../auth/SessionContext", () => ({
  useSession: () => ({ session: { authenticated: true, username: "admin", role: "admin", csrf: "c", review_url: null } }),
}));

function st(id: string, after: string[], over: Partial<EditorStage> = {}): EditorStage {
  const base = { id, agent: "ba", skill: null, enabled: true, optional: false, after, gate: "none" as const, produces: [], condition: null };
  return { ...base, locked: false, lockReason: null, overridden: false, overriddenFields: [], mustFollowLocked: false,
           default: { agent: base.agent, skill: null, enabled: true, optional: false, after, gate: "none", produces: [], condition: null }, ...over };
}

function makeView(exists: boolean): EditorView {
  const full = [st("requirements", []), st("requirements-review", ["requirements"], { agent: "human", gate: "human", locked: true, lockReason: "ล็อก" }),
                st("develop", ["requirements-review"], { agent: "developer", mustFollowLocked: true })];
  const plain = full.map(({ id, agent, skill, enabled, optional, after, gate, produces, condition }) =>
    ({ id, agent, skill, enabled, optional, after, gate, produces, condition }));
  return {
    project: "", target: ".veda/flow.default.yaml", hasFrontend: null, base_sha256: exists ? "d".repeat(64) : null, editable: true,
    notEditableReason: null, exists, flow: { default: exists ? ".veda/flow.default.yaml" : "packaged", overlay: null }, version: 1,
    tracks: { full: { entry_condition: null, stages: full, lockedFields: [] } },
    defaultFlow: { version: 1, tracks: { full: { entry_condition: null, stages: plain } } },
    issues: [], loadError: false, policyFix: null, overrides: 0, warnings: [],
    options: { gates: ["none", "human", "agent-confirm"], conditions: ["hasFrontend"], reservedAgents: ["human"] },
  };
}

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("DefaultFlowEditorPage (story 4-3)", () => {
  let container: HTMLDivElement;
  let root: Root;
  let view: EditorView;
  let previews: Array<Record<string, unknown>>;

  beforeEach(() => {
    view = makeView(false);
    previews = [];
    dropDraft("default");
    clearPending();
    vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/flow-default") return json(200, { ok: true, data: view });
      if (url === "/api/agents/names") return json(200, { ok: true, data: [{ name: "ba", description: "" }, { name: "developer", description: "" }] });
      if (url === "/api/skills") return json(200, { ok: true, data: [] });
      if (url === "/api/flow-default/validate") {
        const draft = JSON.parse(String(init?.body)).flow;
        const devOff = !draft.tracks.full.stages.find((s: { id: string }) => s.id === "develop").enabled;
        return json(200, { ok: true, data: {
          errors: devOff ? [{ code: "project_flow_broken", error: "โปรเจกต์ uses-qa: overlay แก้ขั้น qa ที่ไม่มีใน default", project: "uses-qa",
                              stage: "qa", reason: "unknown_stage" }] : [],
          warnings: [], ackRequired: [], overrides: devOff ? 1 : 0, resetsToDefault: !devOff, overlay: {}, tracks: view.tracks } });
      }
      if (url === "/api/changes/preview") {
        previews.push(JSON.parse(String(init?.body)));
        return json(422, { ok: false, code: "x", error: "x" });
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
        <MemoryRouter initialEntries={["/flow/default"]}>
          <Routes>
            <Route path="/flow/default" element={<DefaultFlowEditorPage />} />
          </Routes>
        </MemoryRouter>,
      );
    });
    await settle();
  }

  it("warns about the impact and explains the packaged default when there is no file", async () => {
    await render();
    expect(container.textContent).toContain("กระทบทุกโปรเจกต์ที่ไม่ override");
    expect(container.textContent).toContain("ยังไม่มี .veda/flow.default.yaml");
    expect(container.textContent).toContain("default ที่มากับแอป · เท่ากับ default ของแอป");
    expect(container.querySelector('[data-stage="requirements-review"]')?.className).toContain("flow-row--locked");
  });

  it("shows projects that would break by name and blocks review", async () => {
    view = makeView(true);
    await render();
    expect(container.textContent).not.toContain("ยังไม่มี .veda/flow.default.yaml");
    await act(async () => (container.querySelector('[data-stage="develop"] input[type=checkbox]') as HTMLElement).click());
    await settle();
    const panel = container.querySelector(".validation-panel") as HTMLElement;
    expect(panel.textContent).toContain("โปรเจกต์ uses-qa");
    expect(Array.from(panel.querySelectorAll("button")).some((b) => b.textContent === "ไปที่ขั้น")).toBe(false);
    const primary = Array.from(container.querySelectorAll("button")).find((b) => b.textContent?.includes("ตรวจสอบและดูการเปลี่ยนแปลง")) as HTMLElement;
    expect(primary.getAttribute("aria-disabled")).toBe("true");
  });

  it("sends kind flow-default with the target from the API", async () => {
    view = makeView(true);
    await render();
    await act(async () => (container.querySelector('[data-stage="requirements"] input[type=checkbox]') as HTMLElement).click());
    await settle();
    const primary = Array.from(container.querySelectorAll("button")).find((b) => b.textContent?.includes("ตรวจสอบและดูการเปลี่ยนแปลง")) as HTMLElement;
    await act(async () => primary.click());
    await settle(50);
    expect(previews.at(-1)).toMatchObject({ kind: "flow-default", target: ".veda/flow.default.yaml", base_sha256: "d".repeat(64) });
  });
});
