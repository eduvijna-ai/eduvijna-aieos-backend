# AIEOS360-S04-I01 — Coherent NON_PRODUCTION School Context Current-Fact Provider

Classification: **NON_PRODUCTION**.

Governing architecture: **ADR-AIEOS-062 Frozen / Approved** v1.0.1
(AIEOS360-S04P3F). This slice is the **provider substrate** only. It does
**not** authorize Admin OS, Admin UI, a production ERP/SIS adapter, AIEOS
School / Class / Roster / Enrollment / Family mastery, NATS, Temporal,
OpenAPI change, migration, or S04-I02+.

## Constitutional position

| Concept | Status in this slice |
| --- | --- |
| Admin / ERP / SIS School Context | External **business master** (unchanged) |
| AIEOS School / Class / Roster / Enrollment / Family SoR | **Not created** |
| Four existing School Context ports | **Remain distinct** |
| Coherent current-fact provider | NON_PRODUCTION in-memory development substrate |
| Provider consolidation | Implementation only — **not** contract consolidation |
| Production School Context | Unconfigured / omitted / **fail closed** |
| `PrincipalKind` | HUMAN / WORKLOAD only — **no** `ADMIN` |
| `admin.*` capability | **Not created** |
| Admin UI / public mutation API | **Not created** |
| Production ERP/SIS adapter | **Not implemented** |
| NATS / Temporal | **Not introduced** |
| Alembic / OpenAPI | Unchanged (`a360s010004` / pin unchanged) |

AIEOS continues **not** to be the school / roster / family business master.

## Four ports remain distinct

One provider implementation may satisfy all four reader Protocols. That is
not a new giant application contract.

| Port | Existing contract | Question |
| --- | --- | --- |
| A. Teaching Class authority | `SchoolContextClassReader` / `SchoolContextClassAuthority` | Which ClassRefs may this teacher currently assign / teach / assess? |
| B. Learning membership | `SchoolContextLearnerMembershipReader` / `SchoolContextLearnerMembershipAuthority` | Is / where is this learner currently a member? |
| C. Principal school scope | `SchoolContextPrincipalScopeReader` / `CurrentPrincipalSchoolScopeService` | Which ClassRefs are currently in this HUMAN Principal's school scope? |
| D. Parent learner access | `SchoolContextParentLearnerAccessReader` / `CurrentParentLearnerAccessService` | Which learner Principals may this adult currently access? |

Do **not** create `SchoolContextService`, `UniversalSchoolContextPort`, or
`AdminSchoolService`. Domain-facing contracts are unchanged. Historical
disconnected development adapters remain for compatibility; this slice does
not perform a broad runtime composition migration.

Returned DTOs remain the existing:

- `AssignableClassRef`
- `CurrentLearnerClassMembership`
- `AuthorizedSchoolClassRef`
- `AuthorizedLearnerAccess`

## One shared fact universe

Module:

```
src/aieos/development/coherent_school_context.py
DevelopmentCoherentSchoolContextProvider
```

Minimum facts: one tenant, ClassRef definitions (`class_ref` +
`display_label`), teacher→ClassRef authority, learner→ClassRef membership
(optional opaque `school_learner_ref`), Principal→ClassRef scope, and
adult→learner Principal access.

Coherent development baseline (existing synthetic identities):

```text
Teacher  SYNTHETIC_PRINCIPAL_ID     → class-5a, class-5b
Learner  STUDENT_A_PRINCIPAL_ID     → class-5a
Learner  STUDENT_B_PRINCIPAL_ID     → class-5b
Principal PRINCIPAL_OS_HUMAN_PRINCIPAL_ID → class-5a, class-5b
Adult    PARENT_OS_HUMAN_ADULT_A_ID → STUDENT_A
Adult    PARENT_OS_HUMAN_ADULT_B_ID → STUDENT_B
```

Invariant: a ClassRef the configured Teacher can assign is the same ClassRef
the configured learner belongs to, the configured Principal can observe, and
the authorized adult's learner story participates in.

This provider is a **development surrogate** for the external-authority
contract. It is **not** “the ERP”.

## Current-fact semantics

Reads observe **current** facts. No authorization result is cached as durable
authority. A subsequent read after revocation or re-scope observes the new
facts.

Wrong tenant / unknown teacher / unknown learner / unknown Principal /
unknown adult → **no authority** (empty current set from this configured
development provider). Unavailable or malformed fixture state must not become
implicit ALLOW.

## Development-only mutation / control

Narrow fixture-control methods exist on the provider:

- `set_teacher_class_authority`
- `set_learner_membership`
- `set_principal_class_scope`
- `set_adult_learner_access`

These are **DEVELOPMENT PROVIDER CONTROL ONLY**. They are not AIEOS business
commands, Admin commands, ERP/SIS mutation APIs, HTTP APIs, or production
capabilities. No public route is authorized.

Rejected fixture mutations fail closed and leave the previous valid current
facts in place.

## Fail-closed validation

Malformed provider state is rejected (`CoherentSchoolContextFixtureError`),
not silently partially accepted:

- blank ClassRef
- duplicate ClassRef definition
- duplicate entries where the owning port requires uniqueness
- malformed required UUID identity
- unknown referenced ClassRef in a relationship
- adult access to malformed / unknown learner fixture identity
- cross-tenant reads (no authority)
- malformed optional external correlation
- conflicting class display definition

## Production remains fail closed

Production composition must not import
`aieos.development.coherent_school_context`. Production School Context
posture remains unconfigured / omitted / fail closed. This slice does **not**
implement a production provider.

Dependency direction:

```text
development provider → existing domain application contracts
```

never:

```text
domain → development implementation
```

## Explicitly not in I01

- Admin OS / Admin UI / role selector / fixture-control UI
- public Admin mutation API
- `PrincipalKind.ADMIN` / `admin.*` capability
- AIEOS School / Class / Roster / Enrollment / Family aggregates or tables
- production ERP/SIS adapter or HTTP client
- PostgreSQL School Context schema / Alembic migration
- NATS publisher/consumer
- Temporal workflow/activity
- OpenAPI change
- Frontend work
- S04-I02 / S04-I03
