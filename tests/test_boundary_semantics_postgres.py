"""Versioned boundary behavior on real isolated PostgreSQL."""

import os

import pytest

from tests.test_boundary_semantics_v2 import (
    test_boundary_policy_is_frozen_through_worker_and_report as check_boundary,
)
from tests.test_review_bound_postgres import postgres_setup as postgres_setup
from tests.test_review_bound_postgres import setup as setup

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"), reason="Requires isolated PostgreSQL"
)


@pytest.mark.parametrize("group", ["fixed", "agent"])
@pytest.mark.parametrize("version", [1, 2])
async def test_postgres_boundary_version_is_frozen(setup, group, version):
    await check_boundary(setup, group, version)
