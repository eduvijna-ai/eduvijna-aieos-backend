"""AIEOS360-S03-I02 — Parent Intelligence SQL facts adapter."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.engine import Engine

from aieos.domains.learning.application.learner_membership import (
    CurrentLearnerClassMembership,
)
from aieos.domains.parent_intelligence.application.errors import (
    ParentIntelligenceCapacityExceeded,
    ParentIntelligenceReadUnavailable,
)
from aieos.domains.parent_intelligence.application.models import (
    ATTEMPT_STATUS_IN_PROGRESS,
    ATTEMPT_STATUS_NOT_STARTED,
    ATTEMPT_STATUS_SUBMITTED,
    MAX_ASSIGNMENTS_PER_LEARNER,
)
from aieos.domains.parent_intelligence.infrastructure.read_projection import (
    SqlAlchemyParentIntelligenceFactsReader,
)
from aieos.platform.security.authorization import (
    AIEOS_CONTENT_CAPABILITIES,
    AuthorizationKernel,
    CurrentPrincipalClassificationAuthority,
    KernelParentIntelligenceAuthorization,
)
from aieos.platform.security.authorization.decisions import PrincipalKind
from tests.domains.parent_intelligence.helpers_s03_i02 import (
    CLASS_REF_HOME,
    CLASS_REF_OTHER,
    FIXED_NOW,
    FORBIDDEN_BODY_TOKENS,
    HOME_PATH,
    MutableParentAccessReader,
    RecordingMembershipReader,
    assert_dependent_fact_sources_not_queried,
    assert_exact_response_keys,
    build_client,
    capture_sql_statements,
    headers,
    insert_assignment,
    insert_in_progress_attempt,
    insert_submitted_attempt,
    seed_content,
    seed_human_adult_with_capability,
    seed_human_learner,
)
from aieos.domains.parent_intelligence.application.ports import (
    AIEOS_PARENT_INTELLIGENCE_CAPABILITIES,
)
from tests.platform.security.authorization.helpers import seed_active_authority

pytestmark = pytest.mark.aieos360_s03_i02

FUTURE = datetime.now(UTC) + timedelta(days=7)
PAST_DUE = datetime.now(UTC) - timedelta(days=3)
AVAILABLE = datetime.now(UTC) - timedelta(hours=1)


def _kernel_auth(engine: Engine) -> KernelParentIntelligenceAuthorization:
    return KernelParentIntelligenceAuthorization(
        AuthorizationKernel(
            engine,
            known_capabilities=AIEOS_CONTENT_CAPABILITIES
            | AIEOS_PARENT_INTELLIGENCE_CAPABILITIES,
        )
    )


def _membership(learner_id, class_ref: str = CLASS_REF_HOME) -> RecordingMembershipReader:
    return RecordingMembershipReader(
        {
            learner_id: (
                CurrentLearnerClassMembership(class_ref=class_ref),
            )
        }
    )


class TestAssignmentVisibility:
    def test_current_assignment_visibility_and_attempt_status(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        adult_id = uuid.uuid7()
        teacher_id = uuid.uuid7()
        learner_id = uuid.uuid7()
        other_learner = uuid.uuid7()
        seed_human_adult_with_capability(
            bootstrap_engine, tenant_id=tenant_id, principal_id=adult_id
        )
        seed_human_learner(
            bootstrap_engine, tenant_id=tenant_id, learner_id=learner_id
        )
        seed_human_learner(
            bootstrap_engine, tenant_id=tenant_id, learner_id=other_learner
        )
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=teacher_id,
            principal_kind=PrincipalKind.HUMAN,
            capabilities=(),
        )
        content_id, version_id = seed_content(
            bootstrap_engine, tenant_id=tenant_id, owner_id=teacher_id
        )
        visible = insert_assignment(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_HOME,
            available_from=AVAILABLE,
            due_at=PAST_DUE,
        )
        insert_assignment(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_HOME,
            available_from=FUTURE,
        )
        insert_assignment(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_HOME,
            lifecycle_state="CLOSED",
            available_from=AVAILABLE,
        )
        insert_assignment(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_HOME,
            lifecycle_state="CANCELLED",
            available_from=AVAILABLE,
        )
        insert_assignment(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_OTHER,
            available_from=AVAILABLE,
        )
        client = build_client(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=adult_id,
            access_reader=MutableParentAccessReader((learner_id,)),
            parent_intelligence_authorization=_kernel_auth(bootstrap_engine),
            membership_reader=_membership(learner_id),
            principal_classification_authority=CurrentPrincipalClassificationAuthority(
                bootstrap_engine
            ),
        )
        response = client.get(HOME_PATH, headers=headers(tenant_id))
        assert response.status_code == 200
        body = response.json()
        assert_exact_response_keys(body)
        assignments = body["children"][0]["assignments"]
        assert [item["assignment_id"] for item in assignments] == [str(visible)]
        assignment = assignments[0]
        assert assignment["title"] == "Title"
        assert assignment["content_type"]
        assert assignment["attempt_status"] == ATTEMPT_STATUS_NOT_STARTED
        assert assignment["submitted_at"] is None
        assert assignment["due_at"] is not None
        dumped = response.text.lower()
        for token in ("question", "payload", "secret", "MustNotLeak".lower()):
            assert token not in dumped
        for token in FORBIDDEN_BODY_TOKENS:
            assert token.lower() not in dumped
        assert str(other_learner) not in response.text

    def test_in_progress_and_submitted_attempt_status(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        adult_id = uuid.uuid7()
        teacher_id = uuid.uuid7()
        in_progress_learner = uuid.uuid7()
        submitted_learner = uuid.uuid7()
        seed_human_adult_with_capability(
            bootstrap_engine, tenant_id=tenant_id, principal_id=adult_id
        )
        for learner_id in (in_progress_learner, submitted_learner):
            seed_human_learner(
                bootstrap_engine, tenant_id=tenant_id, learner_id=learner_id
            )
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=teacher_id,
            principal_kind=PrincipalKind.HUMAN,
            capabilities=(),
        )
        content_id, version_id = seed_content(
            bootstrap_engine, tenant_id=tenant_id, owner_id=teacher_id
        )
        assignment_a = insert_assignment(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_HOME,
            available_from=AVAILABLE,
        )
        assignment_b = insert_assignment(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_HOME,
            available_from=AVAILABLE,
        )
        insert_in_progress_attempt(
            bootstrap_engine,
            tenant_id=tenant_id,
            learner_id=in_progress_learner,
            assignment_id=assignment_a,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_HOME,
        )
        _, _ = insert_submitted_attempt(
            bootstrap_engine,
            tenant_id=tenant_id,
            learner_id=submitted_learner,
            assignment_id=assignment_b,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_HOME,
            submitted_at=AVAILABLE,
        )
        membership = RecordingMembershipReader(
            {
                in_progress_learner: (
                    CurrentLearnerClassMembership(class_ref=CLASS_REF_HOME),
                ),
                submitted_learner: (
                    CurrentLearnerClassMembership(class_ref=CLASS_REF_HOME),
                ),
            }
        )
        client = build_client(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=adult_id,
            access_reader=MutableParentAccessReader(
                (in_progress_learner, submitted_learner)
            ),
            parent_intelligence_authorization=_kernel_auth(bootstrap_engine),
            membership_reader=membership,
            principal_classification_authority=CurrentPrincipalClassificationAuthority(
                bootstrap_engine
            ),
        )
        body = client.get(HOME_PATH, headers=headers(tenant_id)).json()
        by_learner = {
            child["learner_principal_id"]: child for child in body["children"]
        }
        in_progress_status = {
            item["assignment_id"]: item
            for item in by_learner[str(in_progress_learner)]["assignments"]
        }
        submitted_status = {
            item["assignment_id"]: item
            for item in by_learner[str(submitted_learner)]["assignments"]
        }
        assert in_progress_status[str(assignment_a)]["attempt_status"] == (
            ATTEMPT_STATUS_IN_PROGRESS
        )
        assert in_progress_status[str(assignment_a)]["submitted_at"] is None
        assert submitted_status[str(assignment_b)]["attempt_status"] == (
            ATTEMPT_STATUS_SUBMITTED
        )
        assert submitted_status[str(assignment_b)]["submitted_at"] is not None
        assert "secret" not in str(body).lower()
        assert "question_id" not in str(body)

    def test_submitted_without_submission_evidence_is_503(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        adult_id = uuid.uuid7()
        teacher_id = uuid.uuid7()
        learner_id = uuid.uuid7()
        seed_human_adult_with_capability(
            bootstrap_engine, tenant_id=tenant_id, principal_id=adult_id
        )
        seed_human_learner(
            bootstrap_engine, tenant_id=tenant_id, learner_id=learner_id
        )
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=teacher_id,
            principal_kind=PrincipalKind.HUMAN,
            capabilities=(),
        )
        content_id, version_id = seed_content(
            bootstrap_engine, tenant_id=tenant_id, owner_id=teacher_id
        )
        assignment_id = insert_assignment(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_HOME,
            available_from=AVAILABLE,
        )
        insert_submitted_attempt(
            bootstrap_engine,
            tenant_id=tenant_id,
            learner_id=learner_id,
            assignment_id=assignment_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_HOME,
            include_submission=False,
        )
        client = build_client(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=adult_id,
            access_reader=MutableParentAccessReader((learner_id,)),
            parent_intelligence_authorization=_kernel_auth(bootstrap_engine),
            membership_reader=_membership(learner_id),
            principal_classification_authority=CurrentPrincipalClassificationAuthority(
                bootstrap_engine
            ),
        )
        response = client.get(HOME_PATH, headers=headers(tenant_id))
        assert response.status_code == 503
        assert response.json()["code"] == "parent_intelligence_unavailable"
        assert "SUBMITTED" not in response.text

    def test_sql_adapter_completeness_and_authorized_filter(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        learner_id = uuid.uuid7()
        seed_human_learner(
            bootstrap_engine, tenant_id=tenant_id, learner_id=learner_id
        )
        reader = SqlAlchemyParentIntelligenceFactsReader(
            runtime_engine,
            membership_reader=_membership(learner_id),
        )
        snapshot = reader.read_authorized_learner_facts(
            tenant_id=tenant_id,
            authorized_learner_principal_ids=(learner_id,),
            observed_at=FIXED_NOW,
        )
        assert snapshot.generated_at == FIXED_NOW
        assert [row.learner_principal_id for row in snapshot.learners] == [learner_id]
        assert snapshot.learners[0].assignments == ()
        with pytest.raises(ParentIntelligenceReadUnavailable):
            SqlAlchemyParentIntelligenceFactsReader(
                runtime_engine,
                membership_reader=_membership(learner_id),
            ).read_authorized_learner_facts(
                tenant_id=tenant_id,
                authorized_learner_principal_ids=(learner_id,),
                observed_at=datetime(2026, 9, 17, 12, 0),
            )


class TestBoundedAssignmentReads:
    def test_one_learner_over_capacity_is_503_before_dependent_reads(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        adult_id = uuid.uuid7()
        teacher_id = uuid.uuid7()
        learner_id = uuid.uuid7()
        seed_human_adult_with_capability(
            bootstrap_engine, tenant_id=tenant_id, principal_id=adult_id
        )
        seed_human_learner(
            bootstrap_engine, tenant_id=tenant_id, learner_id=learner_id
        )
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=teacher_id,
            principal_kind=PrincipalKind.HUMAN,
            capabilities=(),
        )
        content_id, version_id = seed_content(
            bootstrap_engine, tenant_id=tenant_id, owner_id=teacher_id
        )
        over_capacity = MAX_ASSIGNMENTS_PER_LEARNER + 1
        assignment_ids = [
            insert_assignment(
                bootstrap_engine,
                tenant_id=tenant_id,
                teacher_id=teacher_id,
                content_id=content_id,
                content_version_id=version_id,
                class_ref=CLASS_REF_HOME,
                available_from=AVAILABLE,
            )
            for _ in range(over_capacity)
        ]
        insert_in_progress_attempt(
            bootstrap_engine,
            tenant_id=tenant_id,
            learner_id=learner_id,
            assignment_id=assignment_ids[0],
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_HOME,
        )
        client = build_client(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=adult_id,
            access_reader=MutableParentAccessReader((learner_id,)),
            parent_intelligence_authorization=_kernel_auth(bootstrap_engine),
            membership_reader=_membership(learner_id),
            principal_classification_authority=CurrentPrincipalClassificationAuthority(
                bootstrap_engine
            ),
        )
        with capture_sql_statements(runtime_engine) as statements:
            response = client.get(HOME_PATH, headers=headers(tenant_id))
        assert response.status_code == 503
        body = response.json()
        assert body["code"] == "parent_intelligence_unavailable"
        assert "children" not in body
        joined = "\n".join(statements).lower()
        assert "from teaching.assignments" in joined
        assert_dependent_fact_sources_not_queried(statements)

    def test_per_learner_over_capacity_is_503_before_dependent_reads(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        adult_id = uuid.uuid7()
        teacher_id = uuid.uuid7()
        over_capacity_learner = uuid.uuid7()
        empty_learner = uuid.uuid7()
        seed_human_adult_with_capability(
            bootstrap_engine, tenant_id=tenant_id, principal_id=adult_id
        )
        for learner_id in (over_capacity_learner, empty_learner):
            seed_human_learner(
                bootstrap_engine, tenant_id=tenant_id, learner_id=learner_id
            )
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=teacher_id,
            principal_kind=PrincipalKind.HUMAN,
            capabilities=(),
        )
        content_id, version_id = seed_content(
            bootstrap_engine, tenant_id=tenant_id, owner_id=teacher_id
        )
        over_capacity = MAX_ASSIGNMENTS_PER_LEARNER + 1
        available_at = FIXED_NOW - timedelta(hours=1)
        assignment_ids = [
            insert_assignment(
                bootstrap_engine,
                tenant_id=tenant_id,
                teacher_id=teacher_id,
                content_id=content_id,
                content_version_id=version_id,
                class_ref=CLASS_REF_HOME,
                available_from=available_at,
            )
            for _ in range(over_capacity)
        ]
        insert_submitted_attempt(
            bootstrap_engine,
            tenant_id=tenant_id,
            learner_id=over_capacity_learner,
            assignment_id=assignment_ids[0],
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_HOME,
        )
        membership = RecordingMembershipReader(
            {
                over_capacity_learner: (
                    CurrentLearnerClassMembership(class_ref=CLASS_REF_HOME),
                ),
                empty_learner: (
                    CurrentLearnerClassMembership(class_ref=CLASS_REF_OTHER),
                ),
            }
        )
        reader = SqlAlchemyParentIntelligenceFactsReader(
            runtime_engine, membership_reader=membership
        )
        with capture_sql_statements(runtime_engine) as statements:
            with pytest.raises(ParentIntelligenceCapacityExceeded):
                reader.read_authorized_learner_facts(
                    tenant_id=tenant_id,
                    authorized_learner_principal_ids=(
                        over_capacity_learner,
                        empty_learner,
                    ),
                    observed_at=FIXED_NOW,
                )
        joined = "\n".join(statements).lower()
        assert "from teaching.assignments" in joined
        assert_dependent_fact_sources_not_queried(statements)
        client = build_client(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=adult_id,
            access_reader=MutableParentAccessReader(
                (over_capacity_learner, empty_learner)
            ),
            parent_intelligence_authorization=_kernel_auth(bootstrap_engine),
            membership_reader=membership,
            principal_classification_authority=CurrentPrincipalClassificationAuthority(
                bootstrap_engine
            ),
        )
        with capture_sql_statements(runtime_engine) as http_statements:
            response = client.get(HOME_PATH, headers=headers(tenant_id))
        assert response.status_code == 503
        assert response.json()["code"] == "parent_intelligence_unavailable"
        assert "children" not in response.json()
        assert_dependent_fact_sources_not_queried(http_statements)
