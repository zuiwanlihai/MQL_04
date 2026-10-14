"""Per-account gateway routing (M12).

MT5 is single-terminal / single-session: one gateway node (one MT5 terminal on
one host) can only be logged into ONE account at a time.  Different accounts
therefore live on different ``gateway_nodes`` — every run must address the node
its bound ``MT5Account`` sits on, never the global default ``GATEWAY_URL``.

These helpers resolve the node base URL + decrypted credentials for an account.
A missing account/node falls back to the global default so single-node legacy
deployments keep working unchanged.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import GatewayNode, MT5Account
from app.services import credentials


def node_base_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


async def _account_and_node(db: AsyncSession, account_id: uuid.UUID):
    return (await db.execute(
        select(MT5Account, GatewayNode)
        .join(GatewayNode, MT5Account.gateway_node_id == GatewayNode.id)
        .where(MT5Account.id == account_id)
    )).first()


async def resolve_node(
    db: AsyncSession, account_id: uuid.UUID | None
) -> GatewayNode | None:
    """The gateway node owning ``account_id``; None → global default."""
    if account_id is None:
        return None
    row = await _account_and_node(db, account_id)
    return None if row is None else row[1]


async def resolve_gateway(db: AsyncSession, account_id: uuid.UUID | None) -> str:
    """Base URL of the node owning ``account_id``; global default when absent."""
    node = await resolve_node(db, account_id)
    if node is None:
        return settings.GATEWAY_URL
    return node_base_url(node.host, node.port)


async def account_credentials(
    db: AsyncSession, account_id: uuid.UUID | None,
) -> tuple[int, str, str] | None:
    """Decrypted (login, password, server) for the account; None when unusable."""
    if account_id is None:
        return None
    account = await db.get(MT5Account, account_id)
    if account is None or not account.password_encrypted:
        return None
    try:
        password = credentials.decrypt_secret(account.password_encrypted)
    except credentials.CredentialDecryptionError:
        return None
    return account.login, password, account.server


async def probe_active_account_type(
    db: AsyncSession, node_id: uuid.UUID | None,
) -> str | None:
    """Type of the MT5 account the node is CURRENTLY logged into.

    Live probe of the node's ``GET /health``; used by the 实盘/模拟互斥
    double-insurance on paper/live creation.  Returns None when unknown —
    probe disabled, no node, EA-bridge node, or unreachable — in which case
    the persisted-row check is the only insurance (fail-open by design:
    creation must not hinge on gateway reachability).
    """
    if not settings.GATEWAY_MUTEX_PROBE_ENABLED or node_id is None:
        return None
    node = await db.get(GatewayNode, node_id)
    if node is None or node.type == "ea_bridge":
        return None
    from app.services.gateway_client import GatewayClient, GatewayError

    client = GatewayClient.for_registered_node(node_base_url(node.host, node.port))
    try:
        raw = (await client.health()).get("account") or {}
    except GatewayError:
        return None
    finally:
        await client.aclose()
    account_type = str(raw.get("type") or raw.get("account_type") or "").lower()
    return account_type or None
