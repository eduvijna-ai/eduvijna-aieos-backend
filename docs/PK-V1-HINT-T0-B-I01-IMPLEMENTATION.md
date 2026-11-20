# PK-V1-HINT-T0-B-I01 — Teacher hint HTTP (non-normative)

Backend-only HTTP integration for ADR-AIEOS-063 §7 Teacher authoring and structured
attestation commands. Domain services are unchanged; this packet wires routes,
composition, and OpenAPI only.

## Operations

| Operation ID | Method | Path |
|---|---|---|
| `content_worksheet_hint_version_append` | `POST` | `/api/v1/contents/{content_id}/worksheet-learner-hint-versions` |
| `content_worksheet_hint_attestation_record` | `POST` | `/api/v1/contents/{content_id}/versions/{version_id}/actions/record-worksheet-hint-attestation` |

Both require `If-Match`, `Idempotency-Key`, and `X-AIEOS-Tenant-ID` per existing Content
mutation conventions.

## Composition

- Guarded append is composed only when **both** `SchoolContextClassAuthority` and
  `AIGenerationAuthorizationPort` (`content.version.create`) are wired; otherwise the
  dependency layer fails closed with HTTP 503.
- Production `compose_api_runtime_dependencies` now supplies
  `KernelAIGenerationAuthorization` (no new grants).
- Attestation record is always composed with `KernelReviewAuthorization` /
  test doubles for `content.review.decide`.

## I01R1 security-contract correction (Draft PR #74)

- PED-I03: `content_worksheet_hint_version_append` and
  `content_worksheet_hint_attestation_record` added to `FROZEN_API_MUTATION_OPERATION_IDS`.
- RFC9457: `AIGenerationForbidden` → HTTP 403; `WorksheetLearnerHintAttestationInvalid` → HTTP 409.
- OpenAPI metadata: required `Idempotency-Key` / `If-Match`; `ETag` (+ `Location` on append) on 201.

## OpenAPI

- T0-B initial post (I01): `1058E5C715EEA1113E7D88D410A540C89B0459BA97C5EE187DF90B813571F13B`
- After I01R1 header/metadata correction:
  `0061BCF6065FAFA184F196829A87AF98ADEDFCE6B633D45704141A5B48045D22`
- Pre-T0-B main: `43F877E122389A7347DDB00D25F37D8BE8BD907F43FF6960E5DC6E794B1A0071`
- Regenerate: `uv run python` using `build_openapi(create_app(...))` as in
  `tests/domains/education/test_aieos_v1_hint_t0_b_openapi.py`.

## Out of scope

Frontend, production tenant grants, full Hint E2E, and T0-F OpenAPI sync.