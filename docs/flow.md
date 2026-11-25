# flow.yaml — flow ต่อโปรเจกต์ของ Veda

flow บอกว่าโปรเจกต์หนึ่งเดินผ่านขั้นใดบ้าง ใช้ agent/skill อะไร มี gate ตรงไหน และขั้นไหนถูกข้ามด้วยเหตุผลใด
orchestrator อ่านผ่าน `flow resolve <project>` (ดู [Resolver](#resolver-req-048); อ่านอย่างเดียว) — กฎทั้งหมดคำนวณใน `bmad_studio.core.flow` (AD-1)

- JSON Schema: [`docs/flow-schema.json`](flow-schema.json) (สร้างด้วย `uv run python -m bmad_studio.cli flow schema --out docs/flow-schema.json`)
- default ที่มากับแพ็กเกจ: `src/bmad_studio/defaults/flow.default.yaml`

## ไฟล์

| ไฟล์ | ความหมาย |
| --- | --- |
| `.veda/flow.default.yaml` | flow เริ่มต้นของ Veda (ถ้าไม่มี ใช้ default ที่มากับแพ็กเกจ) |
| `.veda/projects/<name>/flow.yaml` | overlay ต่อโปรเจกต์: `extends: default` + เฉพาะ field ที่ต่าง |

ทั้งสองไฟล์สร้าง/แก้ผ่าน write pipeline (preview + ยืนยัน) เท่านั้น; overlay สร้างโดย `to_overlay(effective, default)`

## Field ของ flow เต็ม

| field | ชนิด | ความหมาย |
| --- | --- | --- |
| `version` | `1` | รุ่นของ schema |
| `tracks` | mapping ชื่อ track → track | `full` (วงจรเต็ม) และ `quick` (งานเล็ก) ต้องมีทั้งคู่; track อื่นต้องมี `entry_condition` ที่ล็อกเหมือน quick |
| `tracks.<t>.entry_condition` | `{requirementsReady: bool, status: [str]}` | เงื่อนไขเข้า track — ของ `quick` ล็อกเป็น `requirementsReady: true`, `status: [developing, developed, running, deployed]`; `full` ห้ามมี |
| `tracks.<t>.stages[]` | รายการขั้น (1–64) | ตำแหน่งในรายการไม่มีผลต่อลำดับ ใช้แค่ tie-break |
| `stages[].id` | kebab-case ASCII ตัวเล็ก | ไม่ซ้ำใน track |
| `stages[].agent` | kebab-case | ชื่อไฟล์ `.claude/agents/<agent>.md` ที่มีจริง; `human` = คน (ใช้ได้เฉพาะ `gate: human`) |
| `stages[].skill` | kebab-case หรือไม่มี | โฟลเดอร์ `.claude/skills/<skill>/SKILL.md` ที่มีจริง |
| `stages[].enabled` | bool (ค่าตั้งต้น true) | `false` = ขั้นถูกข้าม (`skipReason`) ขั้นถัดไปถือว่าผ่านต่อได้ |
| `stages[].optional` | bool (ค่าตั้งต้น false) | ขั้นที่ทำหรือไม่ก็ได้ (devops) — resolver เสนอเป็น next เมื่อขั้นอื่นเสร็จหมด |
| `stages[].after` | รายการ stage id | ขั้นที่ต้องเสร็จ/ข้ามก่อน — **ลำดับมาจาก after เท่านั้น**; มีขั้นเริ่มต้น (after ว่าง) ได้ขั้นเดียว |
| `stages[].gate` | `none` \| `human` \| `agent-confirm` | ใครตัดสินว่าขั้นนี้ผ่าน |
| `stages[].produces` | รายการจาก `requirementsReady`, `designReady`, `planReady` | flag ของ registry ที่ขั้นนี้ทำให้เป็นจริง — ใช้ตัดสินว่าขั้นเสร็จ |
| `stages[].condition` | `hasFrontend` \| `requirementsReady` \| `designReady` \| `planReady` หรือไม่มี | ทำขั้นนี้เฉพาะเมื่อ field boolean นั้นของ registry เป็น true (อ่านอย่างเดียว) ไม่งั้นข้าม |

YAML ที่รับ: document เดียว, ไม่มี directive (`%YAML`/`%TAG`), anchor/alias (`&`/`*`), tag (`!...`), merge key (`<<`), key ซ้ำ,
ขนาด ≤ 256 KiB (นับเป็น UTF-8),
ซ้อนลึก ≤ 12 ชั้น; YAML 1.2 (`yes` เป็นข้อความ ไม่ใช่ bool) — ผิดข้อใด = error ไม่เดา

## Field ของ overlay

| field | ความหมาย |
| --- | --- |
| `extends` | ต้องเป็น `default` |
| `tracks.<t>.stages.<id>` | field ที่ต่างจาก default ของขั้นนั้น (ทุก field ของขั้น ยกเว้น `id`) — ลบ key = รีเซ็ตกลับ default |
| `tracks.<t>.add[]` | ขั้นใหม่ (รูปเดียวกับ `stages[]`) |
| `tracks.<t>.entry_condition` | มีได้ตาม schema แต่ค่าถูกล็อก — ต่างจากค่าล็อก = `locked_gate_violation` |

## กฎที่ validator ตรวจ (ผิด = error พร้อมขั้นและสาเหตุภาษาไทย)

| code | เมื่อ |
| --- | --- |
| `yaml_invalid` | YAML ผิดหรือใช้สิ่งที่ไม่รองรับข้างบน |
| `schema_invalid` | ผิด schema (key ไม่รู้จัก เช่น `include`, ชนิดผิด, id ไม่ใช่ kebab-case ASCII) |
| `duplicate_stage`, `duplicate_after`, `unknown_after` | id ซ้ำ / after ซ้ำ / after อ้างขั้นที่ไม่มี |
| `cycle`, `no_predecessor`, `unreachable` | ลำดับวน / มีขั้นเริ่มต้นเกินหนึ่ง / ไปไม่ถึงจากขั้นเริ่มต้น |
| `unknown_agent`, `unknown_skill` | agent/skill ไม่มีไฟล์ใน `.claude/` |
| `plan_gate_missing` | ขั้นที่ลงมือสร้าง (agent `developer` หรือ skill `bmad-build`/`bmad-build-auto`) ใน full ไม่มีขั้นที่ enabled, **ไม่มี condition** และ produces `planReady` มาก่อน |
| `locked_gate_violation` | ละเมิด human gate ที่ล็อก (ด้านล่าง) |
| `validator_missing` | write pipeline ไม่มี validator ของไฟล์ flow — ปฏิเสธไว้ก่อน (fail closed) |
| `unknown_stage`, `unknown_track` | overlay แก้ขั้น/track ที่ไม่มีใน default (ขั้นใหม่ใช้ `add`) |
| `cannot_remove_stage` | `to_overlay` ได้ effective ที่ไม่มีขั้นของ default — ใช้ `enabled: false` แทนการลบ |
| `cannot_remove_track` | `to_overlay` ได้ effective ที่ไม่มี track ของ default |
| `flow_unreadable` | อ่านไฟล์ flow ไม่ได้ (ไม่ใช่ไฟล์/ไม่มีสิทธิ์) |
| `default_invalid` | ตรวจ overlay ไม่ได้เพราะ `.veda/flow.default.yaml` ผิดกฎ (ไม่ fallback ไป default ของแพ็กเกจ) |

การเขียนไฟล์ flow (ทุก kind รวม revert) ตรวจตอน preview และ**ตรวจซ้ำตอน commit** ภายใต้ lock เดียวของไฟล์ flow ทั้งหมด —
ถ้า default/agent/skill เปลี่ยนระหว่าง preview กับ commit จนผิดกฎ commit คืน `has_errors` (422) และไม่เขียน (เปิดตัวอย่างใหม่);
แก้ default แล้วตรวจ overlay ของทุกโปรเจกต์ยังไม่ทำ (story 4-3)

`yaml_invalid` มี `reason` บอกสาเหตุละเอียด: `yaml_alias`, `yaml_anchor`, `yaml_tag`, `yaml_directive`, `yaml_merge_key`,
`yaml_multi_document`, `yaml_complex_key`, `yaml_duplicate_key`, `yaml_too_large`, `yaml_too_deep`, `yaml_invalid`;
`schema_invalid` แสดงไม่เกิน 50 จุดแล้วสรุปจำนวนที่เหลือ

## Resolver (REQ-048)

```
uv run --project C:/Users/thedu/code/bmad-studio python -m bmad_studio.cli flow resolve <project> [--track full|quick] [--json]
uv run --project C:/Users/thedu/code/bmad-studio python -m bmad_studio.cli flow validate [<project>] [--file PATH]
```

(entry point `bmad-studio` ใช้ได้เหมือนกันบนเครื่องที่ไม่บล็อก `.venv/Scripts/*.exe`; บนเครื่องนี้ Windows Application Control
บล็อก exe จึงใช้ `python -m bmad_studio.cli`.) ไม่ต้องรัน server, ไม่เขียนไฟล์ใด, อ่าน registry/state แบบอ่านอย่างเดียว

- exit `0` = สำเร็จ (`--json`: `{ok: true, next, stages, locked, entry, complete, hints, warnings, flow, registry}`)
- exit `2` = flow ผิด (`flow_invalid` + `issues` + `source`), `project_not_found`, `registry_unreadable`, `veda_root_missing`,
  `unknown_track`, `read_failed`, `invalid_arguments` — ไม่เดาผล
- `warnings`: `sprint_status_unreadable`, `story_status_unknown`, `quick_status_unknown`, `condition_unknown`
  (registry ไม่มี field ของ condition — ขั้นนั้น**ไม่ถูกข้าม**จนกว่าจะตั้งค่า)
- `next`: `{stage, agent, skill, gate, optional, status, note}` หรือ `null` (flow จบ / เข้า track ไม่ได้ตาม `entry`)

สถานะต่อขั้น (`done`/`active`/`pending`/`skipped` + `skipReason`):

| ขั้น | เสร็จเมื่อ |
| --- | --- |
| ปิด (`enabled: false`) หรือ `condition` เป็น boolean `false` | `skipped` พร้อมเหตุผล (registry ไม่มีค่า หรือค่าไม่ใช่ true/false เช่น `"true"`/`1`/`null` = ไม่ข้าม + คำเตือน `condition_unknown`) |
| `requirements-review` | `requirementsReady === true` (boolean) ใน registry เท่านั้น — `"true"`/`1` ไม่นับ; ขั้นก่อนหน้าเสร็จ = `active` (รอคนที่ `/review`) |
| ขั้นที่มี `produces` | ทุก flag ใน registry เป็น `true` |
| project-bootstrapper | มีใน registry |
| ba | มี `requirements.md` |
| developer | ทุก story ใน `sprint-status.yaml` เป็น review/done — สถานะอื่น (รวมที่ไม่รู้จัก) = ยังไม่เสร็จ (ไม่มี sprint-status: ดู `status` ของ registry) |
| reviewer (`review`) | ทุก story `done`; มี story `review` = `active` |
| reviewer (`retrospective`) | ทุก epic `done` และ `epic-N-retrospective: done`; epic ที่จบแล้วแต่ยังไม่มี retro = `active` (แม้ epic อื่นยังพัฒนา); ไม่มี epic หรือโปรเจกต์ที่พัฒนาแล้วโดยไม่มี sprint-status = `skipped` |
| devops | registry มี `dockerImage`/`containerName` หรือ `status: deployed` |
| quick: developer / reviewer | `quick/*.md` ที่ status in-progress/ready-for-dev/draft = งาน developer, review/in-review = งาน reviewer |

`next: null` + `complete: false` = flow ยังไม่จบแต่ไม่มีขั้นที่ทำต่อได้ (เช่น เข้า track ไม่ได้ หรือขั้นที่ไม่มีสัญญาณสถานะ) —
UI แสดง "ยังไม่มีขั้นที่ทำต่อได้" ไม่ใช่ "จบแล้ว"

ขั้นถัดไป = ขั้น `active` ที่ลึกสุดซึ่ง**บรรพบุรุษทุกขั้น**เสร็จ/ข้าม/active (ขั้น developer/reviewer ไม่บล็อกกันเอง; reviewer/retro
ก่อน developer เมื่อมีหลายงาน) ไม่งั้นขั้น `pending`
แรกที่ขั้นก่อนหน้าเสร็จ/ข้ามหมด (ขั้น optional เป็นทางเลือกสุดท้าย); ใน `full` ถ้า `requirementsReady` ไม่จริง next จะไม่เลย gate

## แก้ flow ผ่านแอป (story 4-1)

- `GET /api/flows/<project>` — effective flow ต่อ track; ทุกขั้นมี `locked`, `lockReason`, `overridden`, `overriddenFields`, `default`, `mustFollowLocked` (คำนวณใน `core.flow.editor`), `base_sha256` ของ overlay, `issues` + `policyFix` เมื่อไฟล์ที่โหลดผิด lock, `editable: false` เมื่อไฟล์ไม่ใช่ข้อความ
- `POST /api/flows/<project>/validate` `{flow}` — errors/warnings จาก `validate_flow` ชุดเดียวกับ resolver, overlay ที่จะเขียน, `ackRequired`
- บันทึก = `POST /api/changes/preview` kind `flow`, target `.veda/projects/<project>/flow.yaml`, payload `{flow, acknowledged?}` — ส่ง effective ที่แก้แล้ว **ไม่ใช่ overlay**; overlay สร้างจาก `to_overlay` เท่านั้น (เท่ากับ default = ย้าย overlay ไป backup)
- ปิดขั้นออกแบบ UI (ปิด, เปลี่ยน agent, ไม่ produces designReady, condition อื่น, ย้ายหลังขั้นลงมือสร้าง) ในโปรเจกต์ที่ `hasFrontend` ไม่ใช่ `false` = warning `ux_disabled_frontend` ที่ต้องส่งใน `acknowledged` ไม่งั้น `errors[ux_disable_unconfirmed]`
- ไฟล์เดิม ≤ 64 KiB แก้แบบรักษา comment (ruamel round-trip); comment ที่จะหาย = warning `comments_dropped`; รักษาไม่ได้ = เขียนใหม่ + warning `format_rewritten`

## Human gate ที่ล็อก (AD-8, REQ-047)

ค่าคงที่ในโค้ด (`core/flow/policy.py`) ไม่ใช่ใน yaml — แก้ yaml แบบใดก็เลี่ยงไม่ได้:

1. track `full` ต้องมีขั้น `requirements-review`: `agent: human`, `gate: human`, `enabled: true`, `optional: false`,
   `produces: [requirementsReady]`, ไม่มี `skill`/`condition` และต้องมาหลังขั้น agent `ba`
2. ทุกขั้นใน `full` ที่ enabled ต้องมี `requirements-review` เป็นบรรพบุรุษใน after — ยกเว้นขั้นก่อน gate ที่อนุญาต
   (allowlist): agent `project-bootstrapper` หรือ `ba` ที่ไม่ใช้ skill ลงมือทำ (`bmad-build`, `bmad-architecture`,
   `bmad-code-review` ฯลฯ ดู `GATED_SKILLS`) — ตรวจตาม agent/skill ไม่ใช่ id: ขั้นใหม่หรือ agent ใหม่ก็หลบไม่ได้
   (รวม ux-designer/architect/developer/reviewer ตาม AD-8); gate ต้องมาหลังขั้น `ba` ที่เปิดอยู่และไม่มี condition
3. ไม่มีขั้นอื่นที่ produces `requirementsReady`; agent `human` ใช้ได้เฉพาะ `gate: human`
4. `quick` ต้องมีอยู่ และทุก track ที่ไม่ใช่ `full` ต้องมี `entry_condition` เท่ากับค่าล็อก

ขั้น gate ผ่านเมื่อ `requirementsReady` ใน registry เป็น true เท่านั้น (ตั้งโดย app-dashboard หลังคนกด Approve ที่ `/review`);
bmad-studio ไม่เขียน `registry.json` เลย

## Default flow (เทียบกับ CLAUDE.md ของ veda)

ตารางนี้คือข้อกำหนดของ `defaults/flow.default.yaml` — `tests/test_flow_schema.py` ตรวจทุกแถวกับไฟล์จริง
(`-` = ไม่มี/ว่าง)

| track | id | agent | skill | after | gate | produces | condition | enabled | optional |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| full | bootstrap | project-bootstrapper | - | - | none | - | - | true | false |
| full | requirements | ba | - | bootstrap | none | - | - | true | false |
| full | requirements-review | human | - | requirements | human | requirementsReady | - | true | false |
| full | ux-design | ux-designer | - | requirements-review | none | designReady | hasFrontend | true | false |
| full | architecture | architect | bmad-architecture | ux-design | none | planReady | - | true | false |
| full | develop | developer | bmad-build | architecture | none | - | - | true | false |
| full | review | reviewer | bmad-code-review | develop | agent-confirm | - | - | true | false |
| full | retrospective | reviewer | bmad-retrospective | review | none | - | - | true | false |
| full | devops | devops | - | retrospective | none | - | - | true | true |
| quick | develop | developer | bmad-build | - | none | - | - | true | false |
| quick | review | reviewer | bmad-code-review | develop | agent-confirm | - | - | true | false |

ตรงกับ CLAUDE.md: project-bootstrapper → ba → human review (`/review`) → ux-designer (เฉพาะโปรเจกต์ที่มี UI)
→ architect (`bmad-architecture` → epics → sprint planning, ตั้ง `planReady`) → developer (`bmad-build`) ↔ reviewer
(`bmad-code-review`, ตั้ง `done`) ทีละ story → reviewer ทำ retro (`bmad-retrospective`) เมื่อจบ epic → devops (optional);
quick track: developer → reviewer เฉพาะโปรเจกต์ที่ `status` เป็น developing/developed/running/deployed
