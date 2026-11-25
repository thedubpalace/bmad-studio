import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setCsrfToken } from "../api/client";
import type { ChangeEntry, ChangePreview } from "../api/types";
import { ChangeReview } from "./ChangeReview";
import { ChangeDetail, ChangesPage, typeLabel } from "./ChangesPage";
import { clearPending, getPending } from "./changeStore";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const entries: ChangeEntry[] = [
  { change_id: "b".repeat(32), at: "2026-11-24T12:05:00Z", path: "_bmad/custom/config.toml", kind: "revert", op: "modify", added: 1, removed: 1, impact: "normal", reverts: "a".repeat(32), new_sha256: "1".repeat(64), current_sha256: "1".repeat(64), changed_since: false },
  { change_id: "a".repeat(32), at: "2026-11-24T12:00:00Z", path: ".claude/agents/developer.md", kind: "text", op: "create", added: 3, removed: 0, impact: "high", reverts: null, new_sha256: "2".repeat(64), current_sha256: "3".repeat(64), changed_since: true },
];

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

let location = "";
function Probe() {
  const loc = useLocation();
  location = loc.pathname + loc.search;
  return null;
}

describe("ChangesPage", () => {
  let container: HTMLDivElement;
  let root: Root;
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener: () => undefined, removeEventListener: () => undefined }));
    fetchMock.mockReset();
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

  async function render(url: string) {
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={[url]}>
          <Routes>
            <Route path="/changes" element={<ChangesPage />} />
            <Route path="/changes/review" element={<ChangeReview />} />
            <Route path="/changes/:changeId" element={<ChangeDetail />} />
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
  const button = (label: string) => [...container.querySelectorAll("button")].find((b) => b.textContent?.includes(label));

  it("labels change types", () => {
    expect(typeLabel({ kind: "text", op: "modify" })).toBe("แก้");
    expect(typeLabel({ kind: "text", op: "create" })).toBe("สร้าง");
    expect(typeLabel({ kind: "text", op: "delete" })).toBe("ลบ (ย้ายไปสำรอง)");
    expect(typeLabel({ kind: "revert", op: "modify" })).toBe("คืนค่า");
  });

  it("lists entries newest first with time, type, stats, diff link and revert", async () => {
    fetchMock.mockResolvedValue(json(200, { ok: true, data: entries }));
    await render("/changes");
    const paths = [...container.querySelectorAll(".change-item__path")].map((p) => p.textContent);
    expect(paths).toEqual(["_bmad/custom/config.toml", ".claude/agents/developer.md"]);
    expect(text()).toContain("คืนค่า");
    expect(text()).toContain("สร้าง");
    expect(text()).toContain("+3 −0 บรรทัด");
    expect(text()).toContain("ไฟล์ถูกแก้ต่อหลังรายการนี้");
    expect([...container.querySelectorAll(".change-item")][1].querySelector(`a[href="/changes/${"a".repeat(32)}"]`)?.textContent).toBe("ดู diff");
    expect(container.querySelectorAll("time")[0].getAttribute("dateTime")).toBe("2026-11-24T12:05:00Z");
  });

  it("shows the empty state when nothing was written", async () => {
    fetchMock.mockResolvedValue(json(200, { ok: true, data: [] }));
    await render("/changes");
    expect(text()).toContain("ยังไม่มีการแก้ไข");
  });

  it("revert creates a revert ChangeRequest through the same review/confirm flow", async () => {
    const preview: ChangePreview = {
      preview_id: "c".repeat(32), path: ".claude/agents/developer.md", kind: "revert", op: "delete", impact: "high",
      confirm_phrase: "developer", base_sha256: "3".repeat(64), proposed_sha256: null,
      diff: { hunks: [], added: 0, removed: 3 },
      warnings: [{ code: "changed_since", error: "ไฟล์ถูกแก้ต่อหลังรายการนี้ — การคืนค่าจะทับการแก้เหล่านั้นด้วย" }],
      errors: [], confirm_token: "1.x", expires_at: "t",
    };
    fetchMock.mockResolvedValueOnce(json(200, { ok: true, data: entries })).mockResolvedValueOnce(json(200, { ok: true, data: preview }));
    await render("/changes");
    const revert = [...container.querySelectorAll(".change-item")][1].querySelector("button")!;
    await act(async () => {
      revert.click();
      await new Promise((r) => setTimeout(r, 0));
    });
    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe("/api/changes/preview");
    expect(JSON.parse(init.body)).toEqual({
      kind: "revert", target: ".claude/agents/developer.md", payload: { change_id: "a".repeat(32) }, base_sha256: "3".repeat(64),
    });
    expect(location).toBe("/changes/review?step=review");
    expect(text()).toContain("ไฟล์ถูกแก้ต่อหลังรายการนี้ — การคืนค่าจะทับการแก้เหล่านั้นด้วย");
    expect(text()).toContain("ย้ายไฟล์ไปสำรอง");
    expect(getPending()?.returnTo).toBe("/changes");
    await act(async () => {
      button("ถัดไป: ยืนยัน")!.click();
    });
    expect(button("คืนค่าไฟล์")).toBeTruthy();
    expect(text()).toContain("ไฟล์นี้จะถูกย้ายไปที่สำรอง");
    expect(text()).toContain("การคืนค่าจะทับการแก้เหล่านั้นด้วย");
  });

  it("shows the server error when a revert preview fails", async () => {
    fetchMock
      .mockResolvedValueOnce(json(200, { ok: true, data: entries }))
      .mockResolvedValueOnce(json(410, { ok: false, code: "backup_missing", error: "ไม่พบไฟล์สำรองของรายการนี้" }));
    await render("/changes");
    await act(async () => {
      button("คืนค่าไฟล์นี้")!.click();
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(text()).toContain("ไม่พบไฟล์สำรองของรายการนี้");
    expect(location).toBe("/changes");
  });

  it("detail page shows the read-only diff", async () => {
    fetchMock.mockResolvedValue(json(200, { ok: true, data: {
      change_id: "a".repeat(32), path: "_bmad/custom/config.toml", op: "modify", kind: "text", at: "2026-11-24T12:00:00Z",
      diff: { added: 1, removed: 1, hunks: [{ old_start: 1, old_lines: 1, new_start: 1, new_lines: 1, lines: [
        { type: "del", old: 1, new: null, text: "x = 1", spans: [[4, 5]] },
        { type: "add", old: null, new: 1, text: "x = 2", spans: [[4, 5]] },
      ] }] },
    } }));
    await render(`/changes/${"a".repeat(32)}`);
    expect(fetchMock.mock.calls[0][0]).toBe(`/api/changes/${"a".repeat(32)}`);
    expect(text()).toContain("โหมดอ่านอย่างเดียว");
    expect(text()).toContain("x = 2");
    expect(button("เขียนไฟล์")).toBeUndefined();
    expect(button("คืนค่าไฟล์")).toBeUndefined();
  });

  it("detail page handles an unknown id", async () => {
    fetchMock.mockResolvedValue(json(404, { ok: false, code: "change_not_found", error: "ไม่พบรายการนี้ในประวัติ" }));
    await render("/changes/zzz");
    expect(text()).toContain("ไม่พบรายการนี้ในประวัติ");
  });

  it("detail page offers retry for non-404 errors and links a revert to the entry it undid", async () => {
    fetchMock
      .mockResolvedValueOnce(json(410, { ok: false, code: "diff_unavailable", error: "ไม่มีข้อมูลพอสำหรับแสดง diff ของรายการนี้" }))
      .mockResolvedValueOnce(json(410, { ok: false, code: "diff_unavailable", error: "ยังไม่ได้" }));
    await render(`/changes/${"a".repeat(32)}`);
    expect(text()).toContain("แสดง diff ของรายการนี้ไม่ได้");
    await act(async () => {
      button("ลองใหม่")!.click();
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);

    act(() => root.unmount());
    root = createRoot(container);
    fetchMock.mockReset();
    fetchMock.mockResolvedValue(json(200, { ok: true, data: entries }));
    await render("/changes");
    expect(container.querySelector(`a[href="/changes/${"a".repeat(32)}"]`)).toBeTruthy();
    expect(text()).toContain("คืนค่าของรายการก่อนหน้า");
  });
});
