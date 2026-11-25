"""CI03-G2-I02R4-BF — migration refusal + runtime privilege (PG18)."""

from __future__ import annotations

import uuid

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, OperationalError

from aieos.domains.content.application.audit import api_mutation_audit_provenance
from tests.conftest import alembic_config, provision_runtime_grants
from tests.domains.content.application.helpers_ci03_g2_i01 import integrated_attest_service
from tests.domains.content.application.test_ci03_g2_i01_assignment_postgres import (
    _NOW,
    _event,
)
from tests.domains.content.application.test_ci03_g2_i02_r4b1_acceptance_postgres import (
    _bound_hint_scenario,
)
from tests.domains.education.test_aieos_v1_hint_t2 import (
    _hinted_ids_from_version,
    _positive_outcomes,
)

pytestmark = pytest.mark.ci03_g2_i02


def test_downgrade_refuses_when_hint_attestation_receipt_ids_exist(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
    ci03_g2_postgres18: dict[str, str],
) -> None:
    provision_runtime_grants(ci03_g2_bootstrap_engine)
    (
        tenant_id,
        _owner,
        reviewer_id,
        provider,
        _revoke,
        _assignment_id,
        content_id,
        version_id,
        revision,
    ) = _bound_hint_scenario(
        ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, clock=lambda: _NOW
    )
    integrated_attest_service(
        ci03_g2_runtime_engine,
        tenant_id,
        clock=lambda: _NOW,
        school_context_provider=provider,
    ).record(
        tenant_id,
        reviewer_id,
        content_id=content_id,
        version_id=version_id,
        expected_aggregate_revision=revision,
        per_question_outcomes=_positive_outcomes(
            _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
        ),
        idempotency_key=f"bf-downgrade-rcpt-{uuid.uuid7()}",
        event_context=_event(reviewer_id),
        audit_provenance=api_mutation_audit_provenance(reviewer_id),
        now=_NOW,
    )
    with ci03_g2_bootstrap_engine.connect() as conn:
        receipt_rows = int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM api.idempotency_records
                    WHERE result_worksheet_hint_attestation_id IS NOT NULL
                      AND tenant_id = :tid
                    """
                ),
                {"tid": tenant_id},
            ).scalar_one()
        )
    assert receipt_rows >= 1
    cfg = alembic_config(ci03_g2_postgres18["migrator_url"])
    with pytest.raises(Exception, match="refused"):
        command.downgrade(cfg, "ci03g020001")
    with ci03_g2_bootstrap_engine.connect() as conn:
        head = conn.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        assert head == "ci03g020002"


def test_runtime_role_cannot_update_immutable_attestation_row(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
) -> None:
    (
        tenant_id,
        _owner,
        reviewer_id,
        provider,
        _revoke,
        _assignment_id,
        content_id,
        version_id,
        revision,
    ) = _bound_hint_scenario(
        ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, clock=lambda: _NOW
    )
    integrated_attest_service(
        ci03_g2_runtime_engine,
        tenant_id,
        clock=lambda: _NOW,
        school_context_provider=provider,
    ).record(
        tenant_id,
        reviewer_id,
        content_id=content_id,
        version_id=version_id,
        expected_aggregate_revision=revision,
        per_question_outcomes=_positive_outcomes(
            _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
        ),
        idempotency_key=f"bf-priv-{uuid.uuid7()}",
        event_context=_event(reviewer_id),
        audit_provenance=api_mutation_audit_provenance(reviewer_id),
        now=_NOW,
    )
    with pytest.raises(
        (OperationalError, DBAPIError),
        match="immutable|permission denied",
    ):
        with ci03_g2_runtime_engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            conn.execute(
                text(
                    """
                    UPDATE content.worksheet_learner_hint_version_attestations
                    SET payload_digest = :bad
                    WHERE tenant_id = :tid AND version_id = :vid
                    """
                ),
                {"bad": "f" * 64, "tid": tenant_id, "vid": version_id.value},
            )