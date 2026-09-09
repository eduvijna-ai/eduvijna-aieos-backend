# AIEOS360-S01-I05-B3 — Teacher Assessment Intelligence GET projection

**Status:** Implemented (source) — Backend only — NON_PRODUCTION

## Scope

Derived-on-read Teacher Assessment Intelligence for one TeachingAssignment:

`GET /api/v1/assessment/assignments/{assignment_id}/intelligence`

`operationId`: `assessment_assignment_intelligence`

Capability: `assessment.assignment.intelligence.read`

Authorization composition (B3R1):

1. coarse capability ALLOW
2. current HUMAN principal (`CurrentPrincipalClassificationAuthority.require_current_human_principal`)
3. TeachingAssignment lineage / ClassRef discovery
4. current ClassRef assignability
5. derived-on-read projection

No learner submission/evaluation evidence is read before steps 2–4 succeed.

## Authority

- Current tenant + capability ALLOW + current HUMAN principal + current ClassRef assignability
- Historical TeachingAssignment ownership is not perpetual learner-evidence access
- Historical submitted learners remain visible to a currently authorized HUMAN teacher
- WORKLOAD / unclassified / classification-unavailable → fail closed

## Non-goals

- No evaluation ensure/evaluate on GET
- No new Alembic migration / tables
- No ClassroomAssessment / Improve / TeachingWork mutation
- No not-submitted roster count
- No mastery / learner-model / AI / NATS / Temporal
- No Frontend

## Current policy

Server-selected only:

- `aieos.learner_assessment.deterministic` / `1`

Obsolete-policy rows remain historical and do not become current projection.
