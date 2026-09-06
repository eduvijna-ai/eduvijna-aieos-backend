# TOS-DEV10-I03S1 — Principal Kind Security Substrate

**Status:** IMPLEMENTED (source) — Teacher Memory wiring and production
classification backfill remain **NOT AUTHORIZED** in this slice.

## What landed

- Alembic `pedi090002` adds nullable `security.principals.principal_kind`
  with CHECK allowing only `NULL`, `HUMAN`, or `WORKLOAD` (no DEFAULT, no
  blanket UPDATE).
- Typed `PrincipalKind` and `PrincipalAuthorityRow.principal_kind`.
- `CurrentPrincipalClassificationAuthority` re-reads current SoR and fails
  closed for missing/inactive/NULL/wrong kind; corrupt/unavailable →
  `AuthorizationUnavailableError`.
- JWT / request headers never supply kind (`TrustedRequestIdentity` remains
  `{principal_id}` only).

## PRODUCTION-HARDENING CARRY-FORWARD

Development may leave legacy rows as `principal_kind IS NULL`
(unclassified). Any HUMAN/WORKLOAD-required check must fail closed on NULL.

Before Production Readiness:

1. Inventory and explicitly classify every active Principal.
2. Reconcile controlled provisioning so new rows always set kind.
3. Only then make `security.principals.principal_kind` **NOT NULL**.

Do not invent identity by inferring kind from grants, membership, JWT, or
route history.
