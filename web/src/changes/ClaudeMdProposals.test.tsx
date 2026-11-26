import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setCsrfToken } from "../api/client";
import type { ChangePreview, ClaudeMdProposals } from "../api/types";
import { ChangeReview } from "./ChangeReview";
import { ChangesPage } from "./ChangesPage";
import { clearPending, getPending } from "./changeStore";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const diff = {
  hunks: [{ old_start: 22, old_lines: 1, new_start: 22, new_lines: 2, lines: [
    { type: "context" as const, old: 22, new: 22, text: "- `designReady != true` → หยุด", spans: [] },
    { type: "add" as const, old: null, new: 23, text: "  - ยกเว้นเมื่อ flow ปิดขั้น `ux-design`", spans: [] },
  ] }],
  added: 1, removed: 0, truncated: false, old_eof_newline: true, new_eof_newline: true,
};

const data: ClaudeMdProposals = {
  path: "CLAUDE.md", exists: true, base_sha256: "c".repeat(64), issues: [], command: "uv run …",
  blocks: [{ block: "flow-resolver-rule", state: "missing" }, { block: "dispatch-table", state: "applied" }],
  agentGuards: [
    { agent: "developer", path: ".claude/agents/developer.md", sha256: "d".repeat(64), state: "suggested", diff,
      guards: [{ line: 22, flag: "designReady", stage: "ux-design", text: "- `designReady != true` → หยุด", applied: false }] },
  ],
};

const preview: ChangePreview = {
  preview_id: "p".repeat(32), path: "CLAUDE.md", kind: "claude-md", op: "modify", impact: "high", confirm_phrase: "CLAUDE.md",
  base_sha256: "c".repeat(64), proposed_sha256: "e".repeat(64), diff: { ...diff, hunks: [] }, warnings: [], errors: [],
  confirm_token: "t", expires_at: "2026-11-26T12:00:00Z",
};

let location = "";
function Probe() {
  const loc = useLocation();
  location = loc.pathname + loc.search;
  return null;
}

describe("ClaudeMdProposals (/changes)", () => {
  let container: HTMLDivElement;
  let root: Root;
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  let proposals: ClaudeMdProposals = data;

  beforeEach(() => {
    calls.length = 0;
    proposals = data;
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener: () => undefined, removeEventListener: () => undefined }));
    vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url === "/api/claude-md/proposals") return Promise.resolve(json(200, { ok: true, data: proposals }));
      if (url === "/api/changes") return Promise.resolve(json(200, { ok: true, data: [] }));
      if (url === "/api/changes/preview") return Promise.resolve(json(200, { ok: true, data: preview }));
      return Promise.resolve(json(404, { ok: false, code: "not_found", error: "x" }));
    });
    setCsrfToken("csrf");
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    clearPending();
    vi.unstubAllGlobals();
  });

  async function render(url = "/changes") {
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={[url]}>
          <Routes>
            <Route path="/changes" element={<ChangesPage />} />
            <Route path="/changes/review" element={<ChangeReview />} />
          </Routes>
          <Probe />
        </MemoryRouter>,
      );
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
  const text = () => container.textContent ?? "";
  const buttons = (label: string) => [...container.querySelectorAll("button")].filter((b) => b.textContent?.includes(label));
  async function click(el: Element | undefined) {
    if (!el) throw new Error("missing element");
    await act(async () => {
      el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await new Promise((r) => setTimeout(r, 0));
    });
  }

  it("shows each block with its state and the untouched-text note", async () => {
    await render();
    expect(text()).toContain("ข้อเสนอผูก flow กับ CLAUDE.md");
    expect(text()).toContain("ข้อความนอก marker จะไม่ถูกแตะ");
    expect(text()).toContain("กติกา resolver");
    expect(text()).toContain("ยังไม่ใช้");
    expect(text()).toContain("ใช้แล้ว");
    expect(text()).toContain("ยังไม่มีการแก้ไข"); // ส่วนประวัติยังอยู่ (ว่าง)
  });

  it("an applied block's button is disabled with a visible reason and makes no request", async () => {
    await render();
    const applied = buttons("ดู diff")[1];
    expect(applied.getAttribute("aria-disabled")).toBe("true");
    const reason = document.getElementById(applied.getAttribute("aria-describedby") ?? "");
    expect(reason?.textContent).toContain("ตรงกับที่แอปสร้างอยู่แล้ว");
    await click(applied);
    expect(calls.some((c) => c.url === "/api/changes/preview")).toBe(false);
  });

  it("ดู diff opens a claude-md preview (block only, no text) and the high-impact review", async () => {
    await render();
    await click(buttons("ดู diff")[0]);
    const sent = calls.find((c) => c.url === "/api/changes/preview");
    expect(JSON.parse(String(sent?.init?.body))).toEqual({
      kind: "claude-md", target: "CLAUDE.md", payload: { block: "flow-resolver-rule" }, base_sha256: "c".repeat(64),
    });
    expect(location).toBe("/changes/review?step=review");
    expect(getPending()?.preview.impact).toBe("high");
    expect(text()).toContain("ข้อความนอก marker จะไม่ถูกแตะ");
    expect(text()).toContain("ผลสูง");
  });

  it("stale and invalid states", async () => {
    proposals = { ...data, issues: [{ code: "claude_md_marker_duplicate", error: "บรรทัด 3: block dispatch-table ซ้ำ" }],
      blocks: [{ block: "flow-resolver-rule", state: "invalid" }, { block: "dispatch-table", state: "invalid" }] };
    await render();
    expect(text()).toContain("marker ผิดรูป");
    expect(text()).toContain("บรรทัด 3: block dispatch-table ซ้ำ");
    expect(buttons("ดู diff").slice(0, 2).every((b) => b.getAttribute("aria-disabled") === "true")).toBe(true);
    act(() => root.unmount());
    root = createRoot(container);
    proposals = { ...data, blocks: [{ block: "flow-resolver-rule", state: "stale" }, { block: "dispatch-table", state: "missing" }] };
    await render();
    expect(text()).toContain("ล้าสมัย");
    expect(text()).toContain("ทะเบียน agent เปลี่ยน");
    expect(buttons("ดู diff")[0].getAttribute("aria-disabled")).toBe("false");
  });

  it("a guard mixed with another gate shows the manual-split state and no diff", async () => {
    proposals = { ...data, agentGuards: [{ agent: "mixed", path: ".claude/agents/mixed.md", sha256: null, state: "manual", diff: null,
      guards: [{ line: 4, flag: "designReady", stage: "ux-design", text: "- `planReady != true` และ designReady != true", applied: false,
        manual: true, otherGates: ["planReady"] }] }] };
    await render();
    expect(text()).toContain("ต้องแยกเงื่อนไขเอง");
    expect(text()).toContain("gate อื่นปนอยู่ (planReady)");
    expect(buttons("ดู diff")).toHaveLength(2); // เฉพาะสอง block ของ CLAUDE.md
  });

  it("agent guard suggestion is read-only: diff toggles inline and nothing is posted", async () => {
    await render();
    expect(text()).toContain("ข้อเสนอแก้ guard ของ agent");
    expect(text()).toContain("developer");
    expect(text()).toContain("บรรทัด 22");
    const toggle = buttons("ดู diff")[2];
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    await click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(text()).toContain("ดูได้อย่างเดียว");
    expect(text()).toContain("ยกเว้นเมื่อ flow ปิดขั้น");
    expect(calls.filter((c) => c.init?.method === "POST")).toEqual([]);
    expect(location).toBe("/changes");
  });
});
