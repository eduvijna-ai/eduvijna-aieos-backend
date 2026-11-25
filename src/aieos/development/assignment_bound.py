"""NON_PRODUCTION assignment-bound hint mutation dependencies."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.engine import Engine

from aieos.development.coherent_school_context import (
    DevelopmentCoherentSchoolContextProvider,
    development_coherent_school_context_provider,
)
from aieos.development.reviewer_eligibility import development_reviewer_eligibility_reader
from aieos.domains.content.application.review_assignment_bound_mutations import (
    AssignmentBoundMutationDependencies,
)
from aieos.domains.content.application.school_context_reviewer_eligibility import (
    SchoolContextReviewerEligibilityService,
)
from aieos.domains.teaching.application.school_context import SchoolContextClassAuthority
from aieos.platform.security.audit.persistence.authorization_denial_records import (
    SqlAlchemyAuthorizationDenialRecords,
)
from aieos.platform.security.authorization import (
    AuthorizationKernel,
    CurrentPrincipalClassificationAuthority,
    KernelReviewAssignmentAuthorization,
    KernelReviewAuthorization,
)
from aieos.platform.security.authorization.content_adapters import AIEOS_CONTENT_CAPABILITIES


def build_assignment_bound_mutation_dependencies(
    engine: Engine,
    *,
    tenant_id: UUID,
    issuer_class_authority: SchoolContextClassAuthority,
    school_context_provider: DevelopmentCoherentSchoolContextProvider | None = None,
    clock: Callable[[], datetime] | None = None,
) -> AssignmentBoundMutationDependencies:
    """Trusted kernel + school context composition for governed NON_PRODUCTION shells."""
    provider = school_context_provider or development_coherent_school_context_provider(
        tenant_id=tenant_id
    )
    kernel = AuthorizationKernel(engine, known_capabilities=AIEOS_CONTENT_CAPABILITIES)
    effective_clock = clock or (lambda: datetime.now(UTC))
    return AssignmentBoundMutationDependencies(
        assign_authorization=KernelReviewAssignmentAuthorization(kernel),
        review_authorization=KernelReviewAuthorization(kernel),
        principal_classification=CurrentPrincipalClassificationAuthority(engine),
        reviewer_eligibility=SchoolContextReviewerEligibilityService(
            development_reviewer_eligibility_reader(provider)
        ),
        issuer_class_authority=issuer_class_authority,
        denial_audit=SqlAlchemyAuthorizationDenialRecords(engine),
        clock=effective_clock,
    )