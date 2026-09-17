# AIEOS360-S03-I01 — Parent Learner Access Current Authority & Authorization Substrate

Implements the minimum server-side current-authority substrate that answers:

**Which learner Principals may this current HUMAN adult Principal access NOW
in this tenant?**

Governing architecture: **ADR-AIEOS-061 Frozen / Approved v1.0.1**
(AIEOS360-S03P3F). This slice does **not** authorize Parent Intelligence
aggregation, Parent HTTP, Parent OS UI, a Parent business table, presentation
labels, evaluation disclosure, NATS, Temporal, Parent Agent, LLM, messaging,
real ERP/SIS integration, or I02–I04.

## Constitutional position

| Concept | Status in this slice | Where it lives |
| --- | --- | --- |
| Family / guardian / custody / household / enrollment / roster master | External ERP / SIS / Admin School Context | Not an AIEOS table |
| Current adult→learner access | Replaceable **read façade** | Parent Intelligence application port |
| Authenticated Parent OS adult | ACTIVE **HUMAN** AIEOS Principal | `security.principals` |
| `PrincipalKind.PARENT` / `GUARDIAN` / `STUDENT` / `LEARNER` | **Not created** | Identity remains HUMAN / WORKLOAD |
| Exact capability | `parent.intelligence.read` | Parent Intelligence application catalog |
| Teacher assignability | **Not reused** | Teaching `SchoolContextClassReader` |
| Learner Class membership | **Not reused** | Learning `SchoolContextLearnerMembershipReader` |
| Principal School Scope | **Not reused** | School Intelligence ClassRef port |
| Parent Intelligence business SoR | **Not created** | Derived read remains future |
| Parent HTTP / OpenAPI | **Not implemented** | OpenAPI digest unchanged |
| Production ERP adapter | **Deferred** | Unconfigured → fail closed |
| Learner presentation | **Not implemented** | Authorization ≠ presentation |

Canonical call path:

```text
trusted tenant_id + adult_principal_id
        ↓
current ACTIVE HUMAN adult Principal
        ↓
exact capability parent.intelligence.read ALLOW
        ↓
distinct current Parent Learner Access authority
        ↓
validate provider structure
        ↓
validate every learner data-subject (HUMAN, tenant-compatible)
        ↓
complete validated current set (deterministic UUID order)
```

Not:

```text
JWT / header / query / body / frontend Parent flag → learner set
Teacher classes → Parent children
Learner membership enumeration → Parent children
Principal School Scope → Parent children
Unconfigured provider → successful empty set
Historical grant snapshot → continuing access
```

## Exact capability

Canonical owner:
`aieos.domains.parent_intelligence.application.ports`

```text
PARENT_INTELLIGENCE_READ = "parent.intelligence.read"
AIEOS_PARENT_INTELLIGENCE_CAPABILITIES = frozenset({PARENT_INTELLIGENCE_READ})
```

No wildcard. No `parent.*`. No `parent.intelligence.*`. No `*.read`.
Unknown Parent capability = DENY. Platform AuthorizationKernel adapters
compose this catalog; `decisions.py` does not own the string.

Production known-capability union (code-governed, not a DB catalog):

```text
Content | Assessment | Teaching Work | School Intelligence | Parent Intelligence
```

This slice does **not** seed Parent users, roles, or capability-grant rows.
Actual ALLOW still depends on current stored membership/grant truth.

## Adult ACTIVE HUMAN gate

Reuses `CurrentPrincipalClassificationAuthority.require_current_human_principal`
through a technology-neutral Protocol (`HumanPrincipalClassificationGate`).
Parent Intelligence application code does not import SQLAlchemy.

- `PrincipalKind` remains exactly `HUMAN` / `WORKLOAD`
- WORKLOAD adult = fail closed **before** the access provider is invoked
- inactive / missing / NULL-classification adult = fail closed
- JWT / headers / frontend role never supply Parent actor semantics

## Distinct Parent Learner Access Current Authority

```
src/aieos/domains/parent_intelligence/application/learner_access.py
  AuthorizedLearnerAccess
  SchoolContextParentLearnerAccessReader.list_current_authorized_learners
  CurrentParentLearnerAccessService.current_authorized_learners
  UnconfiguredSchoolContextParentLearnerAccessReader
```

Owning package: **Parent Intelligence**
(`aieos.domains.parent_intelligence.application`). School Intelligence,
Teaching, Learning, and Assessment ports remain unchanged and are not
imported.

`AuthorizedLearnerAccess` fields:

- `learner_principal_id: UUID`

No presentation label, display name, legal name, email, username,
relationship type, guardian type, custody type, school learner ref, or
class ref. Missing presentation cannot affect authorization.

## Learner actor vs data-subject

Adult actor lifecycle and learner data-subject integrity are distinct.

I01 **does not** call `require_current_human_principal` or
`resolve_current_principal_kind` for the child. Those methods enforce ACTIVE
Principal actor semantics.

`LearnerPrincipalIntegrityAuthority.validate_learner_subject` (platform:
`SecurityAuthorityLearnerPrincipalIntegrity`) reuses the Security Authority
repository and requires:

- canonical Principal row exists
- `principal_kind == HUMAN` (not NULL, not WORKLOAD)
- learner is currently associated with the requested tenant using existing
  membership status / expiry / revocation semantics
- authority data is structurally readable

It does **not** require the learner Principal to be ACTIVE.

- SUSPENDED HUMAN learner + otherwise valid current tenant/access facts →
  accepted as data subject
- DISABLED HUMAN learner + otherwise valid current tenant/access facts →
  accepted as data subject

Student OS actor authorization is unchanged. A SUSPENDED/DISABLED learner
still cannot act if Student OS actor rules deny them. SUSPENDED/DISABLED is
not interpreted as custody revoked, family access revoked, transfer, or
Parent entitlement revoked. ERP/SIS current access authority controls
adult→learner revocation.

A learner returned by the provider who is unknown, WORKLOAD, NULL-kind,
duplicate, or not currently tenant-associated makes the **entire** provider
result contract-invalid. No silent drop, deduplicate, truncate, or coerce.

## Empty vs unavailable

| Outcome | Meaning |
| --- | --- |
| successful `()` | Provider currently authorizes zero learners. Truthful zero-child set. |
| `ParentLearnerAccessUnavailable` | Provider unconfigured, failed, or unavailable. Not an empty set. |

Production default is `UnconfiguredSchoolContextParentLearnerAccessReader`.
It raises `ParentLearnerAccessUnavailable` and must not return `()`.

## Errors

Parent Intelligence-owned (no HTTP codes in I01):

- `ParentIntelligenceCapabilityForbidden`
- `ParentLearnerAccessUnavailable`
- `ParentLearnerAccessContractError`

HTTP mapping belongs to I02 (not authorized here).

## NON_PRODUCTION adapter

```
src/aieos/development/parent_learner_access.py
```

Deterministic synthetic HUMAN adults and learner children. Marked
`NON_PRODUCTION`. No network access. No presentation labels. Tenant-scoped
and adult-principal-scoped. Mutable current grants so tests can prove
revocation without cache. Must never be imported by production runtime
composition. Not ERP/SIS integration.

## Production composition

Production `compose_api_runtime_dependencies` composes:

- `KernelParentIntelligenceAuthorization`
- `UnconfiguredSchoolContextParentLearnerAccessReader`
- `SecurityAuthorityLearnerPrincipalIntegrity`
- `CurrentParentLearnerAccessService`

The service is carried on `ApiRuntimeDependencies` for later I02 injection.
No Parent HTTP route is registered. OpenAPI is unchanged.

A production configuration flag must not silently select the synthetic
adapter. Only explicit development/test entrypoints may compose it
(`tools/dev/compose_local_api.py`).

## Explicitly not in I01

- `GET /api/v1/parent-os/home`
- `GET /api/v1/parent-os/children/{learner_principal_id}`
- any Parent HTTP route / Parent read DTO
- assignment, attempt, submission, or content-title Parent composition
- Parent UI / Frontend / presentation labels / display-name projection
- assessment/evaluation disclosure
- Parent Intelligence persistence / Parent business table
- family/guardian/custody/household SoR
- database migration
- OpenAPI contract change
- NATS / Temporal / Parent Agent / LLM / messaging / PTM
- real ERP/SIS integration
- I02, I03, I04
