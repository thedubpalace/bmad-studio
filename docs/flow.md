# flow.yaml — flow ต่อโปรเจกต์ของ Veda

flow บอกว่าโปรเจกต์หนึ่งเดินผ่านขั้นใดบ้าง ใช้ agent/skill อะไร มี gate ตรงไหน และขั้นไหนถูกข้ามด้วยเหตุผลใด
orchestrator อ่านผ่าน `bmad-studio flow resolve <project>` (อ่านอย่างเดียว) — กฎทั้งหมดคำนวณใน `bmad_studio.core.flow` (AD-1)

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
| `plan_gate_missing` | ขั้น developer ใน full ไม่มีขั้นที่ enabled และ produces `planReady` มาก่อน |
| `locked_gate_violation` | ละเมิด human gate ที่ล็อก (ด้านล่าง) |
| `validator_missing` | write pipeline ไม่มี validator ของไฟล์ flow — ปฏิเสธไว้ก่อน (fail closed) |
| `flow_overlay_unchecked` | (story 3-1 เท่านั้น) ยังตรวจ overlay ไม่ได้จนกว่าจะมี merge — ปฏิเสธการคืนค่า |

`yaml_invalid` มี `reason` บอกสาเหตุละเอียด: `yaml_alias`, `yaml_anchor`, `yaml_tag`, `yaml_directive`, `yaml_merge_key`,
`yaml_multi_document`, `yaml_complex_key`, `yaml_duplicate_key`, `yaml_too_large`, `yaml_too_deep`, `yaml_invalid`;
`schema_invalid` แสดงไม่เกิน 50 จุดแล้วสรุปจำนวนที่เหลือ

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
