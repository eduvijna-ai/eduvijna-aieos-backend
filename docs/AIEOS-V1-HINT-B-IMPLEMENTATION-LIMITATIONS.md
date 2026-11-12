# PK-V1-HINT-B — Attempt-scoped learner-hint GET (implementation limitations)

## Delivered

- Public `GET /api/v1/learning/attempts/{attempt_id}/learner-hints` (`learning_attempt_learner_hints_get`)
- ADR-063 §8.2 Phase A preview + Phase B assignment→attempt lock linearization (same order as submit)
- Governed worksheet@2 hint extraction with exact-version publication, APPROVE, and §5.3 attestation when required
- `Cache-Control: private, no-store` on all hint GET responses (success and denial)

## Not delivered (explicit)

- Teacher T0 hint editor, Student F UI, per-question hint route, durable read-disclosure audit vocabulary/storage
- Hint text in security mutation audit, telemetry, or RFC 9457 problem bodies
- Production School Context or ERP roster master

## Read observation

ADR-063 conceptual hint read disclosure is **not** persisted: no new audit action or observation store was authorized for this packet.
