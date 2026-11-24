import { describe, expect, it } from "vitest";

import { ApiError } from "../api/client";
import { safeNext } from "../pages/LoginPage";
import { describeLoginError, formatCountdown } from "./loginErrors";

describe("login error messages (UX-DR3)", () => {
  it("401 shows one generic message", () => {
    expect(describeLoginError(new ApiError(401, "invalid_credentials", "x")).message).toBe(
      "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง",
    );
  });

  it("429 counts down from Retry-After", () => {
    const failure = describeLoginError(new ApiError(429, "rate_limited", "x", null, 125));
    expect(failure.retryAfter).toBe(125);
    expect(failure.message).toBe("ลองผิดหลายครั้งเกินไป ลองใหม่ได้ในอีก 02:05");
  });

  it("503 audit shows that nothing changed", () => {
    expect(describeLoginError(new ApiError(503, "audit_unavailable", "x")).message).toBe(
      "ระบบบันทึกไม่ได้ ไม่มีการเปลี่ยนแปลง",
    );
  });

  it("formats mm:ss", () => {
    expect(formatCountdown(0)).toBe("00:00");
    expect(formatCountdown(59.2)).toBe("01:00");
    expect(formatCountdown(900)).toBe("15:00");
  });
});

describe("safeNext", () => {
  it("only allows in-app paths", () => {
    expect(safeNext("/agents/x")).toBe("/agents/x");
    expect(safeNext("//evil.example")).toBe("/");
    expect(safeNext("https://evil.example")).toBe("/");
    expect(safeNext("/\\evil.example")).toBe("/");
    expect(safeNext("/login?next=/x")).toBe("/");
    expect(safeNext(null)).toBe("/");
  });
});
