# AIEOS360-S01-I01 — Learner Membership Façade + NON_PRODUCTION Student Substrate

Implements the minimum current-authority substrate for future Student assignment
eligibility. Governing architecture: **ADR-AIEOS-058 Frozen / Approved**.

This slice does **not** authorize S01-I02, Student HTTP, LearnerAttempt, or
LearnerSubmission.

## Constitutional position

| Concept | Status in this slice | Where it lives |
| --- | --- | --- |
| Class / enrollment / roster master | External ERP / SIS / Admin | Not an AIEOS table |
| Current learner Class membership | Replaceable **read façade** | Learning application port |
| Authenticated student | ACTIVE **HUMAN** AIEOS Principal | `security.principals` |
| `PrincipalKind.STUDENT` | **Not created** | Identity remains HUMAN |
| `school_learner_ref` | Optional opaque correlation metadata | Not authentication identity |
| Assignment-time roster snapshot | **Not implemented** | TeachingAssignment unchanged |
| Student HTTP / OpenAPI | **Not implemented** | OpenAPI digest unchanged |
| LearnerAttempt / LearnerSubmission | **Not implemented** | S01-I02+ |
| Production ERP adapter | **Deferred** | Unconfigured → fail closed |

Canonical call path:

```text
authenticated HUMAN learner Principal
        ↓
current learner Class membership façade
        ↓
require learner currently belongs to class_ref
```

Not:

```text
Teacher assignable classes → learner membership
Browser → ERP / SIS directly
Learning database → roster / student / enrollment SoR
```

## Port

```
src/aieos/domains/learning/application/learner_membership.py
  CurrentLearnerClassMembership
  SchoolContextLearnerMembershipReader.list_current_memberships
  SchoolContextLearnerMembershipAuthorityService.require_current_membership
  UnconfiguredSchoolContextLearnerMembershipReader
```

Owning package: **Learning** (`aieos.domains.learning.application`). Teaching is a
consumer of TeachingAssignment only and is not the learner-membership owner.

`require_current_membership` authorizes from:

```text
current membership result  ∩  requested class_ref
```

Absence → `LearnerClassMembershipDenied`. Provider unavailable / malformed /
blank ClassRef / duplicate ClassRef → fail closed. Cross-tenant data never
authorizes.

Membership is **check-time current authority**. S01 does not claim atomic
ERP↔AIEOS revocation ordering or 2PC/XA.

## Errors

Learning-owned (do not import Teaching errors):

- `LearnerClassMembershipDenied`
- `SchoolContextUnavailable`
- `SchoolContextContractError`

## NON_PRODUCTION adapter

```
src/aieos/development/learner_principals.py
src/aieos/development/learner_school_context.py
```

Deterministic, offline, tenant-scoped, learner-principal-scoped. Must never be
imported by production runtime composition.

Default synthetic mapping (same Teacher OS development tenant):

| Principal | Kind | Current membership |
| --- | --- | --- |
| Student A | HUMAN | `class-5a` |
| Student B | HUMAN | `class-5b` |

The Teacher OS development principal is **not** a learner member.

## Development authentication

Teacher OS continues to use `DevelopmentPrincipalAuthenticator` (fixed teacher
principal). A separate `DevelopmentMappedPrincipalAuthenticator` maps explicit
opaque bearer aliases (`dev-student-a` / `dev-student-b`) to preconfigured
PrincipalIds. Unknown token → unauthenticated. The bearer value is never a
Principal UUID. Production authentication is unchanged.

## Production composition

Production does not receive the synthetic learner adapter. Real ERP/SIS is not
configured in S01-I01. `UnconfiguredSchoolContextLearnerMembershipReader`
fails closed (`SchoolContextUnavailable`).

## Explicit non-goals (I01)

No Student OS routes, assignment listing, attempt/submission, roster table,
student table, migration, OpenAPI change, or ERP network integration.
Alembic remains `tosd100001`.
