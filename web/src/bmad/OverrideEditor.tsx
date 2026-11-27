// แท็บ override ของ /bmad/skills/<id> (REQ-041, UX-DR9): ฟอร์มจากพื้นผิวใน customize.toml เท่านั้น, ไอคอนกฎ merge,
// team/personal, แผง "ผลหลัง merge" sticky; บันทึกผ่าน preview kind "override" เสมอ (AD-5)
// ฟิลด์คำสั่งของ agent (activation_steps / persistent_facts) ติดป้ายผลสูง — server บังคับพิมพ์ชื่อไฟล์ยืนยัน
import { useCallback, useEffect, useId, useState } from "react";
import { useNavigate } from "react-router";

import { api, ApiError } from "../api/client";
import type { OverrideField, SkillOverride } from "../api/types";
import { clearPending, startChange } from "../changes/changeStore";
import { Icon } from "../components/Icon";
import { th } from "../i18n/th";
import { type ConfigDraft, type Edit, type Layer, loadConfigDraft, saveConfigDraft, type Scalar } from "./configDraftStore";

const OUTSIDE = "override_outside_surface";

function show(value: unknown): string {
  // ไม่ตัดความยาว: ค่าที่ agent จะอ่านต้องเห็นครบ (review 6-3) — CSS ตัดบรรทัดให้
  if (value === null || value === undefined) return "";
  return typeof value === "string" ? value : JSON.stringify(value) ?? "";
}

function RuleBadge({ field }: { field: OverrideField }) {
  const label = field.rule === "append" ? th.override.ruleAppend : field.rule === "keyed" ? th.override.ruleKeyed : th.override.ruleReplace;
  const icon = field.rule === "append" ? "down" : field.rule === "keyed" ? "reset" : "changes";
  return (
    <span className="rule-badge" title={th.override.ruleHelp(field.rule, field.keyedBy ?? "id")}>
      <Icon name={icon} size={14} />
      <span>{label}</span>
    </span>
  );
}

const listText = (value: unknown): string => (Array.isArray(value) ? value.map(String).join("\n") : String(value ?? ""));

function FieldRow({ field, layer, edit, onChange }: {
  field: OverrideField; layer: Layer; edit: Edit | undefined; onChange: (e: Edit | null) => void;
}) {
  const id = useId();
  const helpId = useId();
  const has = field.has[layer];
  const current = field[layer];
  const removing = edit?.kind === "unset";
  const isList = field.type === "list";
  const currentOk = !has || (isList ? Array.isArray(current) && current.every((v) => typeof v === "string")
    : field.type === "complex" ? false : typeof current !== "object");
  const editable = field.type !== "complex" && field.type !== "table" && currentOk;
  // ช่องหลายบรรทัดเลือกจากค่าตั้งต้น (ไม่ใช่ข้อความที่กำลังพิมพ์) — ไม่สลับ element ระหว่างพิมพ์
  const multiline = isList || [field.default, current].some((v) => typeof v === "string" && (v.includes("\n") || v.length > 80));
  // list เก็บข้อความดิบใน draft (กด Enter เพิ่มบรรทัดได้) แล้วแยกบรรทัดตอนส่ง
  const asText = edit?.kind === "set" ? String(edit.value) : has ? (isList ? listText(current) : String(current ?? "")) : "";
  const originalText = has ? (isList ? listText(current) : String(current ?? "")) : "";
  const set = (raw: Scalar, kind: "number" | "list" | null = null) => {
    if (kind === "number" ? raw === originalText || (String(raw).trim() !== "" && Number(raw) === current)
      : typeof raw === "boolean" ? raw === current : raw === originalText) {
      onChange(null);
      return;
    }
    if (!has && raw === "") {
      onChange(null);
      return;
    }
    onChange({ kind: "set", path: field.path, value: raw, numeric: kind === "number", list: kind === "list" });
  };
  const textual = field.type === "string" || isList;
  return (
    <li className={`override-field${field.instruction ? " override-field--instruction" : ""}`}>
      <div className="override-field__head">
        <label className="override-field__key mono" htmlFor={id}>{field.key}</label>
        <RuleBadge field={field} />
        {field.instruction ? (
          <span className="tag tag--warn"><Icon name="alert" size={14} /> {th.override.instructionTag}</span>
        ) : textual ? (
          <span className="tag">{th.override.textTag}</span>
        ) : null}
        {edit ? <span className="tag tag--warn">{removing ? th.config.willRemove : th.config.edited}</span> : null}
      </div>
      <p id={helpId} className="hint">
        {th.override.ruleHelp(field.rule, field.keyedBy ?? "id")}
        {field.instruction ? ` ${th.override.instructionHelp}` : textual ? ` ${th.override.textHelp}` : ""}
      </p>
      <p className="override-field__default">
        <span className="muted">{th.override.defaultLabel}</span> <span className="mono">{show(field.default) || th.config.notSet}</span>
      </p>
      {!editable ? (
        <p className="hint">{th.config.complexValue}{has ? ` — ${show(current)}` : ""}</p>
      ) : field.type === "boolean" ? (
        <select id={id} className="input mono" aria-describedby={helpId}
                value={removing ? "" : edit?.kind === "set" ? String(edit.value) : has ? String(current) : ""} disabled={removing}
                onChange={(e) => (e.target.value === "" ? onChange(null) : set(e.target.value === "true"))}>
          {!has ? <option value="">{th.config.notSet}</option> : null}
          <option value="true">{th.config.trueLabel}</option>
          <option value="false">{th.config.falseLabel}</option>
        </select>
      ) : multiline ? (
        <textarea id={id} className="input mono override-field__area" aria-describedby={helpId} rows={isList ? 3 : 5}
                  value={removing ? "" : asText} disabled={removing} placeholder={isList ? th.override.listPlaceholder : th.config.notSet}
                  onChange={(e) => set(e.target.value, isList ? "list" : null)} />
      ) : (
        <input id={id} className="input mono" aria-describedby={helpId} value={removing ? "" : asText} disabled={removing}
               placeholder={th.config.notSet} inputMode={field.type === "number" ? "decimal" : undefined}
               onChange={(e) => set(e.target.value, field.type === "number" ? "number" : null)} />
      )}
      {has ? (
        <button type="button" className="button button--secondary config-cell__remove"
                onClick={() => onChange(removing ? null : { kind: "unset", path: field.path })}>
          {removing ? th.config.undoRemove : th.config.removeOverride}
        </button>
      ) : null}
    </li>
  );
}

function formPayload(edits: Record<string, Edit>): { set: unknown[]; unset: string[][] } | { error: string } {
  const set: unknown[] = [];
  const unset: string[][] = [];
  for (const edit of Object.values(edits)) {
    if (edit.kind === "unset") {
      unset.push(edit.path);
      continue;
    }
    let value: unknown = edit.value;
    if (edit.numeric) {
      const raw = String(edit.value).trim();
      const n = Number(raw);
      if (raw === "" || !Number.isFinite(n)) return { error: th.config.badNumber(edit.path.join(".")) };
      value = n;
    } else if (edit.list) {
      value = String(edit.value).replace(/\r/g, "").split("\n").map((l) => l.trim()).filter((l) => l !== "");
    }
    set.push({ path: edit.path, value });
  }
  return { set, unset };
}

type State = { kind: "loading" } | { kind: "ready"; data: SkillOverride } | { kind: "error"; message: string };

export function OverrideEditor({ skill }: { skill: string }) {
  const navigate = useNavigate();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [layer, setLayer] = useState<Layer>("team");
  const [local, setLocal] = useState<Partial<Record<Layer, ConfigDraft>>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [outsideKeys, setOutsideKeys] = useState<string[] | null>(null);
  const [ack, setAck] = useState(false);
  const textId = useId();
  const errorId = useId();
  const ackId = useId();
  const draftKey = (l: Layer) => `override:${skill}:${l}`;

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const data = await api.get<SkillOverride>(`/api/skills/${encodeURIComponent(skill)}/override`);
      const restored: Partial<Record<Layer, ConfigDraft>> = {};
      for (const l of ["team", "user"] as Layer[]) {
        const stored = loadConfigDraft(draftKey(l), data.layers[l].sha256 ?? null);
        if (stored) restored[l] = stored;
      }
      setLocal(restored);
      setState({ kind: "ready", data });
    } catch (cause) {
      setState({ kind: "error", message: cause instanceof ApiError ? cause.message : th.override.loadFailed });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [skill]);

  useEffect(() => {
    void load();
  }, [load]);

  if (state.kind === "loading") return <section className="panel glass" aria-busy="true"><p className="muted">{th.bmad.loading}</p></section>;
  if (state.kind === "error") {
    return (
      <section className="panel surface-solid error-panel" role="alert">
        <p className="error-panel__title"><Icon name="alert" /><span>{th.override.loadFailed}</span></p>
        <p>{state.message}</p>
        <button type="button" className="button button--secondary" onClick={() => void load()}>{th.errors.retry}</button>
      </section>
    );
  }

  const { data } = state;
  const info = data.layers[layer];
  const base = info.sha256 ?? null;
  const draft: ConfigDraft = local[layer] ?? { base, mode: info.issue ? "text" : "form", edits: {}, text: null };
  const { mode, edits, text } = draft;
  const layerText = info.text ?? "";
  const editCount = Object.keys(edits).length;
  const textChanged = text !== null && text !== layerText;
  const canSubmit = mode === "form" ? editCount > 0 : textChanged;
  const modeLocked = mode === "form" ? editCount > 0 : textChanged;
  const target = data.targets[layer];
  const fileName = target.split("/").pop() ?? target;

  const update = (change: Partial<ConfigDraft>) => {
    const next: ConfigDraft = { ...draft, ...change, base };
    saveConfigDraft(draftKey(layer), next);
    setLocal((all) => ({ ...all, [layer]: next }));
    setError(null);
    setOutsideKeys(null);
    setAck(false);
  };
  const setMode = (m: "form" | "text") => {
    if (m === mode || modeLocked) return;
    update({ mode: m, edits: {}, text: m === "text" ? layerText : null });
  };

  const submit = async () => {
    if (busy) return;
    if (!canSubmit) {
      setError(th.config.nothingChanged);
      return;
    }
    let payload: Record<string, unknown>;
    if (mode === "text") payload = { text: text ?? layerText };
    else {
      const built = formPayload(edits);
      if ("error" in built) {
        setError(built.error);
        return;
      }
      payload = built;
    }
    if (ack) payload = { ...payload, acknowledged: [OUTSIDE] };
    setBusy(true);
    setError(null);
    try {
      const next = await startChange({ kind: "override", target, payload, base_sha256: base },
                                     `/bmad/skills/${encodeURIComponent(skill)}?tab=override`,
                                     mode === "text" ? (text ?? "") : JSON.stringify(payload, null, 2));
      const unconfirmed = (next.preview.errors ?? []).some((e) => e.code === "override_outside_surface_unconfirmed");
      if (unconfirmed && !ack) {
        // ค่านอก surface (REQ-041 a): ให้ยืนยันในหน้านี้ก่อน แล้วเปิดตัวอย่างใหม่พร้อม acknowledged
        const warning = (next.preview.warnings ?? []).find((w) => w.code === OUTSIDE);
        setOutsideKeys(warning?.keys ?? []);
        clearPending(); // ตัวอย่างนี้บันทึกไม่ได้อยู่แล้ว — ไม่ค้างเป็น "งานที่ยังไม่เขียน"
        return;
      }
      navigate("/changes/review?step=review");
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : th.errors.unexpected);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="override-layout">
      <section className="panel glass override-editor" aria-labelledby="override-title">
        <h2 id="override-title" className="section-title">{th.override.title}</h2>
        <p className="muted">{th.override.lead}</p>
        {(data.issues ?? []).map((item, i) => (
          <p key={`${item.code}-${i}`} className="form-error" role="status"><Icon name="alert" size={16} /> {item.error}</p>
        ))}
        <div className="config-toolbar">
          <div className="segmented" role="group" aria-label={th.config.layerLabel}>
            {(["team", "user"] as Layer[]).map((l) => (
              <button key={l} type="button" className="segmented__item" aria-pressed={layer === l}
                      onClick={() => { setLayer(l); setError(null); setOutsideKeys(null); setAck(false); }}>
                {l === "team" ? th.config.team : th.config.user}
                <span className="mono config-toolbar__file">{data.targets[l].split("/").pop()}</span>
              </button>
            ))}
          </div>
          <div className="segmented" role="group" aria-label={th.config.modeLabel}>
            <button type="button" className="segmented__item" aria-pressed={mode === "form"}
                    aria-disabled={Boolean(info.issue) || (mode !== "form" && modeLocked)}
                    onClick={() => { if (!info.issue) setMode("form"); }}>
              {th.config.modeForm}
            </button>
            <button type="button" className="segmented__item" aria-pressed={mode === "text"}
                    aria-disabled={mode !== "text" && modeLocked} onClick={() => setMode("text")}>
              {th.config.modeText}
            </button>
          </div>
        </div>
        <p className="hint">{layer === "user" ? th.config.userHint : th.config.teamHint}</p>
        {modeLocked ? <p className="hint">{th.config.modeLocked}</p> : null}
        {info.issue ? <p className="form-error"><Icon name="alert" size={16} /> {th.config.layerIssue}</p> : null}
        {data.outside[layer].length ? (
          <p className="hint"><Icon name="alert" size={14} /> {th.override.existingOutside(data.outside[layer].join(", "))}</p>
        ) : null}

        {mode === "form" ? (
          <ul className="override-fields" aria-label={th.override.fieldsLabel}>
            {data.fields.map((field) => (
              <FieldRow key={field.id} field={field} layer={layer} edit={edits[field.id]}
                        onChange={(e) => {
                          const next = { ...edits };
                          if (e) next[field.id] = e;
                          else delete next[field.id];
                          update({ edits: next });
                        }} />
            ))}
          </ul>
        ) : (
          <div className="field config-text">
            <label className="field__label" htmlFor={textId}>{th.config.textLabel(target)}</label>
            <textarea id={textId} className="input mono config-text__area" spellCheck={false} rows={14}
                      value={text ?? layerText} onChange={(e) => update({ text: e.target.value })} />
            <p className="hint">{th.override.textHint}</p>
          </div>
        )}

        {outsideKeys ? (
          <section className="issue-box issue-box--warn" aria-labelledby={ackId} role="alert">
            <h3 id={ackId} className="issue-box__title"><Icon name="alert" size={18} /> {th.override.outsideTitle}</h3>
            <p>{th.override.outsideBody}</p>
            <span className="issue-keys">
              {outsideKeys.map((k) => <code key={k} className="mono issue-keys__key">{k}</code>)}
            </span>
            <label className="check">
              <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
              <span>{th.override.outsideAck}</span>
            </label>
          </section>
        ) : null}

        <div className="config-actions">
          {canSubmit ? (
            <button type="button" className="button button--secondary"
                    onClick={() => update({ edits: {}, text: null, mode: info.issue ? "text" : "form" })}>
              {th.config.discard}
            </button>
          ) : null}
          <button type="button" className="button button--primary" aria-disabled={!canSubmit || busy || (outsideKeys !== null && !ack)}
                  aria-describedby={error ? errorId : undefined}
                  onClick={() => { if (outsideKeys === null || ack) void submit(); }}>
            {busy ? th.config.reviewing : th.config.review}
          </button>
        </div>
        <p id={errorId} className="form-error" role="alert">{error ?? ""}</p>
      </section>

      <aside className="panel surface-solid override-effective" aria-labelledby="override-effective">
        <h3 id="override-effective" className="issue-box__title">{th.override.effectiveTitle}</h3>
        <p className="hint">{th.override.effectiveHint(fileName)}</p>
        {!data.effectiveAvailable ? <p className="form-error">{th.config.effectiveUnavailable}</p> : (
          <dl className="effective-list">
            {data.fields.map((f) => (
              <div key={f.id} className="effective-list__row">
                <dt className="mono">{f.key}</dt>
                <dd className="mono">{f.has.effective ? show(f.effective) || '""' : th.config.notSet}</dd>
              </div>
            ))}
          </dl>
        )}
      </aside>
    </div>
  );
}
