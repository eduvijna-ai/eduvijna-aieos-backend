"""AIEOS360-S01-I05-B2 — concurrent same-business evaluation ensure."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy.engine import Engine

from aieos.development.school_context import DevelopmentSchoolContextClassReader
from aieos.domains.assessment.application.audit import api_mutation_audit_provenance
from aieos.domains.assessment.application.evaluation_ensure import (
    EnsureLearnerAssessmentEvaluationService,
)
from aieos.domains.assessment.infrastructure.persistence.uow import (
    SqlAlchemyAssessmentUnitOfWorkFactory,
)
from aieos.domains.teaching.application.school_context import (
    SchoolContextClassAuthorityService,
)
from tests.domains.assessment.helpers_dev08_i02 import event_context
from tests.domains.assessment.helpers_s01_i05_b2 import (
    count_eval_audits,
    count_evaluations,
    seed_world,
)
from tests.fakes import AllowClassroomAssessmentAuthorization

pytestmark = pytest.mark.aieos360_s01_i05_b2


def test_30_concurrent_same_business_one_evaluation_one_audit(
    bootstrap_engine: Engine, runtime_engine: Engine
) -> None:
    world = seed_world(bootstrap_engine, runtime_engine)
    service = EnsureLearnerAssessmentEvaluationService(
        SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine),
        SchoolContextClassAuthorityService(
            DevelopmentSchoolContextClassReader(
                tenant_id=world.tenant_id,
                teacher_principal_id=world.teacher_id,
            )
        ),
        AllowClassroomAssessmentAuthorization(),
        idempotency_retention=timedelta(hours=24),
    )

    def _ensure(key: str):
        return service.ensure_submission(
            world.tenant_id,
            world.teacher_id,
            submission_id=world.submission_id,
            idempotency_key=key,
            event_context=event_context(world.teacher_id),
            audit_provenance=api_mutation_audit_provenance(world.teacher_id),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_ensure, "concurrent-a")
        second = pool.submit(_ensure, "concurrent-b")
        results = [first.result(), second.result()]

    assert results[0].evaluation_id == results[1].evaluation_id
    assert count_evaluations(bootstrap_engine, tenant_id=world.tenant_id) == 1
    assert count_eval_audits(bootstrap_engine, tenant_id=world.tenant_id) == 1
