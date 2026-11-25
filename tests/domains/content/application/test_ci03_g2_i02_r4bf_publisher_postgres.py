"""CI03-G2-I02R4-BF — publisher negatives (PG18)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.engine import Engine

from aieos.domains.content.application.audit import api_mutation_audit_provenance
from aieos.domains.content.application.errors import PublicationForbidden
from aieos.domains.content.application.ports import CONTENT_REVIEW_DECIDE
from aieos.platform.security.authorization import (
    AuthorizationKernel,
    CurrentPrincipalClassificationAuthority,
)
from aieos.platform.security.authorization.content_adapters import (
    AIEOS_CONTENT_CAPABILITIES,
    KernelPublicationAuthorization,
)
from aieos.platform.security.authorization.decisions import PrincipalKind
from tests.domains.content.application.helpers_ci03_g2_i02_r4b3r1 import (
    build_terminal_approved_scenario,
)
from tests.domains.content.application.test_ci03_g2_i01_assignment_postgres import (
    _NOW,
    _event,
)
from tests.domains.education.test_aieos_v1_hint_t1 import (
    build_preparation_content_schema_registry,
)
from tests.fakes import (
    AllowAssetCurrentGovernance,
    AllowPublicationGovernance,
    IDEMPOTENCY_RETENTION,
)
from aieos.domains.content.application.publish import PublishContentService
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from tests.platform.security.authorization.helpers import seed_active_authority

pytestmark = pytest.mark.ci03_g2_i02


def test_content_owner_without_publish_grant_denied_at_kernel(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
) -> None:
    scenario = build_terminal_approved_scenario(
        ci03_g2_bootstrap_engine, ci03_g2_runtime_engine
    )
    owner_like = uuid.uuid7()
    seed_active_authority(
        ci03_g2_bootstrap_engine,
        tenant_id=scenario.tenant_id,
        principal_id=owner_like,
        principal_kind=PrincipalKind.HUMAN,
        capabilities=(CONTENT_REVIEW_DECIDE,),
    )
    kernel = AuthorizationKernel(
        ci03_g2_runtime_engine, known_capabilities=AIEOS_CONTENT_CAPABILITIES
    )
    publish = PublishContentService(
        SqlAlchemyContentUnitOfWorkFactory(ci03_g2_runtime_engine),
        KernelPublicationAuthorization(kernel),
        AllowPublicationGovernance(),
        AllowAssetCurrentGovernance(),
        build_preparation_content_schema_registry(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
        principal_classification=CurrentPrincipalClassificationAuthority(
            ci03_g2_runtime_engine
        ),
    )
    with pytest.raises(PublicationForbidden):
        publish.publish(
            scenario.tenant_id,
            owner_like,
            content_id=scenario.content_id,
            version_id=scenario.version_id,
            expected_aggregate_revision=scenario.revision,
            idempotency_key=f"bf-owner-no-pub-{uuid.uuid7()}",
            event_context=_event(owner_like),
            audit_provenance=api_mutation_audit_provenance(owner_like),
            now=_NOW,
        )