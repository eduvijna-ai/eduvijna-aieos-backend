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

- Idempotent attestation replay stores `attestation_id` in `result_review_decision_id` (no new idempotency column in this packet)
- FLAG heuristics are deterministic placeholders; they do not prove semantic pedagogical safety
