import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ConfigView } from "../api/types";
import { clearPending, getPending } from "../changes/changeStore";
import { clearConfigDrafts, ConfigEditor } from "./ConfigEditor";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const has = (base: boolean, team: boolean, user: boolean, effective: boolean) => ({ base, team, user, effective });

const view: ConfigView = {
  layers: {
    team: { exists: true, text: '[core]\ndocument_output_language = "English"\n', sha256: "a".repeat(64), issue: null },
    user: { exists: false, text: "", sha256: null, issue: null },
  },
  targets: { team: "_bmad/custom/config.toml", user: "_bmad/custom/config.user.toml" },
  rows: [
    { key: "core.document_output_language", id: '["core","document_output_language"]', path: ["core", "document_output_language"], base: "Thai", team: "English",
      user: null, effective: "English", has: has(true, true, false, true) },
    { key: "core.max_rounds", id: '["core","max_rounds"]', path: ["core", "max_rounds"], base: 2, team: null, user: null, effective: 2,
      has: has(true, false, false, true) },
    { key: "agents.bmad-agent-dev.menu", id: '["agents","bmad-agent-dev","menu"]', path: ["agents", "bmad-agent-dev", "menu"], base: [{ code: "a" }], team: null,
      user: null, effective: [{ code: "a" }], has: has(true, false, false, true) },
  ],
  baseAvailable: true,
  effectiveAvailable: true,
  issues: [],
};

const preview = {
  preview_id: "p1", path: "_bmad/custom/config.toml", kind: "config", op: "modify", impact: "high",
  confirm_phrase: "config.toml", base_sha256: "a".repeat(64), proposed_sha256: "b".repeat(64),
  diff: { hunks: [], added: 1, removed: 1, truncated: false, old_eof_newline: true, new_eof_newline: true },
  warnings: [], errors: [], confirm_token: "t", expires_at: "x", view: null,
};

function json(data: unknown) {
  return new Response(JSON.stringify({ ok: true, data }), { status: 200, headers: { "Content-Type": "application/json" } });
}

const button = (root: HTMLElement, text: string, prefix = false) =>
  Array.from(root.querySelectorAll("button")).find((b) => (prefix ? b.textContent?.startsWith(text) : b.textContent === text))!;

describe("ConfigEditor", () => {
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

  async function render() {
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={["/bmad/config"]}>
          <Routes>
            <Route path="/bmad/config" element={<ConfigEditor />} />
            <Route path="/changes/review" element={<p id="review-page">review</p>} />
          </Routes>
        </MemoryRouter>,
      );
    });
    await flush();
  }

  function type(input: HTMLInputElement | HTMLTextAreaElement, value: string) {
    const proto = input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value")!.set!;
    act(() => {
      setter.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
  }

  const inputs = () => container.querySelectorAll<HTMLInputElement>(".config-row input");

  it("shows base locked, override and effective per key; nested values only in text mode", async () => {
    fetchMock.mockResolvedValueOnce(json(view));
    await render();
    const rows = container.querySelectorAll(".config-row");
    expect(rows).toHaveLength(3);
    expect(rows[0].textContent).toContain("จาก installer — แก้ไม่ได้");
    expect(rows[0].querySelector(".config-cell--effective")?.textContent).toContain("English");
    expect((rows[0].querySelector("input") as HTMLInputElement).value).toBe("English");
    expect(rows[2].querySelector("input")).toBeNull(); // array ของ table = โหมดข้อความ
    expect(container.textContent).not.toMatch(/_bmad\/config\.toml/); // ไม่มีทางแก้ไฟล์ของ installer
  });

  it("form edit sends set/unset to kind config with base sha and opens review", async () => {
    fetchMock.mockResolvedValueOnce(json(view)).mockResolvedValueOnce(json(preview));
    await render();
    type(inputs()[1], "5");
    act(() => button(container, "ลบ override").click());
    await act(async () => button(container, "ตรวจสอบและดูการเปลี่ยนแปลง").click());
    await flush();
    const body = JSON.parse(fetchMock.mock.calls[1][1].body);
    expect(body).toEqual({
      kind: "config", target: "_bmad/custom/config.toml", base_sha256: "a".repeat(64),
      payload: { set: [{ path: ["core", "max_rounds"], value: 5 }], unset: [["core", "document_output_language"]] },
    });
    expect(getPending()?.returnTo).toBe("/bmad/config");
    expect(container.querySelector("#review-page")).not.toBeNull();
  });

  it("text mode sends payload.text for the personal layer (create)", async () => {
    fetchMock.mockResolvedValueOnce(json(view)).mockResolvedValueOnce(json(preview));
    await render();
    act(() => button(container, "personal", true).click());
    act(() => button(container, "แก้เป็นข้อความ TOML").click());
    type(container.querySelector("textarea")!, '[core]\nuser_name = "me"\n');
    await act(async () => button(container, "ตรวจสอบและดูการเปลี่ยนแปลง").click());
    await flush();
    const body = JSON.parse(fetchMock.mock.calls[1][1].body);
    expect(body).toEqual({ kind: "config", target: "_bmad/custom/config.user.toml", base_sha256: null,
                           payload: { text: '[core]\nuser_name = "me"\n' } });
  });

  it("does not submit without changes and keeps the draft per layer after coming back", async () => {
    fetchMock.mockResolvedValueOnce(json(view));
    await render();
    await act(async () => button(container, "ตรวจสอบและดูการเปลี่ยนแปลง").click());
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(container.textContent).toContain("ยังไม่ได้แก้ค่าใด");
    type(inputs()[0], "Lao");
    act(() => button(container, "personal", true).click());
    expect(inputs()[0].value).toBe("");
    act(() => button(container, "team", true).click());
    expect(inputs()[0].value).toBe("Lao");
    act(() => root.unmount());
    root = createRoot(container);
    fetchMock.mockResolvedValueOnce(json(view));
    await render();
    expect(inputs()[0].value).toBe("Lao");
  });

  it("drops a stored draft when the file changed on disk", async () => {
    fetchMock.mockResolvedValueOnce(json(view));
    await render();
    type(inputs()[0], "Lao");
    act(() => root.unmount());
    root = createRoot(container);
    const changed = { ...view, layers: { ...view.layers, team: { ...view.layers.team, sha256: "c".repeat(64) } } };
    fetchMock.mockResolvedValueOnce(json(changed));
    await render();
    expect(inputs()[0].value).toBe("English");
  });

  it("boolean rows send a real boolean; half-typed numbers are refused, decimals can be typed", async () => {
    const withBool: ConfigView = {
      ...view,
      rows: [...view.rows, { key: "core.strict", id: '["core","strict"]', path: ["core", "strict"], base: true, team: true,
                             user: null, effective: true, has: has(true, true, false, true) }],
    };
    fetchMock.mockResolvedValueOnce(json(withBool)).mockResolvedValueOnce(json(preview));
    await render();
    const select = container.querySelector<HTMLSelectElement>(".config-row select")!;
    act(() => {
      select.value = "false";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    type(inputs()[1], "1.");
    expect(inputs()[1].value).toBe("1."); // ไม่เด้งกลับเป็น 1 ระหว่างพิมพ์
    type(inputs()[1], "1e");
    await act(async () => button(container, "ตรวจสอบและดูการเปลี่ยนแปลง").click());
    expect(container.textContent).toContain("ต้องเป็นตัวเลข");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    type(inputs()[1], "1.5");
    await act(async () => button(container, "ตรวจสอบและดูการเปลี่ยนแปลง").click());
    await flush();
    const body = JSON.parse(fetchMock.mock.calls[1][1].body);
    expect(body.payload.set).toEqual([{ path: ["core", "strict"], value: false }, { path: ["core", "max_rounds"], value: 1.5 }]);
  });

  it("does not drop form edits silently when switching to text mode", async () => {
    fetchMock.mockResolvedValueOnce(json(view));
    await render();
    type(inputs()[0], "Lao");
    act(() => button(container, "แก้เป็นข้อความ TOML").click());
    expect(container.querySelector("textarea")).toBeNull();
    expect(container.textContent).toContain("ก่อนสลับวิธีแก้");
    act(() => button(container, "ทิ้งการแก้").click());
    act(() => button(container, "แก้เป็นข้อความ TOML").click());
    expect(container.querySelector("textarea")?.value).toBe(view.layers.team.text);
  });

  it("a layer that BMAD cannot read opens in text mode and the form is unavailable", async () => {
    const broken: ConfigView = {
      ...view,
      layers: { ...view.layers, team: { ...view.layers.team, issue: { code: "toml_bom", error: "BOM" } } },
    };
    fetchMock.mockResolvedValueOnce(json(broken));
    await render();
    expect(container.querySelector("textarea")).not.toBeNull();
    expect(button(container, "ฟอร์ม").getAttribute("aria-disabled")).toBe("true");
    act(() => button(container, "ฟอร์ม").click());
    expect(container.querySelector("textarea")).not.toBeNull();
  });
});
