"""Shared fixtures for the backend test-suite.

The database URL is redirected to an isolated test database **before** any
``app.*`` import — :mod:`app.config` builds its singleton and
:mod:`app.engine.session` bootstraps the engine eagerly at import time.

The schema is created in a throw-away event loop and the engine pool is
disposed afterwards, so every connection used by the tests is created inside
the ``TestClient`` portal's own loop (asyncpg connections are loop-bound).
"""

from __future__ import annotations

import asyncio
import os

TEST_DB_NAME = "mt5_quant_test"
TEST_DB_USER = "mt5user"
TEST_DB_PASSWORD = "mt5password"
TEST_DB_HOSTPORT = "localhost:5432"

TEST_DATABASE_URL = (
    f"postgresql+asyncpg://{TEST_DB_USER}:{TEST_DB_PASSWORD}"
    f"@{TEST_DB_HOSTPORT}/{TEST_DB_NAME}"
)
# Provision with the SAME role the app uses.  CI's postgres service is created
# with POSTGRES_USER=mt5user only — there is no `postgres` superuser there, so
# the old admin URL made every backend test error out at connect.  mt5user
# needs just CREATEDB, which the CI service grants (POSTGRES_USER is a
# superuser) and the local portable cluster grants explicitly.
ADMIN_URL = (
    f"postgresql://{TEST_DB_USER}:{TEST_DB_PASSWORD}@{TEST_DB_HOSTPORT}/postgres"
)
TEST_DB_URL = (
    f"postgresql://{TEST_DB_USER}:{TEST_DB_PASSWORD}"
    f"@{TEST_DB_HOSTPORT}/{TEST_DB_NAME}"
)

# Must run before any test module imports ``app``.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
# Heartbeat must never poll the real gateway during tests.
os.environ["GATEWAY_HEARTBEAT_ENABLED"] = "false"
# 互斥探活同理（paper/live 创建会探测节点当前登录账户类型）。
os.environ["GATEWAY_MUTEX_PROBE_ENABLED"] = "false"
# Alert-rule loop is driven explicitly by tests (evaluate_once).
os.environ["ALERT_RULES_ENABLED"] = "false"
# Audit patrol is driven explicitly by tests (patrol_once).
os.environ["AUDIT_PATROL_ENABLED"] = "false"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


async def _prepare_schema() -> None:
    import asyncpg

    conn = await asyncpg.connect(ADMIN_URL)
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", TEST_DB_NAME
        )
        if not exists:
            await conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
    finally:
        await conn.close()

    # PG15+: the public schema is owned by pg_database_owner, so a database
    # created by mt5user already grants it via ownership.  A database left over
    # from an older run, owned by some other role, still needs the explicit
    # grant — best effort, because that owner may be the only one allowed to
    # grant on it.  Schema privileges are per-database, hence the separate
    # connection to the test DB itself.
    test_conn = await asyncpg.connect(TEST_DB_URL)
    try:
        try:
            await test_conn.execute(f"GRANT ALL ON SCHEMA public TO {TEST_DB_USER}")
        except asyncpg.PostgresError:
            pass  # already owned/usable — see comment above
    finally:
        await test_conn.close()

    from app.engine.session import get_engine
    from app.models import Base

    engine = get_engine()
    async with engine.begin() as conn_:
        await conn_.run_sync(Base.metadata.drop_all)
        await conn_.run_sync(Base.metadata.create_all)
        await conn_.run_sync(_seed_templates)
    await engine.dispose()  # drop loop-bound connections before TestClient runs


def _seed_templates(sync_conn) -> None:
    """Insert the built-in templates (the migration path is not used here)."""
    import sys
    from pathlib import Path

    from sqlalchemy import text

    shared_dir = str(Path(__file__).resolve().parents[2] / "shared")
    if shared_dir not in sys.path:
        sys.path.insert(0, shared_dir)
    from sdk.templates import seed_rows  # type: ignore

    stmt = text(
        "INSERT INTO strategy_templates "
        "(id, name, description, language, category, sort_order, parameters, source_code) "
        "VALUES (:id, :name, :description, :language, :category, :sort_order, "
        "CAST(:parameters AS jsonb), :source_code) "
        "ON CONFLICT (name) DO NOTHING"
    )
    import json

    for row in seed_rows():
        row = dict(row)
        row["parameters"] = json.dumps(row["parameters"])
        sync_conn.execute(stmt, row)


@pytest.fixture(scope="session", autouse=True)
def _test_database():
    asyncio.run(_prepare_schema())
    yield


@pytest.fixture(scope="session")
def client(_test_database):
    from app.main import app

    # Context manager runs the lifespan (init_db) and keeps all
    # requests on a single event loop.
    with TestClient(app) as test_client:
        yield test_client
