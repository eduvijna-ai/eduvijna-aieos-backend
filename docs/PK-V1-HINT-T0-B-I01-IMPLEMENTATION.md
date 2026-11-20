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

## OpenAPI

- Pre: `43F877E122389A7347DDB00D25F37D8BE8BD907F43FF6960E5DC6E794B1A0071`
- Post: `1058E5C715EEA1113E7D88D410A540C89B0459BA97C5EE187DF90B813571F13B`
- Regenerate: `uv run python` using `build_openapi(create_app(...))` as in
  `tests/domains/education/test_aieos_v1_hint_t0_b_openapi.py`.

## Out of scope

Frontend, production tenant grants, full Hint E2E, and T0-F OpenAPI sync.