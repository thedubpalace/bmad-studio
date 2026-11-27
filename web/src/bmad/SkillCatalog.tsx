// /bmad/skills — catalog แบบรายการ (ไม่ใช่ card grid) จัดกลุ่ม bmad-* / skill อื่นของ Veda (REQ-051, UX-DR9)
// ทุกค่ามาจาก server (AD-1): override, ที่ใช้ใน flow, skill ที่ถูกอ้างแต่ไม่มีโฟลเดอร์
import { useCallback, useEffect, useId, useMemo, useState } from "react";
import { Link } from "react-router";

import { api, ApiError } from "../api/client";
import type { MissingSkill, SkillName, SkillReferences, SkillUse } from "../api/types";
import { Icon } from "../components/Icon";
import { th } from "../i18n/th";
import { BmadNav } from "./BmadNav";

type Filter = "all" | "override" | "unused" | "missing";
type State = { kind: "loading" } | { kind: "ready"; skills: SkillName[]; refs: SkillReferences } | { kind: "error"; message: string };

/** ลิงก์ไปขั้นของ flow ใน editor (default หรือโปรเจกต์) */
export function stageLink(use: SkillUse): string {
  const q = `?track=${encodeURIComponent(use.track)}&stage=${encodeURIComponent(use.stage)}`;
  return use.kind === "default" ? `/flow/default${q}` : `/p/${encodeURIComponent(use.flow)}/flow${q}`;
}

function SkillRow({ skill }: { skill: SkillName }) {
  return (
    <li className="item skill-row">
      <Link className="skill-row__link" to={`/bmad/skills/${encodeURIComponent(skill.id)}`}>
        <span className="item__name mono">{skill.id}</span>
      </Link>
      <p className="item__desc">{skill.description || th.bmad.noDescription}</p>
      <p className="item__tags">
        {skill.overrideTeam ? <span className="tag tag--warn">{th.bmad.overrideTeam}</span> : null}
        {skill.overrideUser ? <span className="tag tag--warn">{th.bmad.overrideUser}</span> : null}
        {!skill.usageKnown ? (
          <span className="tag tag--warn">{th.skills.usageUnknown}</span>
        ) : (skill.flowCount ?? 0) > 0 ? (
          <span className="tag">{th.skills.usedIn(skill.flowCount ?? 0)}</span>
        ) : (
          <span className="tag">{th.skills.unused}</span>
        )}
      </p>
    </li>
  );
}

function MissingRow({ item }: { item: MissingSkill }) {
  return (
    <li className="item skill-row skill-row--missing">
      <p className="item__name">
        <span className="mono">{item.id}</span>{" "}
        <span className="tag tag--danger"><Icon name="cross" size={14} /> {th.skills.missing}</span>
      </p>
      <p className="item__desc">{th.skills.missingHint}</p>
      <ul className="skill-uses">
        {item.usedIn.map((u) => (
          <li key={JSON.stringify([u.kind, u.flow, u.track, u.stage])}>
            <Link to={stageLink(u)} className="mono">{th.skills.useLabel(u, u.track, u.stage)}</Link>
            {!u.enabled ? <span className="tag">{th.skills.stageOff}</span> : null}
          </li>
        ))}
      </ul>
    </li>
  );
}

export function SkillCatalog() {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const searchId = useId();

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const [skills, refs] = await Promise.all([
        api.get<SkillName[]>("/api/skills"),
        api.get<SkillReferences>("/api/skill-references"),
      ]);
      setState({ kind: "ready", skills, refs });
    } catch (cause) {
      setState({ kind: "error", message: cause instanceof ApiError ? cause.message : th.skills.loadFailed });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const visible = useMemo(() => {
    if (state.kind !== "ready") return { bmad: [], veda: [], missing: [] as MissingSkill[] };
    const q = query.trim().toLowerCase();
    const match = (s: SkillName) => !q || s.id.toLowerCase().includes(q) || s.description.toLowerCase().includes(q);
    const keep = (s: SkillName) =>
      match(s) &&
      (filter === "all" ||
        (filter === "override" && (s.overrideTeam || s.overrideUser)) ||
        (filter === "unused" && Boolean(s.usageKnown) && (s.flowCount ?? 0) === 0));
    const skills = filter === "missing" ? [] : state.skills.filter(keep);
    const missing = filter === "all" || filter === "missing" ? state.refs.missing.filter((m) => !q || m.id.toLowerCase().includes(q)) : [];
    return { bmad: skills.filter((s) => s.group !== "veda"), veda: skills.filter((s) => s.group === "veda"), missing };
  }, [state, query, filter]);

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
          <p className="error-panel__title"><Icon name="alert" /><span>{th.skills.loadFailed}</span></p>
          <p>{state.message}</p>
          <button type="button" className="button button--secondary" onClick={() => void load()}>{th.errors.retry}</button>
        </section>
      </>
    );
  }

  const total = visible.bmad.length + visible.veda.length + visible.missing.length;
  const filters: Array<[Filter, string]> = [
    ["all", th.skills.filterAll], ["override", th.skills.filterOverride], ["unused", th.skills.filterUnused],
    ["missing", th.skills.filterMissing(state.refs.missing.length)],
  ];
  return (
    <>
      <BmadNav />
      <section className="panel glass skill-catalog" aria-labelledby="skills-title">
        <h2 id="skills-title" className="section-title">{th.skills.title}</h2>
        <p className="muted">{th.skills.lead}</p>
        {state.refs.unreadable.length ? (
          <p className="form-error" role="status">
            <Icon name="alert" size={16} /> {state.refs.known === false ? th.skills.defaultUnreadable
              : th.skills.unreadable(state.refs.unreadable.map((u) => u.flow).join(", "))}
          </p>
        ) : null}
        <div className="skill-toolbar">
          <div className="field skill-toolbar__search">
            <label className="field__label" htmlFor={searchId}>{th.skills.search}</label>
            <input id={searchId} className="input" type="search" value={query} onChange={(e) => setQuery(e.target.value)} />
          </div>
          <div className="segmented skill-toolbar__filters" role="group" aria-label={th.skills.filterLabel}>
            {filters.map(([f, label]) => (
              <button key={f} type="button" className="segmented__item" aria-pressed={filter === f} onClick={() => setFilter(f)}>
                {label}
              </button>
            ))}
          </div>
        </div>
        <p className="sr-only" aria-live="polite">{th.skills.count(total)}</p>
        {total === 0 ? <p className="muted">{th.skills.none}</p> : null}
        {visible.missing.length ? (
          <section aria-labelledby="skills-missing">
            <h3 id="skills-missing" className="skill-group__title">{th.skills.groupMissing}</h3>
            <ul className="item-list">{visible.missing.map((m) => <MissingRow key={m.id} item={m} />)}</ul>
          </section>
        ) : null}
        {visible.bmad.length ? (
          <section aria-labelledby="skills-bmad">
            <h3 id="skills-bmad" className="skill-group__title">{th.skills.groupBmad(visible.bmad.length)}</h3>
            <ul className="item-list">{visible.bmad.map((s) => <SkillRow key={s.id} skill={s} />)}</ul>
          </section>
        ) : null}
        {visible.veda.length ? (
          <section aria-labelledby="skills-veda">
            <h3 id="skills-veda" className="skill-group__title">{th.skills.groupVeda(visible.veda.length)}</h3>
            <ul className="item-list">{visible.veda.map((s) => <SkillRow key={s.id} skill={s} />)}</ul>
          </section>
        ) : null}
      </section>
    </>
  );
}
