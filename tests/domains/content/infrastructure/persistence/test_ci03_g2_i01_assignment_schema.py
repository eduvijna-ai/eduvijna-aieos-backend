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
    assert "review_assignment_events" in insp.get_table_names(schema="content")
    assert "review_current_assignments" in insp.get_table_names(schema="content")
