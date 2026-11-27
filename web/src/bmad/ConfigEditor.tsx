// /bmad/config — config 3 ชั้นต่อคีย์ (REQ-040, UX-DR9): base (installer, อ่านอย่างเดียว) → override team/user → effective
// ค่า base/effective มาจาก resolve_config.py ฝั่ง server (AD-10); การบันทึกผ่าน preview kind "config" เสมอ (AD-5)
import { useCallback, useEffect, useId, useState } from "react";
import { useNavigate } from "react-router";

import { api, ApiError } from "../api/client";
import type { ConfigRow, ConfigView } from "../api/types";
import { startChange } from "../changes/changeStore";
import { Icon } from "../components/Icon";
import { th } from "../i18n/th";
import { BmadNav } from "./BmadNav";
import {
  type ConfigDraft,
  type Edit,
  type Layer,
  loadConfigDraft,
  type Mode,
  saveConfigDraft,
  type Scalar,
} from "./configDraftStore";

export { clearConfigDrafts } from "./configDraftStore";

function scalarKind(value: unknown): "string" | "number" | "boolean" | "complex" | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "string") return "string";
  if (typeof value === "number") return "number";
  if (typeof value === "boolean") return "boolean";
  return "complex";
}

function show(value: unknown): string {
  if (value === null || value === undefined) return "";
  const text = typeof value === "string" ? value : JSON.stringify(value) ?? "";
  return text.length > 200 ? `${text.slice(0, 199)}…` : text;
}

function ValueCell({ label, value, has, strong, locked, unknown }: {
  label: string; value: unknown; has: boolean; strong?: boolean; locked?: boolean; unknown?: boolean;
}) {
  return (
    <div className={`config-cell${strong ? " config-cell--effective" : ""}`}>
      <span className="config-cell__label">
        {locked ? <Icon name="lock" size={14} /> : null}
        {label}
      </span>
      <span className={`config-cell__value mono${has && !unknown ? "" : " muted"}`}>
        {unknown ? th.config.unknownValue : has ? show(value) : th.config.notSet}
      </span>
      {locked ? <span className="config-cell__hint">{th.config.baseLocked}</span> : null}
    </div>
  );
}

function OverrideField({ row, layer, edit, onChange }: {
  row: ConfigRow; layer: Layer; edit: Edit | undefined; onChange: (e: Edit | null) => void;
}) {
  const id = useId();
  const has = row.has[layer];
  const current = row[layer];
  const kind = scalarKind(has ? current : row.effective ?? row.base);
  const label = th.config.colOverride(layer === "team" ? th.config.team : th.config.user);
  const removing = edit?.kind === "unset";
  const shown = edit?.kind === "set" ? edit.value : has ? (current as Scalar) : "";

  if (kind === "complex") {
    return (
      <div className="config-cell">
        <ValueCell label={label} value={current} has={has} />
        <span className="config-cell__hint">{th.config.complexValue}</span>
      </div>
    );
  }
  const set = (raw: Scalar, numeric = false) => {
    const original = has ? current : undefined;
    const same = numeric ? String(raw).trim() === String(original ?? "") : raw === original;
    if (same || (raw === "" && !has)) onChange(null);
    else onChange({ kind: "set", path: row.path, value: raw, numeric });
  };
  return (
    <div className="config-cell config-cell--override">
      <label className="config-cell__label" htmlFor={id}>
        {label}
        {edit ? <span className="tag tag--warn config-cell__edited">{removing ? th.config.willRemove : th.config.edited}</span> : null}
      </label>
      {kind === "boolean" ? (
        <select id={id} className="input mono" value={removing ? "" : String(shown)} disabled={removing}
                onChange={(e) => (e.target.value === "" ? onChange(null) : set(e.target.value === "true"))}>
          {!has ? <option value="">{th.config.notSet}</option> : null}
          <option value="true">{th.config.trueLabel}</option>
          <option value="false">{th.config.falseLabel}</option>
        </select>
      ) : (
        <input id={id} className="input mono" value={removing ? "" : String(shown)} disabled={removing}
               placeholder={th.config.notSet} inputMode={kind === "number" ? "decimal" : undefined}
               onChange={(e) => set(e.target.value, kind === "number")} />
      )}
      {has ? (
        <button type="button" className="button button--secondary config-cell__remove"
                onClick={() => onChange(removing ? null : { kind: "unset", path: row.path })}>
          {removing ? th.config.undoRemove : th.config.removeOverride}
        </button>
      ) : null}
    </div>
  );
}

/** แปลง draft ของฟอร์มเป็น payload — ตัวเลขที่พิมพ์ไม่ครบ/ไม่ใช่ตัวเลข = error (ไม่ส่ง string แทน) */
function formPayload(edits: Record<string, Edit>): { payload: { set: unknown[]; unset: string[][] } } | { error: string } {
  const set: unknown[] = [];
  const unset: string[][] = [];
  for (const edit of Object.values(edits)) {
    if (edit.kind === "unset") {
      unset.push(edit.path);
      continue;
    }
    let value: Scalar | string[] = edit.value;
    if (edit.numeric) {
      const raw = String(edit.value).trim();
      const n = Number(raw);
      if (raw === "" || !Number.isFinite(n)) return { error: th.config.badNumber(edit.path.join(".")) };
      value = n;
    }
    set.push({ path: edit.path, value });
  }
  return { payload: { set, unset } };
}

type State = { kind: "loading" } | { kind: "ready"; data: ConfigView } | { kind: "error"; message: string };

const EMPTY: ConfigDraft = { base: null, mode: "form", edits: {}, text: null };

export function ConfigEditor() {
  const navigate = useNavigate();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [layer, setLayer] = useState<Layer>("team");
  const [local, setLocal] = useState<Partial<Record<Layer, ConfigDraft>>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const textId = useId();
  const errorId = useId();
  const modeHintId = useId();

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const data = await api.get<ConfigView>("/api/bmad/config");
      const restored: Partial<Record<Layer, ConfigDraft>> = {};
      for (const l of ["team", "user"] as Layer[]) {
        const stored = loadConfigDraft(l, data.layers[l].sha256 ?? null);
        if (stored) restored[l] = stored;
      }
      setLocal(restored);
      setState({ kind: "ready", data });
    } catch (cause) {
      setState({ kind: "error", message: cause instanceof ApiError ? cause.message : th.config.loadFailed });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (state.kind === "loading") {
    return (
      <>
        <BmadNav />
        <section className="panel glass" aria-busy="true" aria-live="polite">
          <p className="muted">{th.bmad.loading}</p>
        </section>
      </>
    );
  }
  if (state.kind === "error") {
    return (
      <>
        <BmadNav />
        <section className="panel surface-solid error-panel" role="alert">
          <p className="error-panel__title">
            <Icon name="alert" />
            <span>{th.config.loadFailed}</span>
          </p>
          <p>{state.message}</p>
          <button type="button" className="button button--secondary" onClick={() => void load()}>
            {th.errors.retry}
          </button>
        </section>
      </>
    );
  }

  const { data } = state;
  const info = data.layers[layer];
  const base = info.sha256 ?? null;
  const draft: ConfigDraft = local[layer] ?? { ...EMPTY, base, mode: info.issue ? "text" : "form" };
  const { mode, edits, text } = draft;
  const layerText = info.text ?? "";
  const editCount = Object.keys(edits).length;
  const textChanged = text !== null && text !== layerText;
  const canSubmit = mode === "form" ? editCount > 0 : textChanged;
  // สลับโหมดระหว่างมีการแก้ค้าง = การแก้ของอีกโหมดหายเงียบ ๆ — ต้องส่งหรือทิ้งก่อน
  const modeLocked = mode === "form" ? editCount > 0 : textChanged;
  const target = data.targets[layer];

  const update = (change: Partial<ConfigDraft>) => {
    const next: ConfigDraft = { ...draft, ...change, base };
    saveConfigDraft(layer, next);
    setLocal((all) => ({ ...all, [layer]: next }));
    setError(null);
  };
  const setMode = (m: Mode) => {
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
      payload = built.payload;
    }
    setBusy(true);
    setError(null);
    try {
      await startChange({ kind: "config", target, payload, base_sha256: base },
                        "/bmad/config", mode === "text" ? (text ?? "") : JSON.stringify(payload, null, 2));
      navigate("/changes/review?step=review");
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : th.errors.unexpected);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <BmadNav />
      <section className="panel glass config-editor" aria-labelledby="config-title">
        <h2 id="config-title" className="section-title">{th.config.title}</h2>
        <p className="muted config-editor__lead">{th.config.lead}</p>
        {(data.issues ?? []).map((item, i) => (
          <p key={`${item.code}-${i}`} className="form-error" role="status">
            <Icon name="alert" size={16} /> {item.error}
          </p>
        ))}
        <div className="config-toolbar">
          <div className="segmented" role="group" aria-label={th.config.layerLabel}>
            {(["team", "user"] as Layer[]).map((l) => (
              <button key={l} type="button" className="segmented__item" aria-pressed={layer === l}
                      onClick={() => { setLayer(l); setError(null); }}>
                {l === "team" ? th.config.team : th.config.user}
                <span className="mono config-toolbar__file">{data.targets[l].split("/").pop()}</span>
              </button>
            ))}
          </div>
          <div className="segmented" role="group" aria-label={th.config.modeLabel}>
            <button type="button" className="segmented__item" aria-pressed={mode === "form"}
                    aria-disabled={Boolean(info.issue) || (mode !== "form" && modeLocked)}
                    aria-describedby={modeLocked ? modeHintId : undefined}
                    onClick={() => { if (!info.issue) setMode("form"); }}>
              {th.config.modeForm}
            </button>
            <button type="button" className="segmented__item" aria-pressed={mode === "text"}
                    aria-disabled={mode !== "text" && modeLocked}
                    aria-describedby={modeLocked ? modeHintId : undefined}
                    onClick={() => setMode("text")}>
              {th.config.modeText}
            </button>
          </div>
        </div>
        <p className="hint">{layer === "user" ? th.config.userHint : th.config.teamHint}</p>
        {modeLocked ? <p id={modeHintId} className="hint">{th.config.modeLocked}</p> : null}
        {info.issue ? <p className="form-error"><Icon name="alert" size={16} /> {th.config.layerIssue}</p> : null}

        {mode === "form" ? (
          data.rows.length === 0 ? (
            <p className="muted">{th.config.noRows}</p>
          ) : (
            <ul className="config-rows" aria-label={th.config.rowsLabel}>
              {data.rows.map((row) => (
                <li key={row.id} className="config-row">
                  <p className="config-row__key mono">{row.key}</p>
                  <div className="config-row__cells">
                    <ValueCell label={th.config.colBase} value={row.base} has={row.has.base} locked
                               unknown={!data.baseAvailable} />
                    <OverrideField row={row} layer={layer} edit={edits[row.id]}
                                   onChange={(e) => {
                                     const next = { ...edits };
                                     if (e) next[row.id] = e;
                                     else delete next[row.id];
                                     update({ edits: next });
                                   }} />
                    <ValueCell label={th.config.colEffective} value={row.effective} has={row.has.effective} strong
                               unknown={!data.effectiveAvailable} />
                  </div>
                </li>
              ))}
            </ul>
          )
        ) : (
          <div className="field config-text">
            <label className="field__label" htmlFor={textId}>{th.config.textLabel(target)}</label>
            <textarea id={textId} className="input mono config-text__area" spellCheck={false} rows={14}
                      value={text ?? layerText} onChange={(e) => update({ text: e.target.value })} />
            <p className="hint">{th.config.textHint}</p>
          </div>
        )}

        <div className="config-actions">
          {canSubmit ? (
            <button type="button" className="button button--secondary"
                    onClick={() => update({ edits: {}, text: null, mode: info.issue ? "text" : "form" })}>
              {th.config.discard}
            </button>
          ) : null}
          <button type="button" className="button button--primary" aria-disabled={!canSubmit || busy}
                  aria-describedby={error ? errorId : undefined} onClick={() => void submit()}>
            {busy ? th.config.reviewing : th.config.review}
          </button>
        </div>
        <p id={errorId} className="form-error" role="alert">{error ?? ""}</p>
      </section>
    </>
  );
}
