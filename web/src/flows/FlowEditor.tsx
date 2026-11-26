// FlowEditor (design.md "FlowEditor", REQ-045/046/047, UX-DR5/6/7) — ใช้ทั้งต่อโปรเจกต์ (4-2) และ default (4-3)
// UI เป็นแค่ผู้แสดงผล (AD-1): locked/lockReason/mustFollowLocked/errors/ackRequired มาจาก API เท่านั้น,
// ไม่มีรายการขั้นที่ล็อกหรือกฎ validation ในไฟล์นี้; การเขียนไปทาง DiffPreview (AD-5) เท่านั้น
import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import { Link, useNavigate } from "react-router";

import { api, ApiError } from "../api/client";
import type { AgentName, ChangeRequestBody, EditorTrack, FlowDraftCheck, FlowEditor as EditorView, FlowIssue, SkillName } from "../api/types";
import { useSession } from "../auth/SessionContext";
import { startChange } from "../changes/changeStore";
import { Icon } from "../components/Icon";
import { th } from "../i18n/th";
import { gateText } from "../projects/stageView";
import {
  defaultValue,
  draftFromFlow,
  draftFromView,
  fieldDiffers,
  inDisplayOrder,
  moveBlock,
  moveStage,
  nextEnabledAfter,
  sameDraft,
  updateStage,
  withStages,
  type Draft,
  type EditableField,
  type Meta,
  type MoveBlock,
  type Stage,
} from "./flowDraft";
import { dropDraft, loadDraft, saveDraft } from "./draftStore";

export const VALIDATE_DEBOUNCE_MS = 200;
const WIDE = "(min-width: 960px)";
const LOCK = "locked_gate_violation";
/** code ของคำเตือนที่ใช้หัวข้อเฉพาะของ design.md (ConfirmDisableUx) — ไม่ใช่กฎ: server เป็นผู้บอกว่าต้องยืนยัน */
const ACK_UX = "ux_disabled_frontend";

/** แหล่งข้อมูลของ editor — ต่อโปรเจกต์ (4-2) หรือ flow เริ่มต้น (4-3) */
export interface FlowEditorSource {
  key: string;
  load: () => Promise<EditorView>;
  validate: (draft: Draft) => Promise<FlowDraftCheck>;
  request: (draft: Draft, acknowledged: string[], base: string | null, target: string) => ChangeRequestBody;
  returnTo: string;
  title: string;
  backTo: { href: string; label: string };
  /** ข้อความเตือนเพิ่มเหนือ editor (เช่น "กระทบทุกโปรเจกต์ที่ไม่ override") */
  notice?: (view: EditorView) => ReactNode;
  /** ป้ายที่มา (ค่าตั้งต้น: "สืบทอดจาก default · ปรับเอง N จุด") */
  sourceLabel?: (overrides: number, view: EditorView) => string;
}

function useMedia(query: string): boolean {
  const get = () => (typeof window !== "undefined" && typeof window.matchMedia === "function" ? window.matchMedia(query).matches : false);
  const [matches, setMatches] = useState(get);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mql = window.matchMedia(query);
    const onChange = () => setMatches(mql.matches);
    onChange();
    mql.addEventListener?.("change", onChange);
    return () => mql.removeEventListener?.("change", onChange);
  }, [query]);
  return matches;
}

function blockText(block: MoveBlock): string | null {
  switch (block) {
    case "first":
      return th.flowEditor.blockFirst;
    case "last":
      return th.flowEditor.blockLast;
    case "locked-prev":
      return th.flowEditor.blockLockedPrev;
    case "locked-next":
      return th.flowEditor.blockLockedNext;
    case "locked-self":
      return th.flowEditor.blockLockedSelf;
    default:
      return null;
  }
}

function valueText(field: EditableField, value: unknown): string {
  if (value === null || value === undefined || value === "") return th.flowEditor.none;
  if (field === "enabled") return value ? th.flowEditor.yes : th.flowEditor.no;
  if (field === "gate") return gateText(String(value));
  return String(value);
}

// -- dialog / sheet ---------------------------------------------------------------------------------

/** dialog/sheet ที่เปิดอยู่ (บนสุด = ท้ายสุด) — Esc/Tab ทำงานกับตัวบนสุดเท่านั้น */
const trapStack: HTMLElement[] = [];

const FOCUSABLE = "button:not([disabled]), input:not([disabled]):not([type=hidden]), select:not([disabled]), a[href], [tabindex='0']";

/** ตัวที่ควรได้ focus แรก: ``[data-autofocus]`` ถ้ามี ไม่งั้นตัวแรกที่ focus ได้ */
function initialTarget(node: HTMLElement): HTMLElement | null {
  return node.querySelector<HTMLElement>("[data-autofocus]") ?? node.querySelector<HTMLElement>(FOCUSABLE);
}

/**
 * focus trap ของ dialog/sheet: focus แรกไปที่ ``[data-autofocus]`` (ตั้งใน layout effect แล้วยืนยันอีกครั้งในเฟรมถัดไป
 * — คลิกเมาส์/Enter จริงมี event ที่มาทีหลังและดึง focus ออกได้ ซึ่ง element.click() ใน test ไม่มี), และดึง focus
 * กลับเข้ามาทุกครั้งที่ไปอยู่นอก dialog (focusin + Tab) ไม่ใช่แค่วนที่ปุ่มแรก/สุดท้าย (review 4-2 B1)
 */
function useFocusTrap(ref: React.RefObject<HTMLElement | null>, active: boolean, onEscape?: () => void) {
  const escape = useRef(onEscape);
  escape.current = onEscape;
  useLayoutEffect(() => {
    if (!active) return;
    const opener = document.activeElement;
    const node = ref.current;
    if (!node) return;
    trapStack.push(node);
    const isTop = () => trapStack[trapStack.length - 1] === node;
    const pullIn = () => {
      if (isTop() && !node.contains(document.activeElement)) initialTarget(node)?.focus();
    };
    initialTarget(node)?.focus();
    const frame = window.requestAnimationFrame(pullIn);
    const late = window.setTimeout(pullIn, 120);
    function onFocusIn(event: FocusEvent) {
      if (isTop() && event.target instanceof Node && !node!.contains(event.target)) initialTarget(node!)?.focus();
    }
    function onKey(event: KeyboardEvent) {
      if (!isTop()) return;
      if (event.key === "Escape" && escape.current) {
        if (event.defaultPrevented) return; // control ข้างในจัดการ Esc เองแล้ว
        event.preventDefault();
        escape.current();
        return;
      }
      if (event.key !== "Tab") return;
      const items = Array.from(node!.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      const at = document.activeElement;
      if (!(at instanceof HTMLElement) || !node!.contains(at)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && at === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && at === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("focusin", onFocusIn, true);
    return () => {
      window.cancelAnimationFrame(frame);
      window.clearTimeout(late);
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("focusin", onFocusIn, true);
      const at = trapStack.lastIndexOf(node);
      if (at >= 0) trapStack.splice(at, 1);
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
    };
  }, [active, ref]);
}

/** ยืนยันคำเตือนที่ server บอกว่าต้องยืนยัน (ackRequired) — ข้อความรายละเอียดมาจาก API */
function ConfirmWarning({ codes, warnings, onCancel, onConfirm }: {
  codes: string[]; warnings: FlowIssue[]; onCancel: () => void; onConfirm: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const bodyId = useId();
  useFocusTrap(ref, true, onCancel);
  const ux = codes.includes(ACK_UX);
  const details = warnings.filter((w) => codes.includes(w.code));
  return (
    <div className="overlay">
      <div ref={ref} className="dialog surface-solid" role="alertdialog" aria-modal="true" aria-labelledby={titleId}
           aria-describedby={bodyId}>
        <h2 id={titleId} className="dialog__title">{ux ? th.flowEditor.uxTitle : th.flowEditor.ackTitle}</h2>
        <div id={bodyId} className="dialog__body">
          {ux ? <p>{th.flowEditor.uxBody}</p> : null}
          {details.map((w, i) => <p key={i}>{w.error}</p>)}
        </div>
        <div className="dialog__actions">
          <button ref={cancelRef} type="button" className="button button--secondary" onClick={onCancel} data-autofocus>
            {th.flowEditor.uxCancel}
          </button>
          <button type="button" className="button button--warn" onClick={onConfirm}>
            {ux ? th.flowEditor.uxConfirm : th.flowEditor.ackConfirm}
          </button>
        </div>
      </div>
    </div>
  );
}

function Sheet({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useFocusTrap(ref, true, onClose);
  return (
    <div className="overlay overlay--sheet" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={ref} className="sheet surface-solid" role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <div className="sheet__head">
          <h2 id={titleId} className="sheet__title mono">{title}</h2>
          <button type="button" className="icon-button sheet__close" onClick={onClose} aria-label={th.flowEditor.close} data-autofocus>
            <Icon name="cross" />
          </button>
        </div>
        <div className="sheet__body">{children}</div>
      </div>
    </div>
  );
}

// -- StageList ------------------------------------------------------------------------------------

interface RowProps {
  stage: Stage;
  meta: Meta | undefined;
  index: number;
  total: number;
  selected: boolean;
  picked: boolean;
  wide: boolean;
  errors: FlowIssue[];
  nextEnabled: string | null;
  hasFrontend: boolean | null | undefined;
  reviewUrl: string | null;
  reserved: string[];
  onSelect: () => void;
  onToggle: (enabled: boolean) => void;
  onHandleKey: (event: ReactKeyboardEvent<HTMLButtonElement>) => void;
  onDragStart: (event: DragEvent<HTMLButtonElement>) => void;
  onDrop: (event: DragEvent<HTMLLIElement>) => void;
}

function StageRowView(props: RowProps) {
  const { stage, meta, index, total, selected, picked, wide, errors, nextEnabled } = props;
  const reasonId = useId();
  const errorId = useId();
  const locked = Boolean(meta?.locked);
  const added = meta !== undefined && !meta.default;
  const overridden = Boolean(meta?.overridden);
  const rowClass = ["flow-row", locked ? "flow-row--locked" : "", stage.enabled ? "" : "flow-row--off",
    selected ? "flow-row--selected" : "", picked ? "flow-row--picked" : "", errors.length ? "flow-row--error" : ""]
    .filter(Boolean)
    .join(" ");
  return (
    <li
      className={rowClass}
      data-stage={stage.id}
      onDragOver={(e) => wide && e.preventDefault()}
      onDrop={props.onDrop}
    >
      <div className="flow-row__rail" aria-hidden="true">
        <span className="flow-row__dot" />
      </div>
      <div className="flow-row__lead">
        {locked ? (
          <span className="flow-row__lock" title={th.flowEditor.locked}>
            <Icon name="lock" size={20} />
          </span>
        ) : (
          <button
            type="button"
            className="flow-row__handle"
            aria-label={th.flowEditor.handle(stage.id)}
            aria-pressed={picked}
            aria-describedby={reasonId}
            draggable={wide}
            onKeyDown={props.onHandleKey}
            onDragStart={props.onDragStart}
          >
            <Icon name="grip" size={20} />
            <span className="flow-row__num" aria-hidden="true">{index + 1}</span>
          </button>
        )}
      </div>
      <button
        type="button"
        className="flow-row__body"
        aria-current={selected ? "true" : undefined}
        aria-describedby={[locked ? reasonId : "", errors.length ? errorId : ""].filter(Boolean).join(" ") || undefined}
        onClick={props.onSelect}
      >
        <span className="flow-row__title">
          <span className="flow-row__id mono">{stage.id}</span>
          {locked ? (
            <span className="tag tag--lock">
              <Icon name="lock" size={14} /> {th.flowEditor.locked}
            </span>
          ) : null}
          {overridden && !added ? <span className="tag tag--warn">● {th.flowEditor.overridden}</span> : null}
          {added ? <span className="tag tag--warn">{th.flowEditor.added}</span> : null}
          {!stage.enabled ? <span className="tag tag--skip">{th.flowEditor.disabled}</span> : null}
        </span>
        <span className="flow-row__meta">
          <span>{props.reserved.includes(stage.agent) ? th.flow.gate.human : stage.agent}</span>
          {stage.skill ? <span className="mono">{stage.skill}</span> : null}
          {stage.gate !== "none" ? (
            <span className="tag">
              {stage.gate === "human" ? <Icon name="person" size={14} /> : null} {th.flow.gateLabel}: {gateText(stage.gate)}
            </span>
          ) : null}
          {stage.condition ? <span className="tag">{th.flowEditor.condition(stage.condition)}</span> : null}
        </span>
      </button>
      <div className="flow-row__trail">
        {locked ? (
          // คอนโทรลที่ถูกตัดออกแทนด้วยกุญแจในตำแหน่งเดิม: focus ได้ อ่านเหตุผลได้ แต่เปลี่ยนไม่ได้
          <span className="flow-row__lock" role="switch" aria-checked={stage.enabled} aria-disabled="true" tabIndex={0}
                aria-label={th.flowEditor.enable(stage.id)} aria-describedby={reasonId}>
            <Icon name="lock" size={20} />
          </span>
        ) : (
          <label className="switch">
            <input
              type="checkbox"
              role="switch"
              checked={stage.enabled}
              onChange={(e) => props.onToggle(e.target.checked)}
              aria-label={th.flowEditor.enable(stage.id)}
            />
            <span className="switch__track" aria-hidden="true" />
          </label>
        )}
      </div>
      <div className="flow-row__notes">
        {locked && meta?.lockReason ? (
          <p id={reasonId} className="flow-row__reason flow-row__reason--lock">
            {th.flowEditor.lockedPolicy(meta.lockReason)}
            {props.reviewUrl ? (
              <>
                {" "}
                <a href={props.reviewUrl} target="_blank" rel="noopener noreferrer">{th.flowEditor.seeReview}</a>
              </>
            ) : null}
          </p>
        ) : (
          <span id={reasonId} className="sr-only">{th.flowEditor.position(index + 1, total)}</span>
        )}
        {!stage.enabled ? (
          <p className="flow-row__reason">
            {nextEnabled ? th.flowEditor.skipTo(nextEnabled) : null}
            {props.hasFrontend === false && stage.condition === "hasFrontend" ? (
              <span className="tag tag--ok"><Icon name="check" size={14} /> {th.flowEditor.noFrontendOk}</span>
            ) : null}
          </p>
        ) : null}
      </div>
      {errors.length ? (
        <ul id={errorId} className="flow-row__errors">
          {errors.map((e, i) => (
            <li key={`${e.code}-${i}`}>
              <Icon name="cross" size={16} /> {e.error}
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

// -- Inspector ------------------------------------------------------------------------------------

interface InspectorProps {
  stage: Stage;
  meta: Meta | undefined;
  lockedStageId: string | null;
  agents: AgentName[] | null;
  skills: SkillName[] | null;
  gates: string[];
  conditions: string[];
  reserved: string[];
  moveUp: MoveBlock;
  moveDown: MoveBlock;
  reviewUrl: string | null;
  onChange: (patch: Partial<Stage>) => void;
  onMove: (dir: -1 | 1) => void;
}

function DefaultNote({ stage, meta, field, onRestore }: { stage: Stage; meta: Meta | undefined; field: EditableField; onRestore: () => void }) {
  if (!fieldDiffers(stage, meta, field)) return null;
  return (
    <p className="field__default">
      <span>{th.flowEditor.defaultIs(valueText(field, defaultValue(meta, field)))}</span>
      <button type="button" className="button button--ghost" onClick={onRestore}>
        <Icon name="reset" size={16} /> {th.flowEditor.restoreDefault}
      </button>
    </p>
  );
}

function ReadOnlyInspector({ stage, meta, reviewUrl }: { stage: Stage; meta: Meta | undefined; reviewUrl: string | null }) {
  const rows: Array<[string, string]> = [
    [th.flowEditor.fieldEnabled, valueText("enabled", stage.enabled)],
    [th.flowEditor.fieldAgent, stage.agent],
    [th.flowEditor.fieldSkill, valueText("skill", stage.skill)],
    [th.flowEditor.fieldGate, valueText("gate", stage.gate)],
    [th.flowEditor.fieldCondition, valueText("condition", stage.condition)],
    [th.flowEditor.fieldProduces, stage.produces.join(", ") || th.flowEditor.none],
  ];
  return (
    <div className="inspector inspector--locked">
      <p className="inspector__locked-title">
        <Icon name="lock" size={20} /> {th.flowEditor.readOnlyTitle}
      </p>
      <dl className="inspector__values">
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd className="mono">{value}</dd>
          </div>
        ))}
      </dl>
      <details open className="inspector__why">
        <summary>{th.flowEditor.whyLocked}</summary>
        {meta?.lockReason ? <p>{meta.lockReason}</p> : null}
        <p>{th.flowEditor.noAppWrite}</p>
        {reviewUrl ? (
          <a className="button button--secondary" href={reviewUrl} target="_blank" rel="noopener noreferrer">
            <Icon name="external" size={18} /> {th.flowEditor.seeReview}
          </a>
        ) : null}
      </details>
    </div>
  );
}

function StageInspector(props: InspectorProps) {
  const { stage, meta, agents, skills } = props;
  const [query, setQuery] = useState("");
  const ids = { agent: useId(), skill: useId(), search: useId(), condition: useId(), up: useId(), down: useId(), gate: useId() };
  if (meta?.locked) return <ReadOnlyInspector stage={stage} meta={meta} reviewUrl={props.reviewUrl} />;
  const agentKnown = agents === null || props.reserved.includes(stage.agent) || agents.some((a) => a.name === stage.agent);
  const skillKnown = stage.skill === null || skills === null || skills.some((s) => s.id === stage.skill);
  const q = query.trim().toLowerCase();
  const shownSkills = (skills ?? []).filter((s) => !q || s.id.toLowerCase().includes(q) || s.description.toLowerCase().includes(q));
  const restore = (field: EditableField) => props.onChange({ [field]: defaultValue(meta, field) } as Partial<Stage>);
  const upText = blockText(props.moveUp);
  const downText = blockText(props.moveDown);
  const baseGate = defaultValue(meta, "gate");
  return (
    <div className="inspector">
      {meta?.mustFollowLocked && props.lockedStageId ? (
        <p className="inspector__follow">
          <Icon name="lock" size={16} /> {th.flowEditor.mustFollow(props.lockedStageId)}
        </p>
      ) : null}
      <div className="inspector__moves">
        <div>
          <button type="button" className="button button--secondary" aria-disabled={upText !== null}
                  aria-describedby={upText ? ids.up : undefined} onClick={() => upText === null && props.onMove(-1)}>
            <Icon name="up" size={18} /> {th.flowEditor.moveUp}
          </button>
          {upText ? <p id={ids.up} className="hint">{upText}</p> : null}
        </div>
        <div>
          <button type="button" className="button button--secondary" aria-disabled={downText !== null}
                  aria-describedby={downText ? ids.down : undefined} onClick={() => downText === null && props.onMove(1)}>
            <Icon name="down" size={18} /> {th.flowEditor.moveDown}
          </button>
          {downText ? <p id={ids.down} className="hint">{downText}</p> : null}
        </div>
      </div>

      <div className="field">
        <label className="check">
          <input type="checkbox" role="switch" checked={stage.enabled} onChange={(e) => props.onChange({ enabled: e.target.checked })} />
          <span>{th.flowEditor.fieldEnabled}</span>
        </label>
        <DefaultNote stage={stage} meta={meta} field="enabled" onRestore={() => restore("enabled")} />
      </div>

      <div className="field">
        <label className="field__label" htmlFor={ids.agent}>{th.flowEditor.fieldAgent}</label>
        <select id={ids.agent} className="input mono" value={stage.agent} onChange={(e) => props.onChange({ agent: e.target.value })}
                aria-invalid={!agentKnown || undefined}>
          {!agentKnown ? <option value={stage.agent}>{th.flowEditor.notFoundOption(stage.agent)}</option> : null}
          {agents === null || props.reserved.includes(stage.agent) ? <option value={stage.agent}>{stage.agent}</option> : null}
          {(agents ?? []).map((a) => (
            <option key={a.name} value={a.name}>{a.name}</option>
          ))}
        </select>
        {!agentKnown ? <p className="form-error"><Icon name="cross" size={16} /> {th.flowEditor.agentMissing}</p> : null}
        <DefaultNote stage={stage} meta={meta} field="agent" onRestore={() => restore("agent")} />
      </div>

      <div className="field">
        <label className="field__label" htmlFor={ids.skill}>{th.flowEditor.fieldSkill}</label>
        <input id={ids.search} className="input" type="search" placeholder={th.flowEditor.fieldSkillSearch}
               aria-label={th.flowEditor.fieldSkillSearch} value={query} onChange={(e) => setQuery(e.target.value)} />
        <select id={ids.skill} className="input mono" value={stage.skill ?? ""} aria-invalid={!skillKnown || undefined}
                onChange={(e) => props.onChange({ skill: e.target.value || null })}>
          <option value="">{th.flowEditor.noSkill}</option>
          {!skillKnown && stage.skill ? <option value={stage.skill}>{th.flowEditor.notFoundOption(stage.skill)}</option> : null}
          {stage.skill && skillKnown && !shownSkills.some((s) => s.id === stage.skill) ? (
            <option value={stage.skill}>{stage.skill}</option>
          ) : null}
          {shownSkills.map((s) => (
            <option key={s.id} value={s.id}>{s.id}</option>
          ))}
        </select>
        {!skillKnown ? <p className="form-error"><Icon name="cross" size={16} /> {th.flowEditor.skillMissing}</p> : null}
        <DefaultNote stage={stage} meta={meta} field="skill" onRestore={() => restore("skill")} />
      </div>

      <div className="field" role="group" aria-labelledby={ids.gate}>
        <span id={ids.gate} className="field__label">{th.flowEditor.fieldGate}</span>
        <div className="segmented">
          {props.gates.map((g) => (
            <button key={g} type="button" className="segmented__item" aria-pressed={stage.gate === g}
                    onClick={() => props.onChange({ gate: g as Stage["gate"] })}>
              {gateText(g)}
            </button>
          ))}
        </div>
        {baseGate === "human" && stage.gate === "none" ? (
          <p className="banner banner--warn"><Icon name="alert" size={18} /> <span>{th.flowEditor.gateDowngrade}</span></p>
        ) : null}
        <DefaultNote stage={stage} meta={meta} field="gate" onRestore={() => restore("gate")} />
      </div>

      <div className="field">
        <label className="field__label" htmlFor={ids.condition}>{th.flowEditor.fieldCondition}</label>
        <select id={ids.condition} className="input mono" value={stage.condition ?? ""}
                onChange={(e) => props.onChange({ condition: e.target.value || null })}>
          <option value="">{th.flowEditor.noCondition}</option>
          {stage.condition && !props.conditions.includes(stage.condition) ? (
            <option value={stage.condition}>{th.flowEditor.notFoundOption(stage.condition)}</option>
          ) : null}
          {props.conditions.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        <DefaultNote stage={stage} meta={meta} field="condition" onRestore={() => restore("condition")} />
      </div>

      <div className="field">
        <span className="field__label">{th.flowEditor.fieldProduces}</span>
        <p className="inspector__chips">
          {stage.produces.length ? stage.produces.map((p) => <span key={p} className="tag mono">{p}</span>) : th.flowEditor.none}
        </p>
      </div>
    </div>
  );
}

// -- ValidationPanel ------------------------------------------------------------------------------

type Check = { kind: "idle" } | { kind: "checking"; last: FlowDraftCheck | null } | { kind: "done"; result: FlowDraftCheck } | { kind: "failed" };

function ValidationPanel({ check, onGo, compact, open, onToggle, onRetry }: {
  check: Check; onGo: (stage: string, track?: string | null) => void; compact: boolean; open: boolean; onToggle: () => void;
  onRetry: () => void;
}) {
  const titleId = useId();
  const result = check.kind === "done" ? check.result : check.kind === "checking" ? check.last : null;
  const errors = result?.errors ?? [];
  const warnings = result?.warnings ?? [];
  const summary = check.kind === "failed"
    ? th.flowEditor.validationFailed
    : errors.length
    ? th.flowEditor.summaryErrors(errors.length)
    : warnings.length
      ? th.flowEditor.summaryWarnings(warnings.length)
      : th.flowEditor.summaryOk;
  const body = (
    <div className="validation" aria-live={compact ? undefined : "polite"} aria-busy={check.kind === "checking"}>
      {check.kind === "failed" ? (
        <p className="form-error">
          {th.flowEditor.validationFailed}{" "}
          <button type="button" className="button button--ghost" onClick={onRetry}>{th.errors.retry}</button>
        </p>
      ) : null}
      {check.kind === "checking" && !result ? <p className="muted">{th.flowEditor.validationChecking}</p> : null}
      {result && !errors.length && !warnings.length ? (
        <p className="validation__ok"><Icon name="check" size={18} /> {th.flowEditor.validationOk}</p>
      ) : null}
      {errors.length || warnings.length ? (
        <ul className="validation__list">
          {[...errors.map((e) => ({ ...e, sev: "error" as const })), ...warnings.map((w) => ({ ...w, sev: "warning" as const }))].map(
            (item, i) => (
              <li key={`${item.sev}-${item.code}-${i}`} className={`validation__item validation__item--${item.sev}`}>
                <Icon name={item.sev === "error" ? "cross" : "alert"} size={18} />
                <span>
                  <strong>{item.sev === "error" ? th.flowEditor.issueError : th.flowEditor.issueWarning}:</strong> {item.error}
                </span>
                {item.stage && !item.project ? (
                  <button type="button" className="button button--ghost" onClick={() => onGo(item.stage as string, item.track)}>
                    {th.flowEditor.goToStage}
                  </button>
                ) : null}
              </li>
            ),
          )}
        </ul>
      ) : null}
    </div>
  );
  if (!compact) {
    return (
      <section className="validation-panel surface-solid" aria-labelledby={titleId}>
        <h3 id={titleId} className="section-title">{th.flowEditor.validationTitle}</h3>
        {body}
      </section>
    );
  }
  return (
    <section className="validation-bar surface-solid" aria-label={th.flowEditor.validationTitle}>
      <button type="button" className={`validation-bar__toggle${errors.length ? " validation-bar__toggle--error" : warnings.length ? " validation-bar__toggle--warn" : ""}`}
              aria-expanded={open} onClick={onToggle}>
        <Icon name={errors.length ? "cross" : warnings.length ? "alert" : "check"} size={18} />
        <span aria-live="polite">{summary}</span>
        <span aria-hidden="true">{open ? "⌄" : "›"}</span>
      </button>
      {open ? body : null}
    </section>
  );
}

// -- หน้า editor ----------------------------------------------------------------------------------

type Load = { kind: "loading"; slow: boolean } | { kind: "ready"; view: EditorView } | { kind: "error"; message: string; notFound: boolean };

export function FlowEditor({ source }: { source: FlowEditorSource }) {
  const navigate = useNavigate();
  const { session } = useSession();
  const reviewUrl = session?.review_url ?? null;
  const wide = useMedia(WIDE);
  const [load, setLoad] = useState<Load>({ kind: "loading", slow: false });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [acknowledged, setAcknowledged] = useState<string[]>([]);
  const [track, setTrack] = useState("full");
  const [selected, setSelected] = useState<string | null>(null);
  const [check, setCheck] = useState<Check>({ kind: "idle" });
  const [agents, setAgents] = useState<AgentName[] | null>(null);
  const [skills, setSkills] = useState<SkillName[] | null>(null);
  const [announce, setAnnounce] = useState("");
  const [picked, setPicked] = useState<{ id: string; snapshot: Stage[] } | null>(null);
  const [uxPrompt, setUxPrompt] = useState<{ before: Draft; codes: string[]; fromSubmit?: boolean } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [retry, setRetry] = useState(0);
  /** draft ล่าสุดที่ตรวจแล้วไม่ต้องยืนยันอะไรเพิ่ม — "ยกเลิก" ใน dialog ยืนยันกลับไปที่นี่ */
  const lastClean = useRef<Draft | null>(null);
  const declined = useRef<Draft | null>(null);
  const requestId = useRef(0);
  const blockedId = useId();

  const view = load.kind === "ready" ? load.view : null;
  const loaded = useMemo(() => (view ? draftFromView(view) : null), [view]);

  // โหลด view + รายชื่อ agent/skill
  const reload = useCallback(async () => {
    setLoad({ kind: "loading", slow: false });
    const slow = window.setTimeout(() => setLoad((s) => (s.kind === "loading" ? { kind: "loading", slow: true } : s)), 400);
    try {
      const data = await source.load();
      const stored = loadDraft(source.key, data.base_sha256 ?? null);
      lastClean.current = null;
      setLoad({ kind: "ready", view: data });
      setDraft(stored ? stored.draft : draftFromView(data));
      // ไม่ยืนยันแทนผู้ใช้: สถานะที่โหลดมาซึ่งปิด ux อยู่แล้ว (จาก default/revert/แก้มือ) แสดงเป็นแบนเนอร์
      // และถามตอนกดตรวจสอบ — ไม่ถามทุกครั้งที่แก้ขั้นอื่น (review 4-2 N1, 4-3 B1)
      setAcknowledged(stored ? stored.acknowledged : []);
    } catch (error) {
      setLoad({
        kind: "error",
        message: error instanceof ApiError ? error.message : th.flowEditor.loadFailed,
        notFound: error instanceof ApiError && error.status === 404,
      });
    } finally {
      window.clearTimeout(slow);
    }
  }, [source]);

  useEffect(() => {
    void reload();
    api.get<AgentName[]>("/api/agents/names").then(setAgents, () => setAgents(null));
    api.get<SkillName[]>("/api/skills").then(setSkills, () => setSkills(null));
  }, [reload]);

  const dirty = Boolean(draft && loaded && !sameDraft(draft, loaded));

  // เก็บ draft ไว้ใน memory (กลับจาก DiffPreview แล้วยังอยู่)
  useEffect(() => {
    if (!view || !draft) return;
    if (dirty) saveDraft(source.key, { base: view.base_sha256 ?? null, draft, acknowledged });
    else dropDraft(source.key);
  }, [draft, dirty, acknowledged, view, source.key]);

  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = th.flowEditor.unsaved;
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);

  // validation สด (debounce) — คำตอบที่เก่ากว่าทิ้ง
  useEffect(() => {
    if (!draft) return;
    const id = ++requestId.current;
    setCheck((c) => ({ kind: "checking", last: c.kind === "done" ? c.result : c.kind === "checking" ? c.last : null }));
    const timer = window.setTimeout(async () => {
      try {
        const result = await source.validate(draft);
        if (id !== requestId.current) return;
        setCheck({ kind: "done", result });
        // การยืนยันที่ไม่ต้องใช้แล้ว (เช่น เปิด ux กลับ) ถูกลบ — ปิดอีกครั้งต้องยืนยันใหม่
        if (acknowledged.some((c) => !result.ackRequired.includes(c))) {
          setAcknowledged(acknowledged.filter((c) => result.ackRequired.includes(c)));
        }
        const fresh = result.ackRequired.filter((code) => !acknowledged.includes(code));
        if (!fresh.length) {
          lastClean.current = draft;
        } else if (lastClean.current && declined.current !== draft) {
          setUxPrompt({ before: lastClean.current, codes: fresh });
        }
      } catch {
        if (id === requestId.current) setCheck({ kind: "failed" });
      }
    }, VALIDATE_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [draft, source, acknowledged, retry]);

  const result = check.kind === "done" ? check.result : check.kind === "checking" ? check.last : null;
  const metaTracks: Record<string, EditorTrack> | null = result?.tracks ?? view?.tracks ?? null;
  // track ที่แสดง = track ที่เลือกถ้ามีใน draft ไม่งั้น track แรก — ทุกการแก้ใช้ key นี้ (ไม่สร้าง track ใหม่)
  const trackKey = draft && !draft.tracks[track] ? Object.keys(draft.tracks)[0] ?? track : track;
  const metaFor = useCallback(
    (id: string): Meta | undefined =>
      metaTracks?.[trackKey]?.stages.find((s) => s.id === id) ?? view?.tracks[trackKey]?.stages.find((s) => s.id === id),
    [metaTracks, trackKey, view],
  );

  const edit = useCallback((next: Draft) => {
    setDraft(next);
    setNotice(null);
    setSubmitError(null);
  }, []);

  if (load.kind === "loading") {
    return (
      <section className="panel glass" aria-busy="true" aria-live="polite">
        <h2 className="flow-head__name mono">{source.title}</h2>
        {load.slow ? <p className="muted">{th.flowEditor.loading}</p> : null}
      </section>
    );
  }
  if (load.kind === "error" || !view || !draft || !loaded) {
    const message = load.kind === "error" ? load.message : th.flowEditor.loadFailed;
    const notFound = load.kind === "error" && load.notFound;
    return (
      <section className="panel surface-solid error-panel" role="alert">
        <Link className="flow-head__back" to={source.backTo.href}>← {source.backTo.label}</Link>
        <p className="error-panel__title"><Icon name="alert" /> <span>{notFound ? th.flowEditor.notFound : th.flowEditor.loadFailed}</span></p>
        {!notFound ? (
          <>
            <p>{message}</p>
            <button type="button" className="button button--secondary" onClick={() => void reload()}>{th.errors.retry}</button>
          </>
        ) : null}
      </section>
    );
  }

  const tracks = Object.keys(draft.tracks);
  const current = draft.tracks[trackKey];
  if (!current) {
    return (
      <section className="panel surface-solid error-panel" role="alert">
        <p className="error-panel__title"><Icon name="alert" /> <span>{th.flowEditor.loadFailed}</span></p>
      </section>
    );
  }
  const stages = inDisplayOrder(current.stages);
  const total = stages.length;
  const lockedStage = stages.find((s) => metaFor(s.id)?.locked)?.id ?? null;
  const errors = result?.errors ?? [];
  const lockErrors = errors.filter((e) => e.code === LOCK);
  const loadedLockIssues = (view.issues ?? []).filter((i) => i.code === LOCK);
  const stillAsLoaded = sameDraft(draft, loaded);
  const editable = view.editable !== false;
  const stageErrors = (id: string) => errors.filter((e) => e.stage === id && (e.track ?? "full") === trackKey);
  const selectedStage = stages.find((s) => s.id === selected) ?? null;

  const blockReason = !editable
    ? th.flowEditor.blockedNotEditable
    : loadedLockIssues.length && lockErrors.length
      ? th.flowEditor.blockedLocked
      : check.kind === "failed"
        ? th.flowEditor.validationFailed
        : check.kind !== "done"
        ? th.flowEditor.blockedChecking
        : errors.length
          ? th.flowEditor.blockedErrors
          : stillAsLoaded && !view.loadError
            ? th.flowEditor.blockedClean
            : null;

  const setStages = (next: Stage[] | null) => {
    if (next) edit(withStages(draft, trackKey, next));
  };

  const doMove = (id: string, dir: -1 | 1, announceIt = true): boolean => {
    const block = moveBlock(stages, metaFor, id, dir);
    if (block) {
      setAnnounce(blockText(block) ?? "");
      return false;
    }
    const next = moveStage(stages, id, dir);
    if (!next) return false;
    setStages(next);
    if (announceIt) setAnnounce(th.flowEditor.moved(next.findIndex((s) => s.id === id) + 1, total));
    return true;
  };

  const onHandleKey = (id: string) => (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (event.key === " " || event.key === "Enter") {
      event.preventDefault();
      if (picked?.id === id) {
        setPicked(null);
        setAnnounce(th.flowEditor.dropped(id, stages.findIndex((s) => s.id === id) + 1, total));
      } else {
        setPicked({ id, snapshot: current.stages });
        setAnnounce(th.flowEditor.picked(id));
      }
      return;
    }
    if (picked?.id !== id) return;
    if (event.key === "ArrowUp" || event.key === "ArrowDown") {
      event.preventDefault();
      doMove(id, event.key === "ArrowUp" ? -1 : 1);
      // focus ตามที่จับของขั้นเดิมหลัง re-render
      window.requestAnimationFrame(() =>
        document.querySelector<HTMLElement>(`[data-stage="${CSS.escape(id)}"] .flow-row__handle`)?.focus(),
      );
    } else if (event.key === "Escape") {
      event.preventDefault();
      const snap = new Map(picked.snapshot.map((x) => [x.id, x.after]));
      setStages(current.stages.map((x) => (snap.has(x.id) ? { ...x, after: snap.get(x.id) as string[] } : x)));
      setPicked(null);
      setAnnounce(th.flowEditor.cancelled(id));
    }
  };

  const onDragStart = (id: string) => (event: DragEvent<HTMLButtonElement>) => {
    event.dataTransfer.setData("text/plain", id);
    event.dataTransfer.effectAllowed = "move";
  };

  const onDrop = (targetId: string) => (event: DragEvent<HTMLLIElement>) => {
    if (!wide) return;
    event.preventDefault();
    const id = event.dataTransfer.getData("text/plain");
    if (!id || id === targetId || !stages.some((x) => x.id === id)) return;
    // ลากทีละขั้นด้วยกฎเดียวกับปุ่ม ↑↓ — หยุดที่ขั้นที่ล็อก (ลากข้ามขั้นที่ล็อกไม่ได้)
    let list = stages;
    const target = list.findIndex((s) => s.id === targetId);
    const dir: -1 | 1 = target < list.findIndex((s) => s.id === id) ? -1 : 1;
    for (let step = 0; step < total && list.findIndex((s) => s.id === id) !== target; step++) {
      const block = moveBlock(list, metaFor, id, dir);
      if (block) {
        setStages(list);
        setAnnounce(blockText(block) ?? "");
        return;
      }
      const next = moveStage(list, id, dir);
      if (!next) break;
      list = next;
    }
    setStages(list);
    setAnnounce(th.flowEditor.moved(list.findIndex((s) => s.id === id) + 1, total));
  };

  const goTo = (id: string, t?: string | null) => {
    const target = t ?? "full";
    if (draft.tracks[target]) setTrack(target);
    setSelected(id);
    window.requestAnimationFrame(() => document.querySelector<HTMLElement>(`[data-stage="${CSS.escape(id)}"] .flow-row__body`)?.focus());
  };

  const submit = async (extraAcks: string[] = []) => {
    if (blockReason || submitting) return;
    const acks = [...acknowledged, ...extraAcks.filter((c) => !acknowledged.includes(c))];
    const pendingAcks = (result?.ackRequired ?? []).filter((c) => !acks.includes(c));
    if (pendingAcks.length) {
      setUxPrompt({ before: lastClean.current ?? draft, codes: pendingAcks, fromSubmit: true });
      return;
    }
    setSubmitting(true);
    setSubmitError(null);
    try {
      // ส่งเฉพาะการยืนยันของคำเตือนที่ยังต้องยืนยันอยู่จริง (ack ที่ค้างจากการแก้ก่อนหน้าไม่ถูกส่ง)
      const send = acks.filter((c) => (result?.ackRequired ?? []).includes(c));
      await startChange(source.request(draft, send, view.base_sha256 ?? null, view.target), source.returnTo, JSON.stringify(draft, null, 2));
      navigate("/changes/review?step=review");
    } catch (error) {
      setSubmitError(error instanceof ApiError ? error.message : th.flowEditor.previewFailed);
    } finally {
      setSubmitting(false);
    }
  };

  const inspector = selectedStage ? (
    <StageInspector
      key={`${trackKey}:${selectedStage.id}`}
      stage={selectedStage}
      meta={metaFor(selectedStage.id)}
      lockedStageId={lockedStage}
      agents={agents}
      skills={skills}
      gates={view.options.gates}
      conditions={view.options.conditions}
      reserved={view.options.reservedAgents ?? []}
      moveUp={moveBlock(stages, metaFor, selectedStage.id, -1)}
      moveDown={moveBlock(stages, metaFor, selectedStage.id, 1)}
      reviewUrl={reviewUrl}
      onChange={(patch) => edit(updateStage(draft, trackKey, selectedStage.id, patch))}
      onMove={(dir) => doMove(selectedStage.id, dir)}
    />
  ) : (
    <p className="muted">{th.flowEditor.inspectorEmpty}</p>
  );

  const overrides = result?.overrides ?? view.overrides;
  const lockedFields = metaTracks?.[trackKey]?.lockedFields ?? [];

  return (
    <section className="flow-editor" aria-labelledby="flow-editor-title">
      <div className="flow-editor__head panel glass">
        <Link className="flow-head__back" to={source.backTo.href}>← {source.backTo.label}</Link>
        <h2 id="flow-editor-title" className="flow-head__name">{source.title}</h2>
        {source.notice ? source.notice(view) : null}
        <div className="flow-editor__bar">
          <div className="segmented" role="group" aria-label={th.flowEditor.trackLabel}>
            {tracks.map((t) => (
              <button key={t} type="button" className="segmented__item" aria-pressed={trackKey === t}
                      onClick={() => { setTrack(t); setSelected(null); setPicked(null); }}>
                {t}
              </button>
            ))}
          </div>
          <p className="flow-editor__source">{source.sourceLabel ? source.sourceLabel(overrides, view) : th.flowEditor.source(overrides)}</p>
          <button type="button" className="button button--secondary" onClick={() => {
            setPicked(null);
            edit(draftFromFlow(view.defaultFlow));
            setNotice(th.flowEditor.resetDone);
          }}>
            <Icon name="reset" size={18} /> {th.flowEditor.reset}
          </button>
          <div className="flow-editor__primary">
            <button type="button" className="button button--primary" aria-disabled={Boolean(blockReason) || submitting}
                    aria-describedby={blockReason ? blockedId : undefined} onClick={() => void submit()}>
              {submitting ? th.flowEditor.reviewing : th.flowEditor.review}
            </button>
            {blockReason ? <p id={blockedId} className="hint">{blockReason}</p> : null}
          </div>
        </div>
        {notice ? <p className="banner banner--ok" role="status"><Icon name="check" size={18} /> <span>{notice}</span></p> : null}
        {submitError ? <p className="banner banner--danger" role="alert"><Icon name="alert" size={18} /> <span>{submitError}</span></p> : null}
        {!editable ? (
          <p className="banner banner--danger" role="alert"><Icon name="alert" size={18} /> <span>{view.notEditableReason ?? th.flowEditor.notEditable}</span></p>
        ) : null}
        {(loadedLockIssues.length || view.loadError) && editable && (stillAsLoaded || lockErrors.length) ? (
          <div className="banner banner--danger flow-editor__invalid" role="alert">
            <Icon name="alert" size={18} />
            <div>
              <p className="banner__title">{view.loadError ? th.flowEditor.loadedBrokenTitle : th.flowEditor.loadedInvalidTitle}</p>
              <ul>
                {(view.issues ?? []).slice(0, 8).map((i, n) => (
                  <li key={n}>{i.stage ? <span className="mono">{i.stage}: </span> : null}{i.error}</li>
                ))}
              </ul>
              {view.policyFix ? (
                <button type="button" className="button button--primary" onClick={() => {
                  edit(draftFromFlow(view.policyFix!));
                  setNotice(th.flowEditor.fixApplied);
                }}>
                  {th.flowEditor.fixPolicy}
                </button>
              ) : null}
            </div>
          </div>
        ) : null}
        {(view.warnings ?? []).length && stillAsLoaded ? (
          <div className="banner banner--warn flow-editor__existing" role="note">
            <Icon name="alert" size={18} />
            <div>
              <p className="banner__title">{th.flowEditor.existingWarnings}</p>
              <ul>
                {(view.warnings ?? []).slice(0, 8).map((w, n) => <li key={n}>{w.error}</li>)}
                {(view.warnings ?? []).length > 8 ? <li>{th.flowEditor.moreItems((view.warnings ?? []).length - 8)}</li> : null}
              </ul>
            </div>
          </div>
        ) : null}
        {lockedFields.map((f) => (
          <p key={f.field} className="banner flow-editor__entry">
            <Icon name="lock" size={18} />
            <span>{th.flowEditor.entryLocked} <span className="mono">{f.field}</span>: {f.reason}</span>
          </p>
        ))}
      </div>

      <div className="flow-editor__cols">
        <div className="flow-editor__list surface-solid">
          <ol className="flow-steps" aria-label={th.flowEditor.stagesLabel(trackKey)}>
            {stages.map((stage, index) => (
              <StageRowView
                key={stage.id}
                stage={stage}
                meta={metaFor(stage.id)}
                index={index}
                total={total}
                selected={selected === stage.id}
                picked={picked?.id === stage.id}
                wide={wide}
                errors={stageErrors(stage.id)}
                nextEnabled={stage.enabled ? null : nextEnabledAfter(stages, stage.id)}
                hasFrontend={view.hasFrontend}
                reviewUrl={reviewUrl}
                reserved={view.options.reservedAgents ?? []}
                onSelect={() => setSelected(stage.id)}
                onToggle={(enabled) => edit(updateStage(draft, trackKey, stage.id, { enabled }))}
                onHandleKey={onHandleKey(stage.id)}
                onDragStart={onDragStart(stage.id)}
                onDrop={onDrop(stage.id)}
              />
            ))}
          </ol>
          <p className="sr-only" aria-live="assertive" role="status">{announce}</p>
        </div>
        {wide ? (
          <aside className="flow-editor__side">
            <section className="surface-solid flow-editor__inspector" aria-label={selectedStage ? th.flowEditor.inspectorTitle(selectedStage.id) : th.flowEditor.inspectorEmpty}>
              {selectedStage ? <h3 className="section-title mono">{th.flowEditor.inspectorTitle(selectedStage.id)}</h3> : null}
              {inspector}
            </section>
            <ValidationPanel check={check} onGo={goTo} compact={false} open onToggle={() => undefined} onRetry={() => setRetry((n) => n + 1)} />
          </aside>
        ) : null}
      </div>
      {!wide ? (
        <ValidationPanel check={check} onGo={goTo} compact open={panelOpen} onToggle={() => setPanelOpen((o) => !o)}
                         onRetry={() => setRetry((n) => n + 1)} />
      ) : null}
      {!wide && selectedStage ? (
        <Sheet title={th.flowEditor.inspectorTitle(selectedStage.id)} onClose={() => setSelected(null)}>
          {inspector}
        </Sheet>
      ) : null}
      {uxPrompt ? (
        <ConfirmWarning
          codes={uxPrompt.codes}
          warnings={result?.warnings ?? []}
          onCancel={() => {
            if (!uxPrompt.fromSubmit) {
              declined.current = uxPrompt.before;
              setDraft(uxPrompt.before);
            }
            setUxPrompt(null);
          }}
          onConfirm={() => {
            const confirmed = uxPrompt;
            setUxPrompt(null);
            setAcknowledged((a) => [...a, ...confirmed.codes.filter((c) => !a.includes(c))]);
            // ยืนยันจากปุ่ม "ตรวจสอบ" = ไปต่อที่ DiffPreview ทันที ไม่ต้องกดซ้ำ (review 4-2 N2)
            if (confirmed.fromSubmit) void submit(confirmed.codes);
          }}
        />
      ) : null}
    </section>
  );
}
