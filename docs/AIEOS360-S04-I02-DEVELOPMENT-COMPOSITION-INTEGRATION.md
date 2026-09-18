# AIEOS360-S04-I02 — Coherent School Context Development Composition Integration

Classification: **NON_PRODUCTION local composition** + **neutral production carrier**.

Governing architecture: **ADR-AIEOS-062 Frozen / Approved**. S04-I01 provider
substrate is closed. This slice is **composition integration only**. It does
**not** authorize multi-role local auth, cross-role E2E, Admin OS, or production
ERP/SIS integration. Those remain outside I02. Cross-role E2E belongs to
S04-I03 (not authorized here).

## Objective

Integrate the already-merged `DevelopmentCoherentSchoolContextProvider` into the
canonical LOCAL / NON_PRODUCTION API composition so **one provider instance**
supplies all four existing School Context reader contracts:

1. Teacher Class authority (`SchoolContextClassReader`)
2. Learner Class membership (`SchoolContextLearnerMembershipReader`)
3. Principal Class scope (`SchoolContextPrincipalScopeReader`)
4. Parent learner access (`SchoolContextParentLearnerAccessReader`)

The same instance also supplies learner-membership facts used by Parent
Intelligence.

## Neutral composition carrier

`ApiRuntimeDependencies` / `compose_api_application(...)` now optionally carry:

- `school_context_class_reader`
- `learner_membership_reader`

alongside the existing Principal and Parent reader seams, and forward them
unchanged into `create_app(...)`.

This is a **neutral** carrier extension. It does **not** import any development
provider into production runtime code.

## Canonical local composition

`tools/dev/compose_local_api.py` constructs **one** shared
`DevelopmentCoherentSchoolContextProvider` for `LOCAL_DEV_TENANT_ID` and supplies
that same object as all four reader contracts plus the Parent Intelligence
membership reader.

Local F5 single-token compatibility overlay (development control only):

```text
LOCAL_DEV_PRINCIPAL_ID  Teacher authority  → class-5a, class-5b
LOCAL_DEV_PRINCIPAL_ID  Principal scope   → class-5a, class-5b
```

Canonical coherent story preserved:

```text
Student A → class-5a
Student B → class-5b
Principal OS HUMAN → class-5a / class-5b
Parent A → Student A
Parent B → Student B
```

`LOCAL_DEV_PRINCIPAL_ID` is **not** granted Parent learner access and is **not**
made a learner. The overlay is not a role model.

`DevelopmentParentIntelligencePermit` remains the capability authorization
adapter. Capability authorization and School Context facts stay distinct.

## Production remains fail closed

Production `compose_api_dependencies.py` is unchanged and must not import
`aieos.development.coherent_school_context` or compose
`DevelopmentCoherentSchoolContextProvider`.

Existing production posture remains:

| Port | Production posture |
| --- | --- |
| Teacher School Context | unconfigured / unavailable (`None` carrier) |
| Learning membership | Unconfigured / fail closed |
| Principal scope | Unconfigured / fail closed |
| Parent learner access | Unconfigured / fail closed |

No environment flag may silently select the development provider.

## Explicitly not in I02

- Multi-role bearer tokens / `local_auth.py` role switching
- Cross-role real-stack E2E (S04-I03)
- Admin OS / Admin UI / `PrincipalKind.ADMIN` / `admin.*`
- AIEOS School / Class / Roster / Enrollment / Family SoR
- Production ERP/SIS adapter
- NATS / Temporal
- Migration / OpenAPI change / frontend
- Deleting historical disconnected development adapters

## Verification anchors

- Identity invariant: all four dependency reader fields are the **same object**
- Coherence: Teacher / Student A / Principal / Parent A share class-5a
- Revocation: mutating one dimension via I01 control is visible on the next read
  through the shared instance; unrelated ports remain unchanged; fresh composition
  does not leak mutated state
- Alembic head remains `a360s010004`
- OpenAPI SHA-256 remains
  `4042FB2725DA70A02A70EE09563B7698AE2E5DA82927614CAF1B5F7E6AA7C1D0`
