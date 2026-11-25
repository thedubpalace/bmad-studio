// หน้า /flow/default — แก้ flow เริ่มต้น (story 4-3): editor ตัวเดียวกับต่อโปรเจกต์ + คำเตือนผลกระทบ
// การตรวจข้ามโปรเจกต์ทำที่ server (POST /api/flow-default/validate และ preview kind flow-default)
import { api } from "../api/client";
import type { FlowDraftCheck, FlowEditor as EditorView } from "../api/types";
import { Icon } from "../components/Icon";
import { th } from "../i18n/th";
import { FlowEditor, type FlowEditorSource } from "./FlowEditor";

export const defaultSource: FlowEditorSource = {
  key: "default",
  load: () => api.get<EditorView>("/api/flow-default"),
  validate: (draft) => api.post<FlowDraftCheck>("/api/flow-default/validate", { flow: draft }),
  request: (draft, _acknowledged, baseSha, target) => ({
    kind: "flow-default",
    target, // จาก API (view.target)
    payload: { flow: draft },
    base_sha256: baseSha,
  }),
  returnTo: "/flow/default",
  title: th.defaultFlow.title,
  backTo: { href: "/", label: th.defaultFlow.back },
  sourceLabel: (n, view) => th.defaultFlow.source(n, view.exists),
  notice: (view) => (
    <>
      <p className="banner banner--warn" role="note">
        <Icon name="alert" size={18} /> <span>{th.defaultFlow.impact}</span>
      </p>
      {!view.exists ? <p className="muted">{th.defaultFlow.noFile}</p> : null}
    </>
  ),
};

export function DefaultFlowEditorPage() {
  return <FlowEditor source={defaultSource} />;
}
