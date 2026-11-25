// การแสดงผลสถานะขั้น — ข้อมูลทั้งหมด (status, skipReason, locked, next) มาจาก server (AD-1); ไฟล์นี้แค่แปลงเป็นป้าย
import type { NextStage, StageRow } from "../api/types";
import { Icon, type IconName } from "../components/Icon";
import { th } from "../i18n/th";

export type StageStatus = StageRow["status"]; // จาก OpenAPI — ไม่นิยามซ้ำ

export const STATUS_ICON: Record<StageStatus, IconName> = {
  done: "check",
  active: "active",
  pending: "pending",
  skipped: "skip",
};

export function statusText(status: string): string {
  return th.flow.status[status] ?? status;
}

export function gateText(gate: string): string {
  return th.flow.gate[gate] ?? gate;
}

/** ข้อความ "ขั้นถัดไป: agent (gate: …)" — ค่าเดียวกับที่ `flow resolve` ตอบ */
export function nextText(next: NextStage): string {
  const agent = next.agent === "human" ? th.flow.gate.human : next.agent;
  return `${next.stage} · ${agent} (${th.flow.gateLabel}: ${gateText(next.gate)})`;
}

export function StatusBadge({ status }: { status: StageStatus }) {
  return (
    <span className={`stage-status stage-status--${status}`}>
      <Icon name={STATUS_ICON[status]} size={18} />
      <span>{statusText(status)}</span>
    </span>
  );
}
