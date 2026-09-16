# AIEOS360-S02-I01 — Principal School Scope Current Authority & Authorization Substrate

Implements the minimum server-side current-authority substrate for future
Principal / School Intelligence reads. Governing architecture:
**ADR-AIEOS-060 Frozen / Approved** (Founder / Product Architecture
**2026-09-16**; Chief Architect **ACCEPTED / PASS**).

This slice does **not** authorize School Intelligence aggregation, Principal
HTTP, Principal OS UI, a School Intelligence business table, mastery, teacher
ranking, learner identities, NATS, Temporal, or production mutation.

## Constitutional position

| Concept | Status in this slice | Where it lives |
| --- | --- | --- |
| Class / campus / roster / institutional master | External ERP / SIS / Admin School Context | Not an AIEOS table |
| Current Principal school ClassRef scope | Replaceable **read façade** | School Intelligence application port |
| Authenticated Principal OS user | ACTIVE **HUMAN** AIEOS Principal | `security.principals` |
| `PrincipalKind.PRINCIPAL` | **Not created** | Identity remains HUMAN |
| Exact capability | `school.intelligence.read` | School Intelligence application catalog |
| Teacher assignability | **Not reused** | Teaching `SchoolContextClassReader` |
| Learner Class membership | **Not reused** | Learning `SchoolContextLearnerMembershipReader` |
| School Intelligence business SoR | **Not created** | Derived read projection remains future |
| Principal HTTP / OpenAPI | **Not implemented** | OpenAPI digest unchanged |
| Production ERP adapter | **Deferred** | Unconfigured → fail closed |

Canonical call path:

```text
trusted tenant_id + principal_id
        ↓
current ACTIVE HUMAN Principal
        ↓
exact capability school.intelligence.read ALLOW
        ↓
distinct current Principal School Scope authority
        ↓
validated authorized ClassRef set
```

Not:

```text
Teacher assignable classes → Principal school scope
Learner membership → Principal school scope
JWT / header / query / body ClassRef list
Historical grant snapshot → continuing access
Empty unconfigured provider → school-wide ALLOW
```

## Exact capability

Canonical owner:
`aieos.domains.school_intelligence.application.ports`

```text
SCHOOL_INTELLIGENCE_READ = "school.intelligence.read"
AIEOS_SCHOOL_INTELLIGENCE_CAPABILITIES = frozenset({SCHOOL_INTELLIGENCE_READ})
```

No wildcard. Unknown capability = DENY. Platform AuthorizationKernel adapters
compose this catalog; `decisions.py` does not own the string.

Production known-capability union (code-governed, not a DB catalog):

```text
Content | Assessment | Teaching Work | School Intelligence
```

This slice does **not** seed Principal users, roles, or capability-grant rows.
Actual ALLOW still depends on current stored membership/grant truth.

## HUMAN Principal gate

Reuses `CurrentPrincipalClassificationAuthority.require_current_human_principal`.
School Intelligence depends on a technology-neutral Protocol
(`HumanPrincipalClassificationGate`) and does not import SQLAlchemy.

- `PrincipalKind` remains exactly `HUMAN` / `WORKLOAD`
- WORKLOAD = fail closed before scope is exposed
- inactive / missing / NULL-classification = fail closed using existing
  security semantics
- JWT/headers never supply `PrincipalKind`

## Distinct School Scope Current Authority

```
src/aieos/domains/school_intelligence/application/school_scope.py
  AuthorizedSchoolClassRef
  SchoolContextPrincipalScopeReader.list_current_authorized_classes
  CurrentPrincipalSchoolScopeService.current_authorized_classes
  UnconfiguredSchoolContextPrincipalScopeReader
```

Owning package: **School Intelligence**
(`aieos.domains.school_intelligence.application`). Teaching and Learning
school-context ports remain unchanged and are not imported.

`AuthorizedSchoolClassRef` fields:

- `class_ref: str` — opaque external School Context identity
- `display_label: str`

No learner identities, learner names, learner counts, teacher scores, ranking,
mastery, assessment outcomes, attendance, fees, or ERP ownership fields.

Provider unavailable / malformed / blank ClassRef / blank label / duplicate
ClassRef → fail closed. A valid empty provider result means the Principal
currently has no authorized ClassRefs and is distinct from provider failure.

Every future School Intelligence read re-evaluates current authority. No cache.

## Errors

School Intelligence-owned (do not import Teaching or Learning errors):

- `SchoolIntelligenceCapabilityForbidden`
- `SchoolContextUnavailable`
- `SchoolContextContractError`

## NON_PRODUCTION adapter

```
src/aieos/development/principal_school_context.py
```

Deterministic synthetic HUMAN Principal + opaque `class-6a` / `class-6b`.
Marked `NON_PRODUCTION`. Must never be imported by production runtime
composition. Not roster master. Contains no learner identities and no
production credentials.

## Explicitly not in I01

- Principal School Intelligence aggregation
- Principal dashboard / Frontend Principal OS
- `/api/v1/principal-*` or any new HTTP route
- OpenAPI change
- database migration / School Intelligence table
- teacher ranking / surveillance
- learner-sensitive outcome aggregates
- mastery / Learner Intelligence
- Parent Intelligence
- real ERP/SIS adapter
- NATS / Temporal / Agent / MCP
- production deployment / cloud mutation
