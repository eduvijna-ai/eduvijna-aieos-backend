# AIEOS360-S01-I03 — Student assignment consumption + attempt API

Authoritative **Student OS current-assignment** reads and **LearnerAttempt**
start / save / submit application + HTTP. Governing architecture:
**ADR-AIEOS-058 Frozen / Approved / Merged / Closed**. Prerequisites: S01-I01
(membership façade) and S01-I02 (Learning attempt/submission SoR).

This slice does **not** authorize S01-I04, production NATS Learning PUB, AI
grading, Temporal, MCP, `max_attempts`, or `ABANDONED`.

## Constitutional position

| Concept | Status in this slice |
| --- | --- |
| TeachingAssignment | Remains Teaching SoR |
| LearnerAttempt / LearnerSubmission | Remain Learning SoR |
| Cross-domain PostgreSQL FK | **None** |
| Composed command transaction | `SqlAlchemyStudentLearningCommandUnitOfWork` |
| Membership | Current School Context check **before** the DB transaction |
| Lock order | TeachingAssignment `get_for_update` **then** LearnerAttempt `get_for_update` |
| Learner-safe projection | Positive allowlist only |
| S01 attempts | **ONE ATTEMPT ONLY** |
| Learning NATS publication | **HOLD** — `PRODUCTION_EVENT_PUBLISH_PREFIXES` stays content + teaching |
| Outbox facts | `attempt.started.v1` and `attempt.submitted.v1` only (no SAVE event) |
| ClassroomAssessment | Unchanged |
| PrincipalKind.STUDENT | Absent (HUMAN principals) |

## Membership race (no 2PC)

Current membership is observed **outside** the local PostgreSQL transaction.
AIEOS does **not** claim atomic ERP↔AIEOS ordering. An already-authorized
local command **may still commit** after the external authority later denies
the learner. The next fresh command observes the denial and fails closed.
UUID possession is never authority.

## Learner-safe projection

`project_learner_resource` parses `WorksheetV1` / `QuizV1` / `HomeworkV1` for
`education.worksheet@1`, `education.quiz@1`, and `education.homework@1` only.
It constructs a DTO with content identities, title, learning objectives
`{id,text}`, instructions, and questions `{id,prompt,question_type,options}`.
Answers, explanations, teacher notes/summary, difficulty, bloom, and unknown
properties are omitted. Lesson plan, answer key, teacher notes, and unknown
type/schema/version fail closed. No `payload.model_dump` pass-through.

Exact assigned `ContentVersion` is used (not `published_version_id`). An
assigned V1 remains V1 after a later V2 publication.

## Current assignments

Visibility: ACTIVE HUMAN + current membership ClassRefs + tenant +
`lifecycle_state = ACTIVE` + `available_from <= now`. Teacher ownership is
not used. `due_at` in the past remains consumable while ACTIVE. CLOSED and
CANCELLED are not current. Attempt summary is a **read-only derivation**:
`NOT_STARTED` / `IN_PROGRESS` / `SUBMITTED`. Pagination: default 20, max 100.

## One-attempt start / save / submit

- START: copy assignment/content/class identities; `attempt_number = 1`;
  revision 0. IN_PROGRESS exists → conflict. SUBMITTED exists →
  `SecondAttemptNotAuthorized`. Idempotent replay of the original START is OK.
- SAVE: PUT replaces the complete working set. Validate against the exact
  learner projection. No outbox event. Empty set is allowed.
- SUBMIT: uses persisted working responses + I02
  `transition_in_progress_attempt_to_submitted`. Snapshots
  `assignment_revision_at_submit` and `due_at_at_submit` from the locked
  TeachingAssignment. Past `due_at` does **not** reject. Incomplete/empty
  responses are not auto-filled. No grade/score/mastery.

Idempotent replay: no new mutation, outbox, audit, or revision increment.

## HTTP

Prefix `/api/v1` (same as Teaching).

| Method | Path |
| --- | --- |
| GET | `/student-os/home` |
| GET | `/student-os/assignments` |
| GET | `/student-os/assignments/{assignment_id}` |
| POST | `/learning/assignments/{assignment_id}/attempts` |
| GET | `/learning/attempts/{attempt_id}` |
| PUT | `/learning/attempts/{attempt_id}/responses` |
| POST | `/learning/attempts/{attempt_id}/actions/submit` |

`principal_id = effective_actor_id = learner_principal_id`. Never accept
`learner_principal_id` from the body. Another learner's attempt is concealed
as **404** (`attempt_not_found`), not 403. Wrong class membership on current
activity is not visible (assignment not found). SUBMITTED own attempts remain
readable after close or membership loss. IN_PROGRESS after membership removal
fails closed (no auto-delete, auto-submit, or auto-abandon).

## Audit vocabulary

Alembic `a360s010002` extends `security.audit_records` CHECKs only:

- `learning.attempt.start` (create: before NULL, after 0)
- `learning.attempt.save_responses` (increment)
- `learning.attempt.submit` (increment)

No new learning tables. Teaching/content schemas unchanged.

## Explicit non-goals (S01-I04+)

Production NATS Learning publisher permission, AI grading, Temporal, MCP,
Student Agent, `max_attempts`, `ABANDONED`, ClassroomAssessment learner
semantics, and ERP 2PC.
