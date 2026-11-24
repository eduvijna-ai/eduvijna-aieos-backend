"""CI03-G2-I01 assignment schema, RLS, immutability."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

pytestmark = pytest.mark.ci03_g2_i01


def test_head_and_tables(bootstrap_engine: Engine) -> None:
    with bootstrap_engine.connect() as conn:
        assert (
            conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "ci03g020001"
        )
    insp = inspect(bootstrap_engine)
    names = set(insp.get_table_names(schema="content"))
    assert "review_assignment_issuances" in names
    assert "review_assignment_revocations" in names
    assert "review_current_assignments" in names
    security_names = set(insp.get_table_names(schema="security"))
    assert "authorization_denial_records" in security_names
