# AIEOS360-S01-I02 — Learning Attempt + Submission Persistence

Establishes durable **Learning SoR** authority for learner attempts. Governing
architecture: **ADR-AIEOS-058 Frozen / Approved / Merged / Closed**. Prerequisite:
AIEOS360-S01-I01 learner-membership substrate (merged).

This slice does **not** authorize S01-I03, Student HTTP, eligibility orchestration,
or event publication.

## Constitutional position

| Concept | Status in this slice |
| --- | --- |
| Owning domain | **Learning** |
| `LearnerAttempt` | Durable aggregate SoR |
| `AttemptResponseItem` | Typed relational working state |
| `LearnerSubmission` | Distinct immutable evidence snapshot |
| PostgreSQL schema | `learning` with tenant RLS |
| S01 lifecycle | `IN_PROGRESS` → `SUBMITTED` (terminal) |
| S01 business attempts | **ONE ATTEMPT ONLY** |
| Persistence cardinality | Future-capable: unique `(tenant, learner, assignment, attempt_number)` + partial unique one `IN_PROGRESS` |
| `due_at_at_submit` | Persisted nullable snapshot |
| generic `late` Boolean | **Not** persisted |
| Student HTTP / `/api/v1/student-os/*` | **Not** added |
| Authoritative start/submit command | **Not** implemented (S01-I03) |
| Membership / assignment eligibility | **Not** implemented (S01-I03) |
| Learning NATS publication | **NO** — production PUB HOLD under ADR-AIEOS-046R1 unchanged |
| Outbox facts | Deferred to S01-I03 |
| AI / Temporal / MCP / Student Agent | **Not** introduced |
| ClassroomAssessment / TeachingAssignment | **Unchanged** |
| Cross-domain PostgreSQL FKs | **None** |

## Tables

- `learning.attempts`
- `learning.attempt_response_items`
- `learning.submissions`

Tenant predicate: `tenant_id = learning.current_tenant_id()`. Missing
`aieos.tenant_id` fails closed.

Alembic: `a360s010001` (down_revision `tosd100001`). Empty Learning schema may
downgrade; any Learning row fails closed.

## Attempt lifecycle

New attempts start `IN_PROGRESS` at `aggregate_revision = 0`. `SUBMITTED` is
terminal. There is no `ABANDONED`, `GRADED`, `MASTERED`, or `CANCELLED` state in
S01. No reopen, abandon, or business DELETE.

The persistence schema can represent `attempt_number > 1` after a prior attempt
is `SUBMITTED`. That is **PERSISTENCE CAPABILITY ONLY — NOT S01 BUSINESS
AUTHORIZATION**. I02 does not add a second-attempt command, `max_attempts`, or
retry policy.

## Typed responses

S01 kinds: `MULTIPLE_CHOICE`, `SHORT_ANSWER`, `TRUE_FALSE`. Exactly one matching
value. Mutable only while the parent attempt is `IN_PROGRESS`. A material save
increments parent `aggregate_revision` **once**. Database trigger rejects
response INSERT/UPDATE/DELETE when the parent is not `IN_PROGRESS`.

Working state is relational rows, not a mutable mega-JSON document.

## Immutable submission

`LearnerSubmission.response_snapshot` is a canonical JSON array of
`{question_id, response_kind, value}` sorted by `question_id`. It must not embed
raw ContentVersion payload, answer keys, teacher notes, score, grade, mastery, or
AI output.

UPDATE and DELETE on `learning.submissions` are rejected at the database,
including the privileged schema-owner path. No correction/resubmit rewrite.

`attempts.submission_id` is kept consistent by the domain/repository; the
same-domain FK is submission → attempt (`ON DELETE RESTRICT`) to avoid a
circular write requirement. Tests prove a `SUBMITTED` attempt always references
its persisted submission.

## Pure submit transition

`transition_in_progress_attempt_to_submitted` is a **pure/internal Learning
transition**. It does **not** perform current membership, ACTIVE HUMAN Principal
checks, TeachingAssignment ACTIVE / `available_from` validation, or assignment
row serialization. Those belong to S01-I03.

## Optimistic concurrency

LearnerAttempt mutations use `expected_aggregate_revision` compare-and-set.
Stale revision → typed `AttemptConcurrencyConflict` and zero mutation.

Response save and submit persist atomically inside **one Learning transaction**.
TeachingAssignment lock/order is **not** implemented in I02. S01-I03 must compose:

```text
TeachingAssignment authority
        → LearnerAttempt authority
        → LearnerSubmission
```

on one correctly ordered transaction. Repositories accept a SQLAlchemy
`Connection` for that future composition. `SqlAlchemyLearningUnitOfWork` does
not import Teaching UoW.

## Privileges

Runtime: attempts SELECT/INSERT/UPDATE (no DELETE); response items
SELECT/INSERT/UPDATE/DELETE while parent `IN_PROGRESS`; submissions
SELECT/INSERT (no UPDATE/DELETE).

## Explicit non-goals (S01-I03+)

Student HTTP, learner-safe Content projection, TeachingAssignment student-list
orchestration, ERP integration, AI grading, score/grade/mastery, ClassroomAssessment
learner IDs, Student Agent, MCP, Temporal, NATS Learning publisher permission,
Idempotency-Key start/submit API, and production deployment.
