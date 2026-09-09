# AIEOS360-S01-I05-B2 — Learner Assessment Evaluation Application/API

Makes B1 immutable `LearnerAssessmentEvaluation` evidence reachable through
governed Assessment commands. Persistence and
`DeterministicLearnerAssessmentEvaluatorV1` remain unchanged.

## Frozen architecture authority

- ADR-AIEOS-059 — Frozen / Approved v1.0.2 OPTION D
- Architecture pin: `cd9cd87d6101990e52e9c8267322aaa6633fced3`
- Backend base: `63ff2765362bb9807c42553ce48ae95f9d9b4b84`
- B1: formally closed

## HTTP surface

| Method | Path | operation_id | Status |
| --- | --- | --- | --- |
| POST | `/api/v1/assessment/submissions/{submission_id}/actions/evaluate` | `assessment_learner_evaluation_ensure` | 200 |
| POST | `/api/v1/assessment/assignments/{assignment_id}/actions/ensure-evaluations` | `assessment_assignment_evaluations_ensure` | 204 |

Both mutations require `Idempotency-Key`. Trusted identity only. The client
supplies path identity plus the idempotency key. The server derives lineage,
current policy, and evaluation facts.

Side-effecting GET is prohibited. B3
`GET /api/v1/assessment/assignments/{assignment_id}/intelligence` is not
implemented.

## Current teacher ClassRef authority

Current School Context ClassRef authority is the teacher-access gate.

- Historical `TeachingAssignment.teacher_principal_id` is not perpetual
  learner-PII authorization.
- A former assignment creator without current class authority is denied.
- Another currently authorized teacher of that class is allowed.

Capability `assessment.learner_evaluation.ensure` is required and is not
sufficient by itself.

## Historical learner membership

A valid immutable `LearnerSubmission` remains evaluable after the learner
leaves the class. Current learner membership is not re-checked.

## Exact ContentVersion evaluation

Evaluation uses the exact immutable ContentVersion bound by the submission.

- Never follow `published_version_id`
- Never substitute a later answer key
- Supported families: `education.worksheet@1`, `education.quiz@1`,
  `education.homework@1`
- Answer keys remain evaluator-internal and are not returned

## Current policy

Server-selected:

- `evaluation_policy_id = aieos.learner_assessment.deterministic`
- `evaluation_policy_version = 1`

Callers cannot select policy.

## Batch ensure

Ensures current-policy evaluations for existing immutable LearnerSubmissions
of the assignment. It does not enumerate a roster, synthesize IN_PROGRESS
attempts, or invent a not-submitted denominator.

Zero submissions is a successful 204. Exact Idempotency-Key replay does not
absorb later submissions. A later fresh key may.

## Explicit non-goals

Teacher Assessment Intelligence (B3), Improve / TeachingWork / remediation,
mastery, recommendations, NATS Assessment publication, Temporal,
submission-triggered background evaluation, Frontend/E2E consumption.

## Idempotency / audit

- `assessment_learner_evaluation_ensure.v1`
- `assessment_assignment_evaluations_ensure.v1`
- Audit action: `assessment.learner_evaluation.ensure`
- Primary resource type: `assessment.learner_evaluation`
- Creation marker: `resource_revision_before = NULL`, `after = 0`
- No `aggregate_revision` / ETag on LearnerAssessmentEvaluation
- Audit only for newly created evaluations

Alembic: `a360s010004` (down_revision `a360s010003`); nonempty evaluation
audit evidence refuses downgrade. Scope is security audit CHECK vocabulary
only.
