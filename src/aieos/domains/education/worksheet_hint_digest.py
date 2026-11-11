"""Canonical worksheet@2 hint attestation payload digest (ADR-063 §5.3)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from aieos.domains.content.domain.version import canonical_payload_json, thaw_json_value
from aieos.domains.education.schema import WORKSHEET_SCHEMA_ID, WORKSHEET_SCHEMA_VERSION_V2

_CANONICAL_KEYS = ("schema_id", "schema_version", "payload")


def worksheet_hint_attestation_digest_hex(*, payload_body: Mapping[str, object]) -> str:
    """SHA-256 over schema_id + schema_version + exact immutable worksheet payload."""
    envelope = {
        "schema_id": WORKSHEET_SCHEMA_ID,
        "schema_version": WORKSHEET_SCHEMA_VERSION_V2,
        "payload": thaw_json_value(dict(payload_body)),
    }
    canonical = json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def worksheet_hint_attestation_digest_from_version_payload(
    payload_body: Mapping[str, object],
) -> str:
    """Digest helper that accepts frozen/thawed payload mappings."""
    if not isinstance(payload_body, Mapping):
        raise ValueError("payload body must be a mapping")
    normalized = json.loads(canonical_payload_json(payload_body))
    if not isinstance(normalized, dict):
        raise ValueError("payload body must be a JSON object")
    return worksheet_hint_attestation_digest_hex(payload_body=normalized)
