import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setCsrfToken } from "../api/client";
import type { ChangePreview, Diff, DiffLine } from "../api/types";
import { ChangeReview } from "./ChangeReview";
import { clearPending, getPending, setPending } from "./changeStore";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function smallDiff(): Diff {
  return {
    added: 1,
    removed: 1,
    hunks: [
      {
        old_start: 147,
        old_lines: 7,
        new_start: 147,
        new_lines: 7,
        lines: [
          { type: "context", old: 147, new: 147, text: "line 147", spans: [] },
          { type: "del", old: 150, new: null, text: "line 150", spans: [[5, 8]] },
          { type: "add", old: null, new: 150, text: "line 150 edited", spans: [[9, 15]] },
        ],
      },
    ],
  };
}

function bigDiff(n: number): Diff {
  const lines: DiffLine[] = Array.from({ length: n }, (_, i) => ({ type: "add", old: null, new: i + 1, text: `row ${i}`, spans: [] }));
  return { added: n, removed: 0, hunks: [{ old_start: 0, old_lines: 0, new_start: 1, new_lines: n, lines }] };
}

function preview(overrides: Partial<ChangePreview> = {}): ChangePreview {
  return {
    preview_id: "a".repeat(32),
    path: ".claude/agents/developer.md",
    kind: "text",
    op: "modify",
    impact: "high",
    confirm_phrase: "developer",
    base_sha256: "b".repeat(64),
    proposed_sha256: "c".repeat(64),
    diff: smallDiff(),
    warnings: [],
    errors: [],
    confirm_token: "123.sig",
    expires_at: "2026-11-24T12:00:00Z",
    ...overrides,
  };
}

function pend(p: ChangePreview, draft?: string) {
  setPending({
    request: { kind: "text", target: p.path, payload: { text: "x" }, base_sha256: p.base_sha256 },
    preview: p,
    returnTo: "/agents/developer",
    draft,
    outcome: { kind: "idle" },
  });
}

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

let location = "";
function LocationProbe() {
  const loc = useLocation();
  location = loc.pathname + loc.search;
  return null;
}

function mockMedia({ wide = true, reduced = false } = {}) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("min-width") ? wide : query.includes("reduced-motion") ? reduced : false,
    media: query,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }));
}

describe("ChangeReview", () => {
  let container: HTMLDivElement;
  let root: Root;
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
    mockMedia();
    setCsrfToken("csrf-token");
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

  async function render(url = "/changes/review?step=review") {
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={[url]}>
          <Routes>
            <Route path="/changes/review" element={<ChangeReview />} />
            <Route path="*" element={<p>other page</p>} />
          </Routes>
          <LocationProbe />
        </MemoryRouter>,
      );
    });
  }

  const text = () => container.textContent ?? "";
  const button = (label: string) => [...container.querySelectorAll("button")].find((b) => b.textContent?.includes(label));
  async function click(el: Element | undefined) {
    expect(el).toBeTruthy();
    await act(async () => {
      (el as HTMLElement).click();
      await new Promise((r) => setTimeout(r, 0));
    });
  }
  async function type(input: HTMLInputElement, value: string) {
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
      setter.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
  }

  it("config preview shows the effective-after-save panel, hidden-count note and warning keys", async () => {
    pend(preview({
      path: "_bmad/custom/config.toml", kind: "config", confirm_phrase: "config.toml",
      warnings: [{ code: "prompt_text_change", error: "ข้อความเปลี่ยน", keys: ["core.document_output_language"] }],
      view: { changed: [{ key: "core.document_output_language", value: "Thai" }, { key: "core.old", removed: true }],
              changedTotal: 3, beforeAvailable: true },
    }));
    await render();
    const panel = container.querySelector(".effective-panel")!;
    expect(panel.textContent).toContain("ค่าที่ใช้จริงเปลี่ยน 3 คีย์");
    expect(panel.textContent).toContain("Thai");
    expect(panel.textContent).toContain("(ถูกลบ)");
    expect(panel.textContent).toContain("และอีก 1 คีย์");
    expect(panel.textContent).toContain("personal override");
    expect(Array.from(container.querySelectorAll(".issue-keys__key")).map((k) => k.textContent)).toEqual(["core.document_output_language"]);
  });

  it("effective panel says the before state is unknown instead of listing everything", async () => {
    pend(preview({ view: { changed: [], changedTotal: 0, beforeAvailable: false } }));
    await render();
    expect(container.querySelector(".effective-panel")?.textContent).toContain("อ่านค่าที่ใช้อยู่ตอนนี้ไม่ได้");
  });

  it("shows the empty state without a pending change", async () => {
    await render();
    expect(text()).toContain("ไม่มีการเปลี่ยนแปลงที่รอตรวจ");
  });

  it("review step shows path, stats, impact badge, gutter signs and word highlight", async () => {
    pend(preview());
    await render();
    expect(text()).toContain("ตรวจการเปลี่ยนแปลงก่อนบันทึก");
    expect(container.querySelector(".path")?.textContent).toBe(".claude/agents/developer.md");
    expect(text()).toContain("+1 −1 บรรทัด");
    expect(text()).toContain("ผลสูง: มีผลต่อการสั่งงาน agent");
    expect(text()).toContain("… 146 บรรทัดที่ไม่เปลี่ยน");
    expect(container.querySelectorAll("mark.diff__word--add")[0]?.textContent).toBe("edited");
    expect(text()).toContain("เพิ่ม"); // ป้ายสำหรับโปรแกรมอ่านจอ ไม่ใช่สีอย่างเดียว
    expect(document.activeElement?.textContent).toBe("ตรวจการเปลี่ยนแปลงก่อนบันทึก");
  });

  it("uses side-by-side on wide screens and unified only on narrow (360px) screens", async () => {
    pend(preview());
    await render();
    expect(container.querySelector(".diff--split")).toBeTruthy();
    await click(button("รวม"));
    expect(container.querySelector(".diff--unified")).toBeTruthy();

    act(() => root.unmount());
    root = createRoot(container);
    mockMedia({ wide: false });
    pend(preview());
    await render();
    expect(container.querySelector(".diff--unified")).toBeTruthy();
    expect(button("เทียบสองฝั่ง")?.getAttribute("aria-disabled")).toBe("true");
    await click(button("เทียบสองฝั่ง"));
    expect(container.querySelector(".diff--split")).toBeNull();
  });

  it("wrap toggle switches wrapping", async () => {
    pend(preview());
    await render();
    const box = container.querySelector<HTMLInputElement>(".check input")!;
    await click(box);
    expect(container.querySelector(".diff--wrap")).toBeTruthy();
  });

  it("blocks the next step with a visible reason when there are errors", async () => {
    pend(preview({ errors: [{ code: "eol_only_change", error: "การแก้นี้เปลี่ยนแค่ line ending/BOM ของทั้งไฟล์" }] }));
    await render();
    expect(text()).toContain("diff นี้เปลี่ยน line ending ทั้งไฟล์ — ไม่ใช่ที่ตั้งใจ");
    const next = button("ถัดไป: ยืนยัน")!;
    expect(next.getAttribute("aria-disabled")).toBe("true");
    const reason = document.getElementById(next.getAttribute("aria-describedby")!);
    expect(reason?.textContent).toContain("ต้องแก้ก่อนบันทึก");
    await click(next);
    expect(location).toBe("/changes/review?step=review");
    // แม้เปิด URL step=confirm ตรง ๆ ก็ยังอยู่ขั้นตรวจ
    act(() => root.unmount());
    root = createRoot(container);
    await render("/changes/review?step=confirm");
    expect(text()).toContain("ตรวจการเปลี่ยนแปลงก่อนบันทึก");
  });

  it("high impact: write is blocked until the phrase is typed, then commits once with the phrase", async () => {
    pend(preview());
    await render();
    await click(button("ถัดไป: ยืนยัน"));
    expect(location).toBe("/changes/review?step=confirm");
    expect(text()).toContain("ยืนยันการเขียนไฟล์");
    expect(document.activeElement?.textContent).toBe("ยืนยันการเขียนไฟล์");
    expect(text()).toContain("จะสำรองไฟล์เดิมไว้ย้อนกลับได้");
    expect(text()).toContain("หรือกดค้างที่ปุ่ม");

    await click(button("เขียนไฟล์"));
    expect(fetchMock).not.toHaveBeenCalled();

    const input = container.querySelector<HTMLInputElement>(".confirm-field input")!;
    await type(input, "Developer");
    await click(button("เขียนไฟล์"));
    expect(fetchMock).not.toHaveBeenCalled();

    let resolve!: (r: Response) => void;
    fetchMock.mockReturnValue(new Promise<Response>((r) => (resolve = r)));
    await type(input, "developer");
    await click(button("เขียนไฟล์"));
    await click(button("กำลังเขียนไฟล์"));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/changes/commit");
    expect(JSON.parse(init.body)).toEqual({ preview_id: "a".repeat(32), confirm_token: "123.sig", typed_confirmation: "developer" });
    expect(init.headers["X-CSRF-Token"]).toBe("csrf-token");

    await act(async () => {
      resolve(json(200, { ok: true, data: { change_id: "d".repeat(32), path: ".claude/agents/developer.md", op: "modify", sha256: "e".repeat(64), backup: ".runtime/backups/ddd", at: "2026-11-24T12:00:00Z", replayed: false } }));
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(text()).toContain("บันทึกแล้ว · สำรองไว้ที่ .runtime/backups/ddd · ยังไม่ commit");
    expect(container.querySelector('a[href="/changes"]')).toBeTruthy();
    expect(getPending()?.outcome.kind).toBe("done");
  });

  it("browser back after success does not write again", async () => {
    pend(preview({ impact: "normal", confirm_phrase: null, path: "_bmad/custom/config.toml" }));
    await render("/changes/review?step=confirm");
    fetchMock.mockResolvedValue(json(200, { ok: true, data: { change_id: "d".repeat(32), path: "_bmad/custom/config.toml", op: "modify", sha256: "e".repeat(64), backup: ".runtime/backups/x", at: "t", replayed: false } }));
    await click(button("เขียนไฟล์"));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    act(() => root.unmount());
    root = createRoot(container);
    await render("/changes/review?step=review");
    expect(text()).toContain("บันทึกแล้ว");
    expect(button("ถัดไป: ยืนยัน")).toBeUndefined();
    act(() => root.unmount());
    root = createRoot(container);
    await render("/changes/review?step=confirm");
    expect(button("เขียนไฟล์")).toBeUndefined();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("normal impact writes with a single click and no typed confirmation", async () => {
    pend(preview({ impact: "normal", confirm_phrase: null, path: "_bmad/custom/config.toml" }));
    await render("/changes/review?step=confirm");
    expect(container.querySelector(".confirm-field")).toBeNull();
    fetchMock.mockResolvedValue(json(200, { ok: true, data: { change_id: "d".repeat(32), path: "p", op: "modify", sha256: "e".repeat(64), backup: "b", at: "t", replayed: false } }));
    await click(button("เขียนไฟล์"));
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).typed_confirmation).toBeNull();
  });

  it("reduced motion: no hold ring, typed confirmation only", async () => {
    mockMedia({ reduced: true });
    pend(preview());
    await render("/changes/review?step=confirm");
    expect(container.querySelector(".hold-button")).toBeNull();
    expect(text()).not.toContain("หรือกดค้าง");
    expect(text()).toContain("พิมพ์ข้อความยืนยันให้ตรงก่อน");
  });

  it("hold for 800ms confirms without typing", async () => {
    vi.useFakeTimers({ toFake: ["requestAnimationFrame", "cancelAnimationFrame", "performance"] });
    try {
      pend(preview());
      await render("/changes/review?step=confirm");
      fetchMock.mockResolvedValue(json(200, { ok: true, data: { change_id: "d".repeat(32), path: "p", op: "modify", sha256: "e".repeat(64), backup: "b", at: "t", replayed: false } }));
      const hold = container.querySelector<HTMLButtonElement>(".hold-button")!;
      await act(async () => {
        hold.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, button: 0 }));
      });
      await act(async () => {
        vi.advanceTimersByTime(400);
      });
      await act(async () => {
        hold.dispatchEvent(new MouseEvent("pointerup", { bubbles: true, button: 0 }));
      });
      await act(async () => {
        vi.advanceTimersByTime(1000);
      });
      expect(fetchMock).not.toHaveBeenCalled();
      await act(async () => {
        hold.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, button: 0 }));
      });
      await act(async () => {
        vi.advanceTimersByTime(900);
      });
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(JSON.parse(fetchMock.mock.calls[0][1].body).typed_confirmation).toBe("developer");
    } finally {
      vi.useRealTimers();
    }
  });

  it("409 shows the conflict state with reload and copy draft, never an overwrite button", async () => {
    pend(preview({ impact: "normal", confirm_phrase: null }), "my draft");
    await render("/changes/review?step=confirm");
    fetchMock.mockResolvedValue(json(409, { ok: false, code: "conflict", error: "ไฟล์ถูกแก้จากภายนอกระหว่างที่คุณแก้ โหลดใหม่ก่อน" }));
    await click(button("เขียนไฟล์"));
    expect(text()).toContain("ไฟล์ถูกแก้จากภายนอกระหว่างที่คุณแก้");
    expect(button("เขียนไฟล์")).toBeUndefined();
    expect([...container.querySelectorAll("button")].some((b) => b.textContent?.includes("เขียนทับ"))).toBe(false);
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    await click(button("คัดลอก draft"));
    expect(writeText).toHaveBeenCalledWith("my draft");
    await click(button("โหลดใหม่"));
    expect(location).toBe("/agents/developer");
    expect(getPending()).toBeNull();
  });

  it("503 says nothing was changed and allows retry", async () => {
    pend(preview({ impact: "normal", confirm_phrase: null }));
    await render("/changes/review?step=confirm");
    fetchMock.mockResolvedValueOnce(json(503, { ok: false, code: "audit_unavailable", error: "ระบบบันทึกไม่ได้ ไม่มีการเปลี่ยนแปลง" }));
    await click(button("เขียนไฟล์"));
    expect(text()).toContain("เขียนไฟล์ไม่สำเร็จ");
    expect(text()).toContain("ไม่มีอะไรถูกเปลี่ยน");
    expect(button("เขียนไฟล์")).toBeTruthy();
  });

  it("expired token offers a fresh preview from the same request", async () => {
    pend(preview({ impact: "normal", confirm_phrase: null }));
    await render("/changes/review?step=confirm");
    fetchMock
      .mockResolvedValueOnce(json(403, { ok: false, code: "confirm_expired", error: "หมดอายุ" }))
      .mockResolvedValueOnce(json(200, { ok: true, data: preview({ preview_id: "f".repeat(32), impact: "normal", confirm_phrase: null }) }));
    await click(button("เขียนไฟล์"));
    expect(text()).toContain("การยืนยันหมดอายุ");
    await click(button("เปิดตัวอย่างใหม่"));
    expect(fetchMock.mock.calls[1][0]).toBe("/api/changes/preview");
    expect(getPending()?.preview.preview_id).toBe("f".repeat(32));
    expect(location).toBe("/changes/review?step=review");
  });

  it("ux_ack_stale asks again: names the added projects and reopens the preview (retro epic-4 A3)", async () => {
    pend(preview({ impact: "normal", confirm_phrase: null }));
    await render("/changes/review?step=confirm");
    fetchMock
      .mockResolvedValueOnce(json(409, { ok: false, code: "ux_ack_stale", error: "เปลี่ยน",
        details: { projects: ["late-ui", "<b>x</b>", "*unchecked*"] } }))
      .mockResolvedValueOnce(json(200, { ok: true, data: preview({ preview_id: "e".repeat(32), impact: "normal", confirm_phrase: null }) }));
    await click(button("เขียนไฟล์"));
    expect(text()).toContain("รายชื่อโปรเจกต์ที่จะถูกปิดขั้นออกแบบ UI เปลี่ยนไปหลังตัวอย่าง");
    expect(text()).toContain("โปรเจกต์ที่เพิ่มมา: late-ui, <b>x</b>");
    expect(text()).not.toContain("*unchecked*");
    expect(container.querySelector("b")).toBeNull();
    expect(text()).not.toContain("ไฟล์ถูกแก้จากภายนอก");
    await click(button("เปิดตัวอย่างใหม่"));
    expect(fetchMock.mock.calls[1][0]).toBe("/api/changes/preview");
    expect(getPending()?.preview.preview_id).toBe("e".repeat(32));
    expect(location).toBe("/changes/review?step=review");
  });

  // REQ-052 "< 1 วินาที" เป็นเวลาในเบราว์เซอร์จริง — jsdom วัดไม่ได้ จึงตรวจแบบ deterministic:
  // จำนวนแถวที่สร้างจริงมีขอบเขต (virtualize) ไม่ใช่นาฬิกา (review 2-3 B1)
  it("virtualizes diffs over 2,000 lines and renders smaller diffs in full", async () => {
    pend(preview({ diff: bigDiff(5000) }));
    await render();
    expect(container.querySelectorAll(".diff__row").length).toBeLessThan(200);
    expect(container.querySelector<HTMLInputElement>(".check input")?.disabled).toBe(true);
    expect(text()).toContain("ปิดตัดบรรทัดเมื่อ diff ยาวเกิน 2,000 บรรทัด");

    act(() => root.unmount());
    root = createRoot(container);
    pend(preview({ diff: bigDiff(1500) }));
    await render();
    expect(container.querySelectorAll(".diff__row").length).toBe(1500);
    expect(container.querySelector(".diff--virtual")).toBeNull();
  });

  it("a truncated (too large) diff shows a summary and blocks the next step", async () => {
    pend(preview({
      diff: { hunks: [], added: 1048566, removed: 200, truncated: true },
      errors: [{ code: "diff_too_large", error: "การเปลี่ยนแปลงใหญ่เกินกว่าจะแสดงให้ตรวจได้ — แบ่งแก้เป็นส่วนเล็กลง" }],
    }));
    await render();
    expect(text()).toContain("diff นี้ใหญ่เกินกว่าจะแสดงรายบรรทัด");
    expect(text()).toContain("+1048566 −200 บรรทัด");
    expect(container.querySelectorAll(".diff__row").length).toBe(0);
    expect(button("ถัดไป: ยืนยัน")?.getAttribute("aria-disabled")).toBe("true");
  });

  it.each([
    [{ code: "audit_unavailable" }, "failed"],
    [{ code: "write_failed" }, "failed"],
    [{ code: "write_unavailable" }, "failed"],
    [null, "unknown"], // 503 จาก proxy/uvicorn ที่ไม่มี envelope ของแอป
    [{ code: "service_unavailable" }, "unknown"],
  ])("503 %o is %s", async (body, kind) => {
    pend(preview({ impact: "normal", confirm_phrase: null }));
    await render("/changes/review?step=confirm");
    fetchMock.mockResolvedValueOnce(
      body
        ? json(503, { ok: false, ...body, error: "x" })
        : new Response("<html>503 Service Unavailable</html>", { status: 503, headers: { "Content-Type": "text/html" } }),
    );
    await click(button("เขียนไฟล์"));
    expect(getPending()?.outcome.kind).toBe(kind);
    if (kind === "unknown") expect(text()).not.toContain("ไม่มีอะไรถูกเปลี่ยน");
    else expect(text()).toContain("ไม่มีอะไรถูกเปลี่ยน");
  });

  it("hold does not start with a non-primary button and a lost pointer cancels it", async () => {
    vi.useFakeTimers({ toFake: ["requestAnimationFrame", "cancelAnimationFrame", "performance"] });
    try {
      pend(preview());
      await render("/changes/review?step=confirm");
      const hold = container.querySelector<HTMLButtonElement>(".hold-button")!;
      await act(async () => {
        hold.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, button: 2 }));
      });
      await act(async () => {
        vi.advanceTimersByTime(1000);
      });
      expect(fetchMock).not.toHaveBeenCalled();
      await act(async () => {
        hold.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, button: 0 }));
      });
      await act(async () => {
        vi.advanceTimersByTime(300);
        hold.dispatchEvent(new MouseEvent("pointercancel", { bubbles: true }));
      });
      await act(async () => {
        vi.advanceTimersByTime(1000);
      });
      expect(fetchMock).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("Enter in the confirmation field writes once the phrase matches", async () => {
    pend(preview());
    await render("/changes/review?step=confirm");
    fetchMock.mockResolvedValue(json(200, { ok: true, data: { change_id: "d".repeat(32), path: "p", op: "modify", sha256: "e".repeat(64), backup: "b", at: "t", replayed: false } }));
    const input = container.querySelector<HTMLInputElement>(".confirm-field input")!;
    await type(input, "developer");
    await act(async () => {
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("high impact without a server phrase cannot be written", async () => {
    pend(preview({ confirm_phrase: null }));
    await render("/changes/review?step=confirm");
    expect(text()).toContain("ไม่มีชื่อสำหรับยืนยัน");
    expect(container.querySelector(".confirm-field")).toBeNull();
    await click(button("เขียนไฟล์"));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("retry after 503 sends a second commit and succeeds", async () => {
    pend(preview({ impact: "normal", confirm_phrase: null }));
    await render("/changes/review?step=confirm");
    fetchMock
      .mockResolvedValueOnce(json(503, { ok: false, code: "write_failed", error: "เขียนไฟล์ไม่ได้ ไม่มีอะไรถูกเปลี่ยน" }))
      .mockResolvedValueOnce(json(200, { ok: true, data: { change_id: "d".repeat(32), path: "p", op: "modify", sha256: "e".repeat(64), backup: "bk", at: "t", replayed: false } }));
    await click(button("เขียนไฟล์"));
    await click(button("เขียนไฟล์"));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(text()).toContain("สำรองไว้ที่ bk");
  });

  it.each([
    [500, "restore_failed"],
    [0, "network"],
  ])("status %s never claims nothing changed", async (status, code) => {
    pend(preview({ impact: "normal", confirm_phrase: null }));
    await render("/changes/review?step=confirm");
    if (status === 0) fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    else fetchMock.mockResolvedValueOnce(json(status, { ok: false, code, error: "บันทึกประวัติไม่ได้และคืนไฟล์เดิมไม่สำเร็จ" }));
    await click(button("เขียนไฟล์"));
    expect(text()).toContain("ไม่แน่ใจว่าไฟล์ถูกเขียนแล้วหรือยัง");
    expect(text()).not.toContain("ไม่มีอะไรถูกเปลี่ยน");
    expect(button("เขียนไฟล์")).toBeUndefined();
  });

  it("confirm_invalid also offers a fresh preview; a failed refresh shows its error", async () => {
    pend(preview({ impact: "normal", confirm_phrase: null }));
    await render("/changes/review?step=confirm");
    fetchMock
      .mockResolvedValueOnce(json(403, { ok: false, code: "confirm_invalid", error: "ไม่ถูกต้อง" }))
      .mockResolvedValueOnce(json(409, { ok: false, code: "conflict", error: "ไฟล์ถูกแก้จากภายนอกระหว่างที่คุณแก้ โหลดใหม่ก่อน" }));
    await click(button("เขียนไฟล์"));
    expect(text()).toContain("การยืนยันหมดอายุ");
    await click(button("เปิดตัวอย่างใหม่"));
    expect(text()).toContain("โหลดใหม่ก่อน");
    expect(button("เปิดตัวอย่างใหม่")).toBeTruthy();
  });

  it("a late commit result does not overwrite a newer pending change", async () => {
    pend(preview({ impact: "normal", confirm_phrase: null }));
    await render("/changes/review?step=confirm");
    let resolve!: (r: Response) => void;
    fetchMock.mockReturnValue(new Promise<Response>((r) => (resolve = r)));
    await click(button("เขียนไฟล์"));
    act(() => pend(preview({ preview_id: "9".repeat(32), impact: "normal", confirm_phrase: null })));
    await act(async () => {
      resolve(json(200, { ok: true, data: { change_id: "d".repeat(32), path: "p", op: "modify", sha256: "e".repeat(64), backup: "b", at: "t", replayed: false } }));
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(getPending()?.preview.preview_id).toBe("9".repeat(32));
    expect(getPending()?.outcome.kind).toBe("idle");
  });

  it("shows warnings without blocking, and end-of-file newline labels", async () => {
    pend(preview({ warnings: [{ code: "outside_surface", error: "key นี้ไม่อยู่ใน customize surface" }], diff: { ...smallDiff(), old_eof_newline: true, new_eof_newline: false } }));
    await render();
    expect(text()).toContain("คำเตือน");
    expect(text()).toContain("key นี้ไม่อยู่ใน customize surface");
    expect(button("ถัดไป: ยืนยัน")?.getAttribute("aria-disabled")).toBe("false");
    expect(text()).toContain("ไม่มี newline ท้ายไฟล์");
    act(() => root.unmount());
    root = createRoot(container);
    pend(preview({ diff: { ...smallDiff(), old_eof_newline: false, new_eof_newline: true } }));
    await render();
    expect(text()).toContain("เพิ่ม newline ท้ายไฟล์");
  });

  it("virtualized diff renders later rows after scrolling", async () => {
    pend(preview({ diff: bigDiff(5000) }));
    await render();
    const scroller = container.querySelector<HTMLDivElement>(".diff__scroll")!;
    expect(text()).not.toContain("row 3000");
    await act(async () => {
      scroller.scrollTop = 3000 * 21;
      scroller.dispatchEvent(new Event("scroll"));
    });
    expect(text()).toContain("row 3000");
  });
});
