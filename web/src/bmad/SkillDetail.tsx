// /bmad/skills/<id> — "ใช้ใน" (ลิงก์ไปขั้นของ flow) + SKILL.md อ่านอย่างเดียว (REQ-051 b: ไม่มี UI แก้/ลบ)
import { useCallback, useEffect, useId, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";

import { api, ApiError } from "../api/client";
import type { SkillDetail as Detail } from "../api/types";
import { Icon } from "../components/Icon";
import { th } from "../i18n/th";
import { BmadNav } from "./BmadNav";
import { OverrideEditor } from "./OverrideEditor";
import { stageLink } from "./SkillCatalog";

type State = { kind: "loading" } | { kind: "ready"; data: Detail } | { kind: "error"; message: string; notFound: boolean };

export function SkillDetail({ children }: { children?: (data: Detail) => React.ReactNode }) {
  const { id = "" } = useParams();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [showMd, setShowMd] = useState(false);
  const mdId = useId();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "override" ? "override" : "detail";

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      setState({ kind: "ready", data: await api.get<Detail>(`/api/skills/${encodeURIComponent(id)}`) });
    } catch (cause) {
      const error = cause instanceof ApiError ? cause : null;
      // id ที่ไม่ match route (เช่น มี /) = 403 deny-by-default — ผู้ใช้เห็นเป็น "ไม่พบ" เหมือน 404
      setState({ kind: "error", message: error?.message ?? th.skills.loadFailed,
                 notFound: error?.status === 404 || (error?.status === 403 && error.code === "not_allowed") });
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  if (state.kind === "loading") {
    return (
      <>
        <BmadNav />
        <section className="panel glass" aria-busy="true"><p className="muted">{th.bmad.loading}</p></section>
      </>
    );
  }
  if (state.kind === "error") {
    return (
      <>
        <BmadNav />
        <section className="panel surface-solid error-panel" role="alert">
          <p className="error-panel__title"><Icon name="alert" /><span>{state.notFound ? th.skills.notFound : th.skills.loadFailed}</span></p>
          <p>{state.message}</p>
          <Link className="button button--secondary" to="/bmad/skills">{th.skills.back}</Link>
        </section>
      </>
    );
  }
  const { data } = state;
  return (
    <>
      <BmadNav />
      <section className="panel glass skill-detail" aria-labelledby="skill-title">
        <Link className="back-link" to="/bmad/skills">← {th.skills.back}</Link>
        <h2 id="skill-title" className="section-title mono">{data.id}</h2>
        <p className="item__desc skill-detail__desc">{data.description || th.bmad.noDescription}</p>
        {data.customizable ? (
          <div className="segmented" role="tablist" aria-label={th.override.tabsLabel}>
            {(["detail", "override"] as const).map((t) => (
              <button key={t} type="button" role="tab" className="segmented__item" aria-selected={tab === t}
                      aria-pressed={tab === t} onClick={() => setParams(t === "override" ? { tab: t } : {}, { replace: true })}>
                {t === "override" ? th.override.tab : th.override.tabDetail}
              </button>
            ))}
          </div>
        ) : null}
        <p className="item__tags">
          {data.overrideTeam ? <span className="tag tag--warn">{th.bmad.overrideTeam}</span> : null}
          {data.overrideUser ? <span className="tag tag--warn">{th.bmad.overrideUser}</span> : null}
          <span className="tag">{data.group === "veda" ? th.skills.groupVedaShort : th.skills.groupBmadShort}</span>
        </p>
        <h3 className="skill-group__title">{th.skills.usedInTitle}</h3>
        {(data.unreadable ?? []).length ? (
          <p className="form-error" role="status">
            <Icon name="alert" size={16} /> {th.skills.unreadable((data.unreadable ?? []).map((u) => u.flow).join(", "))}
          </p>
        ) : null}
        {(data.usedIn ?? []).length ? (
          <ul className="skill-uses">
            {(data.usedIn ?? []).map((u) => (
              <li key={JSON.stringify([u.kind, u.flow, u.track, u.stage])}>
                <Link to={stageLink(u)} className="mono">{th.skills.useLabel(u, u.track, u.stage)}</Link>
                {!u.enabled ? <span className="tag">{th.skills.stageOff}</span> : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">{data.usageKnown ? th.skills.unusedLong : th.skills.usageUnknown}</p>
        )}
        <div className="skill-detail__md">
          <button type="button" className="button button--secondary" aria-expanded={showMd} aria-controls={showMd ? mdId : undefined}
                  onClick={() => setShowMd((v) => !v)}>
            <Icon name="eye" size={18} /> {showMd ? th.skills.hideMd : th.skills.viewMd}
          </button>
          <p className="hint"><Icon name="lock" size={14} /> {th.skills.mdReadOnly}</p>
          {showMd ? (
            <div id={mdId} className="skill-md" role="region" aria-label={data.skillMdPath}>
              <p className="mono muted">{data.skillMdPath}</p>
              {data.skillMdError ? (
                <p className="form-error"><Icon name="alert" size={16} /> {th.skills.mdError(data.skillMdError)}</p>
              ) : (
                <pre className="mono skill-md__text" tabIndex={0}>{data.skillMd}</pre>
              )}
              {data.skillMdTruncated ? <p className="muted">{th.skills.mdTruncated(data.skillMdLimit)}</p> : null}
            </div>
          ) : null}
        </div>
      </section>
      {data.customizable && tab === "override" ? <OverrideEditor skill={data.id} /> : null}
      {children ? children(data) : null}
    </>
  );
}
