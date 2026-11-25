"""Pre-binding-era hinted worksheet evidence via lawful INSERT (NULL review_assignment_id)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.domain.identities import AggregateRevision, ContentId, ContentVersionId
from aieos.domains.content.infrastructure.persistence.uow import SqlAlchemyContentUnitOfWorkFactory
from aieos.domains.education.worksheet_hint_digest import (
    worksheet_hint_attestation_digest_from_version_payload,
)
from aieos.domains.education.worksheet_hint_qa import WORKSHEET_HINT_QA_POLICY_VERSION
from tests.conftest import provision_runtime_grants
from tests.domains.content.application.helpers_ci03_g2_i01 import seed_worksheet_in_review_governed
from tests.domains.content.application.test_ci03_g2_i01_assignment_postgres import CLASS_REF
from tests.domains.education.test_aieos_v1_hint_t2 import (
    _hinted_ids_from_version,
    _positive_outcomes,
)


@dataclass(frozen=True, slots=True)
class HistoricalHintedApprovedScenario:
    tenant_id: uuid.UUID
    owner_id: uuid.UUID
    reviewer_id: uuid.UUID
    content_id: ContentId
    version_id: ContentVersionId
    revision: AggregateRevision
    attestation_id: uuid.UUID
    review_decision_id: uuid.UUID


def insert_pre_binding_hinted_attest_and_approve(
    bootstrap: Engine,
    runtime: Engine,
    *,
    now: datetime,
    tag: str,
) -> HistoricalHintedApprovedScenario:
    """Hinted worksheet@2, digest-bound attestation + APPROVE with NULL assignment binding."""
    provision_runtime_grants(bootstrap)
    tenant_id = uuid.uuid7()
    owner_id = uuid.uuid7()
    reviewer_id = uuid.uuid7()
    from tests.domains.content.application.test_ci03_g2_i02_mutation_postgres import (
        _seed_reviewers,
    )

    _seed_reviewers(bootstrap, tenant_id, owner_id, reviewer_id)
    content_id, version_id = seed_worksheet_in_review_governed(
        bootstrap,
        runtime,
        tenant_id=tenant_id,
        owner_id=owner_id,
        class_ref=CLASS_REF,
        now=now,
    )
    with runtime.connect() as conn:
        conn.execute(
            text("SELECT set_config('aieos.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        head_rev = int(
            conn.execute(
                text(
                    "SELECT aggregate_revision FROM content.contents WHERE content_id = :cid"
                ),
                {"cid": content_id.value},
            ).scalar_one()
        )
        slot_count = int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM content.review_current_assignments
                    WHERE tenant_id = :tid AND content_id = :cid AND version_id = :vid
                    """
                ),
                {"tid": tenant_id, "cid": content_id.value, "vid": version_id.value},
            ).scalar_one()
        )
    assert slot_count == 0
    with SqlAlchemyContentUnitOfWorkFactory(runtime)(tenant_id) as uow:
        version = uow.versions.get(version_id)
        assert version is not None
        payload_body = dict(version.payload.body)
        author_id = version.created_by_principal_id
    digest = worksheet_hint_attestation_digest_from_version_payload(payload_body)
    question_ids = _hinted_ids_from_version(runtime, tenant_id, version_id)
    outcomes = _positive_outcomes(question_ids)
    outcomes_json = json.dumps(outcomes)
    attestation_id = uuid.uuid7()
    decision_id = uuid.uuid7()
    attest_corr = uuid.uuid7()
    decision_corr = uuid.uuid7()
    with bootstrap.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO content.worksheet_learner_hint_version_attestations (
                    attestation_id, tenant_id, content_id, version_id,
                    payload_digest, qa_policy_version, author_principal_id,
                    reviewer_principal_id, per_question_outcomes, attested_at,
                    correlation_id, review_assignment_id
                ) VALUES (
                    :att_id, :tid, :cid, :vid,
                    :digest, :qa, :owner, :reviewer,
                    CAST(:outcomes AS jsonb), :now, :corr, NULL
                )
                """
            ),
            {
                "att_id": attestation_id,
                "tid": tenant_id,
                "cid": content_id.value,
                "vid": version_id.value,
                "digest": digest,
                "qa": WORKSHEET_HINT_QA_POLICY_VERSION,
                "owner": author_id,
                "reviewer": reviewer_id,
                "outcomes": outcomes_json,
                "now": now,
                "corr": attest_corr,
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO content.review_decisions (
                    review_decision_id, tenant_id, content_id, version_id,
                    decision, reason_code, comment, reviewer_principal_id,
                    effective_actor_id, delegation_id, decided_at, correlation_id,
                    review_assignment_id
                ) VALUES (
                    :did, :tid, :cid, :vid, 'APPROVE', NULL, NULL,
                    :reviewer, :reviewer, NULL, :now, :corr, NULL
                )
                """
            ),
            {
                "did": decision_id,
                "tid": tenant_id,
                "cid": content_id.value,
                "vid": version_id.value,
                "reviewer": reviewer_id,
                "now": now,
                "corr": decision_corr,
            },
        )
        new_rev = head_rev + 1
        conn.execute(
            text(
                """
                UPDATE content.contents
                SET stewardship_state = 'APPROVED',
                    aggregate_revision = :rev
                WHERE tenant_id = :tid AND content_id = :cid
                """
            ),
            {"rev": new_rev, "tid": tenant_id, "cid": content_id.value},
        )
    return HistoricalHintedApprovedScenario(
        tenant_id=tenant_id,
        owner_id=owner_id,
        reviewer_id=reviewer_id,
        content_id=content_id,
        version_id=version_id,
        revision=AggregateRevision(new_rev),
        attestation_id=attestation_id,
        review_decision_id=decision_id,
    )