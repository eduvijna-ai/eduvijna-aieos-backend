"""Content-owned immutable worksheet learner-hint version attestation (ADR-063 §5.3)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from aieos.domains.content.domain.errors import InvalidContentIdentityError
from aieos.domains.content.domain.identities import (
    ContentId,
    ContentVersionId,
    WorksheetLearnerHintAttestationId,
    require_foreign_uuid,
)

_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_POLICY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _require_aware(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidContentIdentityError(f"{label} must be timezone-aware")
    return value


@dataclass(frozen=True, slots=True)
class HintQuestionAttestationOutcome:
    question_id: str
    hard_block_clear: bool
    human_review_pass: bool

    def __post_init__(self) -> None:
        if not self.question_id:
            raise InvalidContentIdentityError("question_id is required")
        if not isinstance(self.hard_block_clear, bool) or not isinstance(
            self.human_review_pass, bool
        ):
            raise InvalidContentIdentityError("attestation outcomes must be booleans")


@dataclass(frozen=True, slots=True)
class WorksheetLearnerHintVersionAttestationV1:
    attestation_id: WorksheetLearnerHintAttestationId
    tenant_id: UUID
    content_id: ContentId
    version_id: ContentVersionId
    payload_digest: str
    qa_policy_version: str
    author_principal_id: UUID
    reviewer_principal_id: UUID
    per_question_outcomes: tuple[HintQuestionAttestationOutcome, ...]
    attested_at: datetime
    correlation_id: UUID

    def __post_init__(self) -> None:
        require_foreign_uuid(self.tenant_id, label="tenant_id")
        require_foreign_uuid(self.author_principal_id, label="author_principal_id")
        require_foreign_uuid(self.reviewer_principal_id, label="reviewer_principal_id")
        require_foreign_uuid(self.correlation_id, label="correlation_id")
        _require_aware(self.attested_at, label="attested_at")
        if not _DIGEST_PATTERN.fullmatch(self.payload_digest):
            raise InvalidContentIdentityError("payload_digest must be lowercase sha256 hex")
        if not _POLICY_PATTERN.fullmatch(self.qa_policy_version):
            raise InvalidContentIdentityError("qa_policy_version is malformed")
        if not self.per_question_outcomes:
            raise InvalidContentIdentityError(
                "per_question_outcomes must be non-empty for hint attestation"
            )
        seen: set[str] = set()
        for outcome in self.per_question_outcomes:
            if outcome.question_id in seen:
                raise InvalidContentIdentityError("duplicate question_id in outcomes")
            seen.add(outcome.question_id)
