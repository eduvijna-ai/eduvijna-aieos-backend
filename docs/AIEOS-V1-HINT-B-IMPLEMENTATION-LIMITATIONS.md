# PK-V1-HINT-B — Attempt-scoped learner-hint GET (implementation limitations)

## Delivered

- Public `GET /api/v1/learning/attempts/{attempt_id}/learner-hints` (`learning_attempt_learner_hints_get`)
- ADR-063 §8.2 Phase A preview + Phase B assignment→attempt lock linearization (same order as submit)
- Governed worksheet@2 hint extraction with exact-version publication, APPROVE, and §5.3 attestation when required
- `Cache-Control: private, no-store` on all hint GET responses (success and denial)
- OpenAPI documents RFC 9457 **409** (`invalid_attempt_state`) and `Cache-Control` on
  the learner-hints operation only (pre-R1 B digest
  `E8DEE8E2110BF42FA1405745BFA0EB1475406E9D39AAD7CAE1078DD617699C7E`; post-R1 digest
  `43F877E122389A7347DDB00D25F37D8BE8BD907F43FF6960E5DC6E794B1A0071`)

## Not delivered (explicit)

- Teacher T0 hint editor, Student F UI, per-question hint route, durable read-disclosure audit vocabulary/storage
- Hint text in security mutation audit, telemetry, or RFC 9457 problem bodies
- Production School Context or ERP roster master

## Read observation

ADR-063 conceptual hint read disclosure is **not** persisted: no new audit action or observation store was authorized for this packet.
