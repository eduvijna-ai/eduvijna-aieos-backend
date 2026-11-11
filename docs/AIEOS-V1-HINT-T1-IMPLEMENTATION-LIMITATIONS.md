# AIEOS-V1-HINT-T1 — Implementation limitations (PK-HINT-A)

Backend-only foundation under frozen ADR-AIEOS-063. This packet does **not**
authorize Teacher hint HTTP APIs, Student attempt-scoped hint GET, T2 durable
attestation storage, T1b/T1c learner/assessment adapters, or production
workflows.

## In scope

- Typed `education.worksheet` / `schema_version=2` registration (development /
  preparation test registries and explicit composition hooks only).
- Generic append bypass closure at `HttpAppendContentVersionService` (key
  presence, not truthiness).
- Guarded application command `AppendWorksheetLearnerHintsContentVersionService`
  (no public route; persists normalized hint text and `authoring_class_ref`
  version provenance; idempotent replay re-checks current authority).
- Temporary fail-closed APPROVE/publish interlock for hint-key-bearing worksheet@2
  payloads until §5.3 attestation exists.

## Out of scope (explicit)

- Similarity / PII flag thresholds, reviewer role separation, digest attestation
  persistence, and semantic safety claims.
- Student `learner_projection` or Assessment exact-content evaluation for `@2`.
- New grants, migrations, or OpenAPI contract changes.
