"""Content-facing School Context reviewer class eligibility (ADR-AIEOS-064 / ADR-062).

Distinct from Teaching ``require_assignable_class_ref``. Reads trusted current facts
only; production remains unavailable/fail closed unless a provider is composed.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from aieos.domains.content.application.errors import ReviewAssignmentForbidden
from aieos.domains.teaching.application.errors import SchoolContextUnavailable


class SchoolContextReviewerEligibilityReader(Protocol):
    """Replaceable port for current reviewer-eligible class_ref facts."""

    def list_reviewer_eligible_class_refs(
        self,
        tenant_id: UUID,
        reviewer_principal_id: UUID,
    ) -> tuple[str, ...]: ...


class SchoolContextReviewerEligibility(Protocol):
    def require_reviewer_class_eligibility(
        self,
        tenant_id: UUID,
        reviewer_principal_id: UUID,
        class_ref: str,
    ) -> str: ...


class SchoolContextReviewerEligibilityService:
    """Revalidates reviewer class eligibility via the School Context port."""

    def __init__(self, reader: SchoolContextReviewerEligibilityReader) -> None:
        self._reader = reader

    def require_reviewer_class_eligibility(
        self,
        tenant_id: UUID,
        reviewer_principal_id: UUID,
        class_ref: str,
    ) -> str:
        try:
            allowed = self._reader.list_reviewer_eligible_class_refs(
                tenant_id, reviewer_principal_id
            )
        except SchoolContextUnavailable:
            raise
        except Exception as exc:
            raise SchoolContextUnavailable(
                "School Context is temporarily unavailable"
            ) from exc
        normalized = class_ref.strip()
        if normalized in allowed:
            return normalized
        raise ReviewAssignmentForbidden(
            "reviewer is not eligible for the mandatory authoring class"
        )