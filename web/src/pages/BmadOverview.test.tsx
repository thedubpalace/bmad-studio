import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Inventory } from "../api/types";
import { BmadOverview } from "./BmadOverview";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const base: Inventory = {
  version: "6.12.4",
  skills: [
    { id: "bmad-build", description: "Builds things", override_team: true, override_user: false },
    { id: "bmad-help", description: "", override_team: false, override_user: false },
  ],
  config: { ok: true, agents: [{ id: "bmad-agent-dev", name: "Amelia" }], core: {}, error: null },
  tools: { uv: true, resolve_script: true },
};

function reply(data: Inventory) {
  return new Response(JSON.stringify({ ok: true, data }), { status: 200, headers: { "Content-Type": "application/json" } });
}

describe("BmadOverview", () => {
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

  async function render() {
    await act(async () => {
      root.render(<BmadOverview />);
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }

  it("shows version, counts, tool status and skills", async () => {
    fetchMock.mockResolvedValue(reply(base));
    await render();
    const text = container.textContent ?? "";
    expect(text).toContain("6.12.4");
    expect(text).toContain("2 skill");
    expect(text).toContain("1 agent");
    expect(text).toContain("พบ uv");
    expect(text).toContain("resolve_config.py ทำงานได้");
    expect(text).toContain("มี override (team)");
    expect(container.querySelectorAll(".status svg").length).toBe(2);
  });

  it("shows a local error panel with retry when the script is unavailable, skills still listed", async () => {
    const broken: Inventory = {
      ...base,
      config: { ok: false, agents: [], core: {}, error: { code: "bmad_unavailable", error: "ไม่พบ uv — ติดตั้ง uv แล้วกด ลองใหม่", reason: "uv_missing" } },
      tools: { uv: false, resolve_script: true },
    };
    fetchMock.mockResolvedValueOnce(reply(broken)).mockResolvedValueOnce(reply(base));
    await render();
    const text = container.textContent ?? "";
    expect(text).toContain("ไม่พบ uv — ติดตั้ง uv แล้วกด ลองใหม่");
    expect(text).toContain("bmad-build");
    const retry = [...container.querySelectorAll("button")].find((b) => b.textContent === "ลองใหม่");
    expect(retry).toBeTruthy();
    await act(async () => {
      retry!.click();
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(container.textContent).toContain("resolve_config.py ทำงานได้");
  });
});
