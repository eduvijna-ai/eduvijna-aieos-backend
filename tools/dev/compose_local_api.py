"""Compose production-faithful API dependencies with local-only adapters.

LOCAL DEVELOPMENT ONLY — NEVER PRODUCTION.
"""

from __future__ import annotations

from sqlalchemy.engine import Engine

from aieos.development.auth_adapters import (
    DevelopmentAssetCurrentUsePermit,
    DevelopmentAssetReferencePermit,
    DevelopmentClassroomAssessmentPermit,
    DevelopmentPublicationAuthorizationPermit,
    DevelopmentPublicationGovernancePermit,
    DevelopmentReviewAuthorizationPermit,
    DevelopmentReviewCommentPermit,
    DevelopmentTeachingWorkPermit,
)
from aieos.development.learner_school_context import (
    DevelopmentSchoolContextLearnerMembershipReader,
)
from aieos.development.parent_learner_access import (
    DevelopmentParentIntelligencePermit,
    DevelopmentSchoolContextParentLearnerAccessReader,
)
from aieos.development.principal_school_context import (
    DevelopmentSchoolContextPrincipalScopeReader,
    DevelopmentSchoolIntelligencePermit,
)
from aieos.domains.parent_intelligence.application.learner_access import (
    CurrentParentLearnerAccessService,
)
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from aieos.domains.assessment.infrastructure.persistence.uow import (
    SqlAlchemyAssessmentUnitOfWorkFactory,
)
from aieos.domains.parent_intelligence.infrastructure.read_projection import (
    SqlAlchemyParentIntelligenceFactsReader,
)
from aieos.domains.school_intelligence.infrastructure.read_projection import (
    SqlAlchemySchoolIntelligenceFactsReader,
)
from aieos.platform.runtime.activation import (
    load_api_mutation_activation_gate_from_process_environment,
)
from aieos.platform.runtime.composition import ApiRuntimeDependencies
from aieos.platform.runtime.content_production import (
    build_production_content_schema_registry,
    build_production_content_type_catalog,
)
from aieos.platform.runtime.models import ApiRuntimeConfig
from aieos.platform.runtime.readiness import SqlAlchemyApiReadinessProbe
from aieos.platform.runtime.remediation_assessment_source import (
    SqlAlchemyRemediationAssessmentSource,
)
from aieos.platform.runtime.student_learning_command import (
    SqlAlchemyStudentLearningCommandUnitOfWorkFactory,
)
from aieos.platform.security.authorization import (
    CurrentPrincipalClassificationAuthority,
    SecurityAuthorityLearnerPrincipalIntegrity,
)
from tools.dev.local_auth import (
    LocalDevelopmentBearerAuthenticator,
    LocalDevelopmentTenantSecurityResolver,
)
from tools.dev.local_config import (
    LOCAL_BEARER_TOKEN,
    LOCAL_DEV_PRINCIPAL_ID,
    LOCAL_DEV_TENANT_ID,
)


def compose_local_api_runtime_dependencies(
    *,
    engine: Engine,
    config: ApiRuntimeConfig,
) -> ApiRuntimeDependencies:
    """Explicit local composition — no JWT/JWKS fetch, no AIStor network I/O."""
    principal_classification_authority = CurrentPrincipalClassificationAuthority(
        engine
    )
    parent_intelligence_authorization = DevelopmentParentIntelligencePermit()
    school_context_parent_learner_access_reader = (
        DevelopmentSchoolContextParentLearnerAccessReader(
            tenant_id=LOCAL_DEV_TENANT_ID,
        )
    )
    parent_learner_integrity_authority = SecurityAuthorityLearnerPrincipalIntegrity(
        engine
    )
    parent_learner_access_service = CurrentParentLearnerAccessService(
        classification=principal_classification_authority,
        authorization=parent_intelligence_authorization,
        reader=school_context_parent_learner_access_reader,
        integrity=parent_learner_integrity_authority,
    )
    return ApiRuntimeDependencies(
        uow_factory=SqlAlchemyContentUnitOfWorkFactory(engine),
        teaching_uow_factory=SqlAlchemyTeachingUnitOfWorkFactory(
            engine,
            remediation_assessment_source_factory=(
                SqlAlchemyRemediationAssessmentSource
            ),
        ),
        assessment_uow_factory=SqlAlchemyAssessmentUnitOfWorkFactory(engine),
        assessment_authorization=DevelopmentClassroomAssessmentPermit(),
        teaching_authorization=DevelopmentTeachingWorkPermit(),
        request_identity_authenticator=LocalDevelopmentBearerAuthenticator(
            principal_id=LOCAL_DEV_PRINCIPAL_ID,
            expected_bearer_token=LOCAL_BEARER_TOKEN,
        ),
        security_resolver=LocalDevelopmentTenantSecurityResolver(
            authorized_tenant_id=LOCAL_DEV_TENANT_ID,
            principal_id=LOCAL_DEV_PRINCIPAL_ID,
        ),
        content_types=build_production_content_type_catalog(),
        schema_registry=build_production_content_schema_registry(),
        review_authorization=DevelopmentReviewAuthorizationPermit(),
        review_comment_policy=DevelopmentReviewCommentPermit(),
        publication_authorization=DevelopmentPublicationAuthorizationPermit(),
        publication_governance=DevelopmentPublicationGovernancePermit(),
        asset_reference_validation=DevelopmentAssetReferencePermit(),
        asset_current_governance=DevelopmentAssetCurrentUsePermit(),
        readiness_probe=SqlAlchemyApiReadinessProbe(engine, config),
        mutation_activation_gate=load_api_mutation_activation_gate_from_process_environment(
            config.release_identity
        ),
        principal_classification_authority=principal_classification_authority,
        student_learning_uow_factory=SqlAlchemyStudentLearningCommandUnitOfWorkFactory(
            engine
        ),
        school_intelligence_authorization=DevelopmentSchoolIntelligencePermit(),
        school_context_principal_scope_reader=(
            DevelopmentSchoolContextPrincipalScopeReader(
                tenant_id=LOCAL_DEV_TENANT_ID,
                principal_id=LOCAL_DEV_PRINCIPAL_ID,
            )
        ),
        school_intelligence_facts_reader=SqlAlchemySchoolIntelligenceFactsReader(
            engine
        ),
        parent_intelligence_authorization=parent_intelligence_authorization,
        school_context_parent_learner_access_reader=(
            school_context_parent_learner_access_reader
        ),
        parent_learner_integrity_authority=parent_learner_integrity_authority,
        parent_learner_access_service=parent_learner_access_service,
        parent_intelligence_facts_reader=SqlAlchemyParentIntelligenceFactsReader(
            engine,
            membership_reader=DevelopmentSchoolContextLearnerMembershipReader(
                tenant_id=LOCAL_DEV_TENANT_ID,
            ),
        ),
    )
