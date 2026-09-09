# AIEOS360-S01-I05-B3 — Teacher Assessment Intelligence GET projection

**Status:** Implemented (source) — Backend only — NON_PRODUCTION

## Scope

Derived-on-read Teacher Assessment Intelligence for one TeachingAssignment:

`GET /api/v1/assessment/assignments/{assignment_id}/intelligence`

`operationId`: `assessment_assignment_intelligence`

Capability: `assessment.assignment.intelligence.read`

## Authority

- Current tenant + capability ALLOW + current ClassRef assignability
- Historical TeachingAssignment ownership is not perpetual learner-evidence access
- Historical submitted learners remain visible to a currently authorized teacher

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
