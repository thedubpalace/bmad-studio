// หน้า /p/<name>/flow — แก้ flow ของโปรเจกต์ (story 4-2): ข้อมูลและการตรวจทั้งหมดมาจาก /api/flows/<name> (4-1)
import { useMemo } from "react";
import { useParams } from "react-router";

import { api } from "../api/client";
import type { FlowDraftCheck, FlowEditor as EditorView } from "../api/types";
import { th } from "../i18n/th";
import { FlowEditor, type FlowEditorSource } from "./FlowEditor";

export function projectSource(name: string): FlowEditorSource {
  const base = `/api/flows/${encodeURIComponent(name)}`;
  return {
    key: `project:${name}`,
    load: () => api.get<EditorView>(base),
    validate: (draft) => api.post<FlowDraftCheck>(`${base}/validate`, { flow: draft }),
    request: (draft, acknowledged, baseSha, target) => ({
      kind: "flow",
      target, // จาก API (view.target) — client ไม่ประกอบ path เอง
      payload: { flow: draft, acknowledged },
      base_sha256: baseSha,
    }),
    returnTo: `/p/${encodeURIComponent(name)}/flow`,
    title: th.flowEditor.title(name),
    backTo: { href: `/p/${encodeURIComponent(name)}`, label: th.flowEditor.back },
  };
}

export function ProjectFlowEditorPage() {
  const { name = "" } = useParams();
  const source = useMemo(() => projectSource(name), [name]);
  return <FlowEditor key={source.key} source={source} />;
}
