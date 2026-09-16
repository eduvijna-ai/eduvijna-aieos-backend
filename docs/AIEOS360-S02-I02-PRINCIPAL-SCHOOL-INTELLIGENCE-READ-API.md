# AIEOS360-S02-I02 — Principal School Intelligence Derived Read API

Governing architecture: **ADR-AIEOS-060 Frozen / Approved**.

Prerequisite: **AIEOS360-S02-I01 CLOSED** (current HUMAN +
`school.intelligence.read` + current Principal School Scope).

This slice implements the first Principal / School Intelligence HTTP read:

```text
GET /api/v1/principal-os/school-intelligence
operation_id: principal_os_school_intelligence_get
```

The projection is:

* read-only
* derived-on-request
* deterministic
* side-effect-free
* a cross-domain application/read projection

It is **not** a School Intelligence business SoR, materialized view, cached
snapshot, learner analytics product, mastery model, teacher evaluation, or
AI-generated narrative.

## Authority order (every GET)

1. trusted server-side `tenant_id` + `principal_id`
2. current ACTIVE HUMAN Principal
3. exact capability `school.intelligence.read`
4. current Principal School Scope enumeration (`CurrentPrincipalSchoolScopeService`)
5. complete authorized ClassRef set
6. only then read/aggregate source-domain facts, filtered to those ClassRefs
   **before** aggregation
7. return the derived Principal DTO

I01 authorization is reused. It is not reimplemented. There is no authority
cache, no historical role snapshot, and no ClassRef list from JWT, headers,
query, body, frontend state, teacher ClassRef ports, or learner membership.

Production composes `UnconfiguredSchoolContextPrincipalScopeReader`. A
production Principal GET therefore fails closed at School Scope Current
Authority before the SQL facts reader is used. The SQL reader does not bypass
scope.

## Freshness / time semantics

| Field | Value |
| --- | --- |
| `projection_mode` | `DERIVED_ON_REQUEST` |
| `generated_at` | PostgreSQL `transaction_timestamp()` of the read snapshot |
| `time_window.mode` | `CURRENT_FACTS_AS_OF_REQUEST` |
| `time_window.start` | `null` |
| `time_window.end` | `generated_at` |

No custom historical date-range query parameters. Historical dashboard
reconstruction is not authorized.

Where timestamps exist, source rows count only when:

* assignment `assigned_at <= generated_at`
* submission `submitted_at <= generated_at`
* evaluation `evaluated_at <= generated_at`
* classroom assessment `recorded_at <= generated_at`
* execution `completed_at <= generated_at`
* remediation origin `created_at <= generated_at`

## Current evaluation policy

Canonical Assessment constants are imported, not copied:

* `evaluation_policy_id` = `aieos.learner_assessment.deterministic`
* `evaluation_policy_version` = `1`

Obsolete-policy evaluations remain Assessment history and do **not** count
toward current Principal evaluation coverage.

## Filter-before-aggregation invariant

Every source query is constrained to:

```text
tenant_id = trusted tenant
AND class_ref ∈ current authorized ClassRefs
```

**before** `GROUP BY` / `COUNT` / `DISTINCT`.

Remediation uses `source_class_ref ∈ current authorized ClassRefs` before
aggregation. Tenant-wide aggregation followed by post-filtering is forbidden.

If current authorized ClassRef count exceeds `100`, the whole projection fails
with `SchoolIntelligenceScopeCapacityExceeded` mapped to a sanitized 503.
The authorized scope is never silently truncated.

## Metric catalogue

For **every** returned metric below:

* **Current scope rule:** only facts whose ClassRef is in the current
  authorized set.
* **Time semantics:** current durable facts as of `generated_at`.
* **Freshness:** derived on this request; no School Intelligence persistence.
* **Privacy boundary:** no learner identity, no raw responses, no question /
  objective outcomes, no teacher ranking, no ClassroomAssessment result/note,
  no private execution notes.

### School summary — `in_scope_class_count`

* **Definition:** count of currently authorized ClassRefs.
* **Source SoR:** School Scope Current Authority (ERP/SIS façade).
* **Included:** validated current provider ClassRefs.
* **Excluded:** teacher assignable classes, learner memberships, JWT/header lists.
* **Numerator/denominator:** count, not a rate.

### `classes_with_assignment_activity_count`

* **Definition:** number of currently authorized classes where
  `teaching_assignment_count > 0`.
* **Source SoR:** Teaching assignment rows.
* **Included:** authorized classes with at least one in-window assignment.
* **Excluded:** unauthorized classes; this is not class adoption, teacher
  adoption, teacher engagement, or teacher performance.

### `teaching_assignment_count`

* **Definition:** count of assignment rows for authorized classes with
  `assigned_at <= generated_at`, across ACTIVE, CLOSED, and CANCELLED.
* **Source SoR:** `teaching.assignments`.
* **Included:** all lifecycle states in window.
* **Excluded:** future `assigned_at`, other tenants, unauthorized ClassRefs.
* **Lifecycle:** `active` + `closed` + `cancelled` **must equal** this count.

### `assignment_lifecycle.active / closed / cancelled`

* **Definition:** counts of those lifecycle states among the same assignment
  population.
* **Source SoR:** `teaching.assignments.lifecycle_state`.

### `learner_submission_count`

* **Definition:** count of immutable submission rows attached to authorized
  in-scope assignments, joined on tenant, assignment id, and ClassRef.
* **Source SoR:** `learning.submissions` joined to `teaching.assignments`.
* **Included:** coherent in-window submissions for authorized ClassRefs.
* **Excluded:** unauthorized classes; submissions that do not join to an
  in-scope assignment; attempt response items; learner identities.
* **Denominator law:** this is a count. Submission rate is **NOT**
  implemented. No roster denominator. No not-submitted learner count.

### `current_policy_evaluation_count`

* **Definition:** count of current-policy evaluation rows grounded in the
  filtered authorized submission population (submissions LEFT JOIN
  evaluations on tenant, submission, assignment, ClassRef + current policy).
* **Source SoR:** `assessment.learner_assessment_evaluations`.
* **Included:** exact current policy id/version only.
* **Excluded:** obsolete policy; evaluation item/outcome rows; objective
  evidence; question outcomes.
* **Invariant:** `0 <= current_policy_evaluation_count <= learner_submission_count`.

### `submitted_but_not_current_policy_evaluated_count`

* **Definition:** `learner_submission_count - current_policy_evaluation_count`.
* **Source SoR:** derived from the two counts above.
* **Included/excluded:** same populations as those counts.

### `evaluation_coverage_among_submitted`

* **Definition:** the authoritative pair
  `{ submitted_count, current_policy_evaluated_count }`.
* **Denominator law:** **among submitted only**.
* **Not:** submission coverage, learner coverage, student completion, class
  completion, or mastery coverage.
* **Percentage:** **not** implemented. When `submitted_count = 0`, both
  counts are `0`. No manufactured `0%` or `100%`.

The first Principal UI may truthfully present
“8 of 10 submitted pieces of evidence evaluated”.

### `classes_with_recorded_classroom_assessment_count`

* **Definition:** authorized classes with at least one current RECORDED
  class-level assessment (`recorded_at <= generated_at`).
* **Source SoR:** `assessment.classroom_assessments`.
* **Included:** `lifecycle_state = RECORDED`.
* **Excluded:** VOIDED rows; result level; note text; class-by-class result
  comparison.

### `assignments_with_recorded_classroom_assessment_count`

* **Definition:** DISTINCT non-null assignment ids with a current RECORDED
  class-level assessment attributable to that authorized class. School
  summary sums the class-level distinct counts.
* **Source SoR:** `assessment.classroom_assessments.assignment_id`.

### Class card `has_recorded_classroom_assessment`

* **Definition:** boolean current RECORDED activity for that class.

### `completed_teaching_execution_count`

* **Definition:** execution rows with `lifecycle_state = COMPLETED` and
  `completed_at <= generated_at` for authorized ClassRefs.
* **Source SoR:** `teaching.executions`.
* **Excluded:** IN_PROGRESS, CANCELLED; observation table/body;
  `PRIVATE_EXECUTION_NOTE`; teacher identity.

### `remediation_activity_count`

* **Definition:** immutable remediation-origin rows whose
  `source_class_ref` is currently authorized and `created_at <= generated_at`.
* **Source SoR:** `teaching.work_remediation_origins`.
* **Class attribution:** `source_class_ref` only. TeachingWork free-text
  `class_label` / `subject` / `topic` cannot influence attribution.
* **Excluded:** result-level snapshot; initiating teacher identity.
  This is evidence that Improve/remediation activity exists, not teacher
  remediation performance.

### Class card `has_assignment_activity`

* **Definition:** `teaching_assignment_count > 0`.

### Class card `class_ref` / `display_label`

* **Definition:** authorized ClassRef ordered `class_ref ASC`; display label
  from **current** School Scope authority.

## Source tables read

* `teaching.assignments`
* `teaching.executions`
* `teaching.work_remediation_origins`
* `learning.submissions`
* `assessment.learner_assessment_evaluations`
* `assessment.classroom_assessments`

School Scope is read through the I01 façade, not a School Intelligence table.

## Source tables not read

* `learning.attempt_response_items`
* `assessment.learner_assessment_evaluation_items`
* `assessment.learner_assessment_objective_evidence`
* `teaching.execution_observations`

## Explicit non-implementations

* **Evaluation coverage = among submitted only.**
* **Submission rate is NOT implemented.**
* **Mastery is NOT implemented.**
* **No teacher ranking.**
* **No learner identity.**
* **No School Intelligence persistence.**
* **Production Principal scope adapter remains unconfigured** unless
  separately implemented.
* No Teacher Assessment Intelligence DTO reuse.
* No NATS, Temporal, MCP, Agent, LLM, or mutation UoW.
* Alembic remains `a360s010004`. No School Intelligence table.

## Error mapping (RFC 9457)

| Application error | HTTP |
| --- | --- |
| `SchoolIntelligenceCapabilityForbidden` | 403 |
| `SchoolContextUnavailable` | 503 |
| `SchoolContextContractError` | 503 |
| `SchoolIntelligenceReadUnavailable` | 503 |
| `SchoolIntelligenceScopeCapacityExceeded` | sanitized 503 unavailable |
| `UnauthenticatedError` | 401 |
| `UnauthorizedError` | 403 |
| `AuthorizationUnavailableError` | 503 |

Source-domain read failure fails the entire projection. It is never translated
into zero. Truthful zero is allowed only after authority and source reads
succeed and return no matching facts.

## Development composition

NON_PRODUCTION only: `DevelopmentSchoolIntelligencePermit` (exact
`school.intelligence.read`; rejects wildcard/unknown) plus
`DevelopmentSchoolContextPrincipalScopeReader` from
`src/aieos/development/principal_school_context.py`. Never imported by
production runtime composition.
