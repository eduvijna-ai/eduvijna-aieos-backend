# PK-V1-HINT-T2 — Implementation limitations (Backend draft)

Backend-only semantic QA + immutable `WorksheetLearnerHintVersionAttestationV1` per ADR-AIEOS-063 §§5.1–5.3.

## In scope (this packet)

- Content-owned table `content.worksheet_learner_hint_version_attestations` (Alembic `a360s010005`)
- Mechanical hard blocks (T1 reuse) + FLAG heuristics (`O63-03-v1`)
- `RecordWorksheetLearnerHintAttestationService` (requires `content.review.decide`)
- Gated APPROVE/publish via attestation + digest + three-actor separation
- No OpenAPI or public HTTP routes

## Not in scope

- Teacher T0 UI, T1b/T1c adapters, learner hint consumption, auto-publish, production DDL execution

## Known limitations

- Idempotent attestation replay re-loads the version-bound attestation record (no typed idempotency result column)
- FLAG heuristics are deterministic placeholders; they do not prove semantic pedagogical safety
- R3: `provision_runtime_grants` applies T2 attestation `SELECT`/`INSERT` only when the table exists; at forward Alembic head the table must exist or grant provisioning fails closed
- R5: T2 `downgrade()` temporarily disables `security.audit_records` RLS under the Security schema-owner role so the audit-evidence guard can see rows (FORCE RLS has no SELECT policy); RLS is re-enabled before fail-closed rollback or DROP
