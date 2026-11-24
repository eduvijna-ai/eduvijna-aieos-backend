"""NON_PRODUCTION School Context reviewer class eligibility (CI03-G2-I01)."""

from __future__ import annotations

from uuid import UUID

from aieos.development.coherent_school_context import (
    DevelopmentCoherentSchoolContextProvider,
    development_coherent_school_context_provider,
)


class DevelopmentReviewerEligibilityReader:
    """Synthetic reviewer-eligible class facts for disposable PG18 tests only."""

    def __init__(self, provider: DevelopmentCoherentSchoolContextProvider) -> None:
        self._provider = provider

    def list_reviewer_eligible_class_refs(
        self,
        tenant_id: UUID,
        reviewer_principal_id: UUID,
    ) -> tuple[str, ...]:
        return self._provider.list_reviewer_eligible_class_refs(
            tenant_id, reviewer_principal_id
        )


def development_reviewer_eligibility_reader(
    provider: DevelopmentCoherentSchoolContextProvider | None = None,
) -> DevelopmentReviewerEligibilityReader:
    return DevelopmentReviewerEligibilityReader(
        provider or development_coherent_school_context_provider()
    )