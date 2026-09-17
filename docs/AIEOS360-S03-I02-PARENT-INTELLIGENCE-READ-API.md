# AIEOS360-S03-I02 — Derived Parent Intelligence Read Projection + GET API

Governing architecture: **ADR-AIEOS-061 Frozen / Approved v1.0.1**.

Prerequisite: **AIEOS360-S03-I01 CLOSED** at merge
`5f969a5df9290edd01ed4c1228f3972e306d8a60` (current HUMAN adult +
`parent.intelligence.read` + current Parent Learner Access).

This slice implements the first Parent Intelligence HTTP read:

```text
GET /api/v1/parent-os/home
operation_id: parent_os_home_get

GET /api/v1/parent-os/children/{learner_principal_id}
operation_id: parent_os_child_get
tag: parent-os
```

The projection is:

* read-only
* derived-on-request (`DERIVED_ON_REQUEST`)
* current facts as of the request (`CURRENT_FACTS_AS_OF_REQUEST`)
* deterministic
* side-effect-free
* a cross-domain application/read projection

It is **not** a Parent Intelligence business SoR, materialized view, cached
access snapshot, evaluation product, mastery model, Parent Agent, or
AI-generated narrative.

I03 (Parent OS UI) and I04 are **not authorized**.

## I01 dependency

Every GET revalidates current authority through
`CurrentParentLearnerAccessService`. I02 does not recreate adult HUMAN
classification, exact-capability authorization, provider structure validation,
or learner data-subject integrity. There is no authorization cache, JWT child
list, or frontend child-list authority.

Adult principal identity comes only from trusted server-side request context.
Query, path, body, custom Parent header, and frontend role are not adult
identity.

## Home authorization / execution order

For `GET /api/v1/parent-os/home`:

1. resolve trusted tenant + adult principal
2. invoke I01 `CurrentParentLearnerAccessService`
3. I01 therefore revalidates adult ACTIVE HUMAN, exact
   `parent.intelligence.read`, current adult→learner access, and every
   returned learner subject
4. sort / preserve deterministic authorized learner set
5. enforce implementation protection bounds without truncation
6. only then read learner current-fact inputs
7. build the positive-allowlist Parent projection
8. return HTTP 200

Never query Parent child facts before current access authority succeeds.

## Selector authorization / execution order

For `GET /api/v1/parent-os/children/{learner_principal_id}`:

1. trusted tenant + adult principal
2. resolve and fully validate the CURRENT authorized learner set through I01
3. compare the requested learner UUID against that validated set
4. if absent: concealment 404 — **do not** probe the guessed Principal,
   PrincipalKind, PrincipalStatus, tenant membership, class membership,
   assignments, attempts, or submissions
5. only if present: read current facts for that authorized learner
6. return a one-child `ParentIntelligenceReadModel`

The 404 decision is set-membership alone.

## Zero authorized children

Successful current access with zero authorized learners returns HTTP 200
`children: []`. Downstream membership / assignment / content readers are
**not** invoked. Empty successful authority is not the same as unconfigured
Parent access (503).

## 404 concealment

`ParentLearnerNotFound` maps to one generic RFC 9457 404:

* HTTP 404
* `code`: `parent_learner_not_found`
* `title`: Parent learner not found
* `detail`: Parent learner was not found

The same representation is used for same-class unauthorized learner,
cross-class learner, unauthorized sibling, other school, other tenant,
revoked / formerly authorized learner, and invented UUID. The response does
not say unauthorized, other tenant, revoked, unknown principal, or
"not your child".

## HTTP failure semantics

| Condition | HTTP |
| --- | --- |
| Authentication failure | existing 401 |
| Tenant / adult / current security authorization failure | 403 |
| Parent capability DENY | 403 |
| Valid trusted context, selector not in current authorized set | 404 conceal |
| Parent access authority unavailable / contract invalid | 503 |
| Learner integrity authority unavailable | 503 |
| Current membership source unavailable / invalid | 503 |
| Parent fact projection source unavailable / corrupt | 503 |
| Implementation protection capacity exceeded | 503 |
| Successful zero authorized learners | 200 `children=[]` |
| Authorized learner | 200 |

## Positive-allowlist DTO

Application and HTTP models are constructed field-by-field. They are not
`dict(source)`, ORM rows, Student DTOs, TeachingAssignment DTOs,
LearnerAttempt DTOs, or LearnerSubmission DTOs.

Top level exactly: `generated_at`, `projection_mode`, `time_window`, `children`.

`time_window` exactly: `mode`, `start`, `end`.

Child exactly: `learner_principal_id`, `assignments`.

Assignment exactly: `assignment_id`, `title`, `content_type`, `available_from`,
`due_at`, `attempt_status`, `submitted_at`.

I02 values:

* `projection_mode` = `DERIVED_ON_REQUEST`
* `time_window.mode` = `CURRENT_FACTS_AS_OF_REQUEST`
* `time_window.start` = `null`
* `time_window.end` = `generated_at`

One timezone-aware UTC observation timestamp is captured per request and used
for `available_from` visibility, `generated_at`, and `time_window.end`.

## ParentIntelligenceFactsReader

```text
read_authorized_learner_facts(
    tenant_id,
    authorized_learner_principal_ids,
    observed_at,
) -> ParentIntelligenceFactsSnapshot
```

The port receives only learner IDs that already passed I01. It does not decide
adult→learner entitlement and does not accept arbitrary client-selected
learner IDs.

A successful non-empty snapshot contains exactly one learner facts row per
requested authorized learner: no missing, extra, or duplicate rows. Assignment
IDs within a child must be unique. Mismatch is 503, not silent drop.

## Current learner membership is a fact source

After Parent access authorization succeeds, current learner Class membership
is consumed by the facts adapter via
`SchoolContextLearnerMembershipReader` /
`ListCurrentLearnerMembershipsService`.

This is **not** Parent access authority. Adult→learner permission is never
inferred from class membership, same class, same school, or same tenant.

Successful current membership of zero classes yields an authorized child card
with `assignments = []`. Membership provider unavailable or contract-invalid
is 503.

## Assignment current-visibility

For each currently authorized learner, expose only TeachingAssignments that
are current for that learner's current ClassRefs:

* tenant matches
* ClassRef is in successful current learner membership
* lifecycle = `ACTIVE`
* `available_from <= observed_at`

`CLOSED` and `CANCELLED` are not exposed. Future `available_from` is not
exposed. Past `due_at` does **not** remove an otherwise ACTIVE assignment.
Teacher ownership is not learner visibility. `class_ref` is not returned.

## Attempt status

Externally allowed values are exactly `NOT_STARTED`, `IN_PROGRESS`,
`SUBMITTED`.

* `NOT_STARTED`: current assignment exists and this learner has no attempt
* `IN_PROGRESS`: a current learner attempt exists in `IN_PROGRESS`;
  `submitted_at` is null
* `SUBMITTED`: a submitted attempt exists **and** its authoritative
  `LearnerSubmission` exists; `submitted_at` is the deterministic latest
  authoritative submission time

If a source row claims `SUBMITTED` without consistent immutable
`LearnerSubmission` evidence, the request fails closed (503). It is never
presented as `SUBMITTED` without submission evidence.

## Content title / type

Internally the adapter resolves the assigned `content_id` +
`content_version_id`. Externally it exposes only catalog `title` and
`content_type`. Payload, questions, prompts, instructions, objectives, answer
options, answer keys, and internal content IDs are not returned. Inconsistent
content/version reads fail closed.

## SUSPENDED / DISABLED data-subject rule

An ACTIVE HUMAN adult with exact Parent capability and current Parent access
may read a child whose `PrincipalStatus` is `SUSPENDED` or `DISABLED` when
current tenant membership facts remain valid. This does not change Student OS
actor ACTIVE-HUMAN rules.

## Strict forbidden disclosure

Parent HTTP must not expose other learner identities, class rosters,
`class_ref`, `school_learner_ref`, class counts, raw attempt responses,
submission snapshots, question IDs, item correctness, answer keys,
teacher notes, `PRIVATE_EXECUTION_NOTE`, ClassroomAssessment class-result
fields, Teacher Memory, Improve snapshots, `teacher_principal_id`, Principal
School Intelligence metrics, Teacher Assessment Intelligence `learners[]`,
system/model prompts, AI provenance, internal source arrays, mastery,
competency, learner level, diagnosis, risk, predictions, rankings, or
"behind peers". Teacher and Principal response DTOs are not reused.

`EVALUATION_EXISTENCE` remains `NO`. I02 does not query
`LearnerAssessmentEvaluation`, ClassroomAssessment, Teacher Assessment
Intelligence, or Principal School Intelligence.

## Production fail-closed composition

Production composes:

* `KernelParentIntelligenceAuthorization`
* `UnconfiguredSchoolContextParentLearnerAccessReader`
* `SecurityAuthorityLearnerPrincipalIntegrity`
* `SqlAlchemyParentIntelligenceFactsReader` with
  `UnconfiguredSchoolContextLearnerMembershipReader`

Until separately authorized real School Context / ERP integration exists,
Parent access remains unconfigured. Development adapters are never selected
silently in production.

## NON_PRODUCTION composition

Tests/dev may explicitly compose `DevelopmentSchoolContextParentLearnerAccessReader`,
Development Parent exact-capability permit, current HUMAN adult classification,
`SecurityAuthorityLearnerPrincipalIntegrity`, NON_PRODUCTION current learner
membership mapping, real local PostgreSQL Teaching/Learning/Content stores, and
the Parent facts reader. This is not ERP/SIS production integration. Synthetic
Parent learner IDs, if given membership, use the existing explicit membership
map. No presentation label is introduced.

## Resource protection / no truncation

Operational implementation-protection limits (not educational domain rules):

* `MAX_AUTHORIZED_LEARNER_COUNT` = 100
* `MAX_CLASS_REFS_PER_LEARNER` = 100
* `MAX_ASSIGNMENTS_PER_LEARNER` = 100

Exceeding a limit fails safely as 503 Parent Intelligence temporarily
unavailable. Partial / truncated bodies are not returned.

## Deterministic ordering

* Children: `learner_principal_id` UUID byte order (same technical ordering as
  I01). This is not performance, priority, risk, or importance ranking.
* Assignments: `assignment_id` UUID byte order. This is not due-date priority
  or educational ranking.

## Side-effect-free GET

The SQL adapter opens a REPEATABLE READ transaction, sets the tenant GUC, reads,
and **rolls back**. No commit, outbox, audit mutation, ensure-evaluation, or
Parent persistence.

## No persistence / no events / no AI

Alembic unique head remains `a360s010004`. No Parent table, access snapshot
table, or relationship table. No NATS subject, outbox event, Temporal workflow,
Parent Agent, LLM call, generated narrative, notifications, or PTM workflow.

## I03 / I04

Not authorized. No Parent page or frontend change in I02.
