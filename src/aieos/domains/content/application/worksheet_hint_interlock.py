"""Fail-closed APPROVE/publish interlock for hint-bearing worksheet@2 (ADR-063 §5.3 T1)."""

from __future__ import annotations

from collections.abc import Mapping

from aieos.domains.content.application.errors import (
    WorksheetLearnerHintAttestationRequired,
    WorksheetLearnerHintPublicationBlocked,
)
from aieos.domains.content.domain.version import ContentVersion
from aieos.domains.education.worksheet_hint_safety import (
    version_requires_worksheet_hint_attestation,
)


def _payload_mapping(version: ContentVersion) -> Mapping[str, object]:
    body = version.payload.body
    if not isinstance(body, Mapping):
        return {}
    return body


def enforce_worksheet_hint_review_interlock(version: ContentVersion) -> None:
    """Block APPROVE until T2 §5.3 attestation is implemented."""
    if version_requires_worksheet_hint_attestation(
        schema_id=str(version.schema_id),
        schema_version=int(version.schema_version),
        payload=_payload_mapping(version),
    ):
        raise WorksheetLearnerHintAttestationRequired(
            "hint-bearing worksheet requires learner-hint attestation before APPROVE"
        )


def enforce_worksheet_hint_publish_interlock(version: ContentVersion) -> None:
    """Block publish until T2 §5.3 attestation is implemented."""
    if version_requires_worksheet_hint_attestation(
        schema_id=str(version.schema_id),
        schema_version=int(version.schema_version),
        payload=_payload_mapping(version),
    ):
        raise WorksheetLearnerHintPublicationBlocked(
            "hint-bearing worksheet requires learner-hint attestation before publish"
        )
