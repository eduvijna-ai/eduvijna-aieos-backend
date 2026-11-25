"""Route PK-HINT governed-assignment functional tests to isolated ci03_g2 PG18."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.conftest import provision_runtime_grants


@pytest.fixture(scope="module")
def ci03_hint_governed_substrate(
    ci03_g2_bootstrap_engine,
) -> Iterator[None]:
    """One provision per module; evidence stays on disposable PG18 until container teardown."""
    provision_runtime_grants(ci03_g2_bootstrap_engine)
    yield