"""
Account endpoints (M1): test connection, bind, list, sync, unbind.

Passwords are Fernet-encrypted at rest and never appear in any response.
Ownership is enforced through ``user_id`` (404 for foreign accounts).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, DBSession
from app.models import GatewayNode, MT5Account
from app.schemas.accounts import (
    AccountCredentials,
    AccountListResponse,
    AccountProbeResult,
    ActivateRequest,
    ActiveAccountInfo,
    ActiveAccountListResponse,
    AlgoTradingRequest,
    AlgoTradingResponse,
    MT5AccountOut,
    TerminalSyncInfo,
)
from app.services import credentials
from app.services.audit import AuditActor
from app.services.audit import record as audit_record
from app.services.gateway_client import (
    GatewayClient,
    GatewayError,
    GatewayLoginError,
    get_gateway_client,
)
from app.services.gateway_heartbeat import ensure_default_node
from app.services.gateway_route import node_base_url

router = APIRouter()

GatewayDep = Annotated[GatewayClient, Depends(get_gateway_client)]

_ACCOUNT_TYPES = ("demo", "real", "contest")


def _actor(current: dict, request: Request | None = None) -> AuditActor:
    ip = request.client.host if request is not None and request.client else ""
    return AuditActor(
        actor_type="user",
        actor_id=current["sub"],
        actor_label=str(current.get("email") or current["sub"])[:120],
        source_ip=ip or "",
    )


def _map_probe(raw: dict) -> AccountProbeResult:
    """Gateway login summary → probe schema (defensive defaults)."""
    account_type = str(raw.get("type") or raw.get("account_type") or "demo").lower()
    if account_type not in _ACCOUNT_TYPES:
        account_type = "demo"

    def _f(key: str) -> float:
        try:
            return float(raw.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    return AccountProbeResult(
        login=int(raw.get("login") or 0),
        server=str(raw.get("server") or ""),
        name=str(raw.get("name") or ""),
        account_type=account_type,  # type: ignore[arg-type]
        leverage=int(raw.get("leverage") or 0),
        currency=str(raw.get("currency") or "USD"),
        balance=_f("balance"),
        credit=_f("credit"),
        equity=_f("equity"),
        margin=_f("margin"),
        margin_free=_f("margin_free"),
        margin_level=_f("margin_level"),
        is_trade_allowed=bool(raw.get("trade_allowed", True)),
    )


async def _probe(
    payload: AccountCredentials, gateway: GatewayClient,
    db: AsyncSession | None = None,
) -> AccountProbeResult:
    """Run the gateway login; normalise errors to HTTP 400/503.

    M13: an explicit ``gateway_url`` wins; otherwise a selected
    ``gateway_node_id`` probes THAT node (in-flow binding for paper/live),
    otherwise the injected default client is used.
    """
    custom_url = payload.gateway_url
    if custom_url is None and payload.gateway_node_id is not None and db is not None:
        node = await db.get(GatewayNode, payload.gateway_node_id)
        if node is None:
            raise HTTPException(status_code=404, detail="网关节点不存在")
        custom_url = node_base_url(node.host, node.port)
    client = GatewayClient(custom_url) if custom_url else gateway
    try:
        raw = await client.login(payload.login, payload.password, payload.server)
    except GatewayLoginError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from exc
    except GatewayError as exc:
        raise HTTPException(status_code=503, detail=exc.detail) from exc
    finally:
        if custom_url:
            await client.aclose()
    return _map_probe(raw)


async def _get_owned(
    db: AsyncSession, account_id: uuid.UUID, current: dict
) -> MT5Account:
    account = await db.get(MT5Account, account_id)
    if account is None or str(account.user_id) != current["sub"]:
        raise HTTPException(status_code=404, detail="账户不存在")
    return account


@router.post("/test", response_model=AccountProbeResult)
async def test_account(
    payload: AccountCredentials,
    current: CurrentUser,
    gateway: GatewayDep,
    db: DBSession,
) -> AccountProbeResult:
    """Probe a gateway login without persisting anything."""
    return await _probe(payload, gateway, db)


@router.post("", response_model=MT5AccountOut, status_code=status.HTTP_201_CREATED)
async def bind_account(
    payload: AccountCredentials,
    current: CurrentUser,
    db: DBSession,
    gateway: GatewayDep,
) -> MT5AccountOut:
    """Bind an MT5 account: probe → duplicate check → encrypted persist.

    M13: when ``gateway_node_id`` is given (in-flow binding from the paper /
    live dialogs) the account lands on THAT node; otherwise the legacy default
    node is used.
    """
    probe = await _probe(payload, gateway, db)

    existing = await db.execute(
        select(MT5Account).where(
            MT5Account.login == probe.login, MT5Account.server == probe.server
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "该 MT5 账户已被某个平台账号绑定（全局唯一）。"
                "如果是你自己的其他平台账号绑的，请先用该账号在「登录 MT5 账户」页"
                "解绑，再回来绑定。"
            ),
        )

    if payload.gateway_node_id is not None:
        node = await db.get(GatewayNode, payload.gateway_node_id)
        if node is None:  # already guarded in _probe; belt for direct binds
            raise HTTPException(status_code=404, detail="网关节点不存在")
    else:
        node = await ensure_default_node(db)
    account = MT5Account(
        login=probe.login,
        server=probe.server,
        name=probe.name,
        account_type=probe.account_type,
        leverage=probe.leverage,
        currency=probe.currency,
        balance=probe.balance,
        credit=probe.credit,
        equity=probe.equity,
        margin=probe.margin,
        margin_free=probe.margin_free,
        margin_level=probe.margin_level,
        is_connected=True,
        is_trade_allowed=probe.is_trade_allowed,
        gateway_node_id=node.id,
        last_sync_at=datetime.now(timezone.utc),
        password_encrypted=(
            credentials.encrypt_secret(payload.password)
            if payload.remember_password
            else None
        ),
        user_id=uuid.UUID(current["sub"]),
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return MT5AccountOut.model_validate(account)


@router.get("", response_model=AccountListResponse)
async def list_accounts(
    current: CurrentUser,
    db: DBSession,
) -> AccountListResponse:
    """Accounts bound by the current user (password structurally excluded)."""
    result = await db.execute(
        select(MT5Account)
        .where(MT5Account.user_id == uuid.UUID(current["sub"]))
        .order_by(MT5Account.created_at)
    )
    accounts = list(result.scalars())
    return AccountListResponse(
        items=[MT5AccountOut.model_validate(a) for a in accounts],
        total=len(accounts),
    )


@router.post("/{account_id}/sync", response_model=MT5AccountOut)
async def sync_account(
    account_id: uuid.UUID,
    current: CurrentUser,
    db: DBSession,
    gateway: GatewayDep,
) -> MT5AccountOut:
    """Re-login through the gateway and refresh balance/equity/margin."""
    account = await _get_owned(db, account_id, current)
    if not account.password_encrypted:
        raise HTTPException(status_code=400, detail="凭据解密失败，请重新绑定")
    try:
        password = credentials.decrypt_secret(account.password_encrypted)
    except credentials.CredentialDecryptionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    probe = await _probe(
        AccountCredentials(
            login=account.login, password=password, server=account.server
        ),
        gateway,
    )

    account.balance = probe.balance
    account.credit = probe.credit
    account.equity = probe.equity
    account.margin = probe.margin
    account.margin_free = probe.margin_free
    account.margin_level = probe.margin_level
    account.leverage = probe.leverage
    account.is_connected = True
    account.is_trade_allowed = probe.is_trade_allowed
    account.last_sync_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(account)
    return MT5AccountOut.model_validate(account)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unbind_account(
    account_id: uuid.UUID,
    current: CurrentUser,
    db: DBSession,
) -> None:
    """Remove the binding (row only — the broker account is untouched)."""
    account = await _get_owned(db, account_id, current)
    await db.delete(account)
    await db.commit()


@router.post("/{account_id}/activate", response_model=MT5AccountOut)
async def activate_account(
    account_id: uuid.UUID,
    request: Request,
    current: CurrentUser,
    db: DBSession,
    gateway: GatewayDep,
    payload: ActivateRequest | None = None,
) -> MT5AccountOut:
    """Switch the terminal login to this account (gateway ``mt5.login``).

    Uses the stored encrypted credentials; accounts bound without
    「记住密码」 must carry ``password`` in the body (used once, never
    stored).  The gateway memorises the override so IPC reconnects stay on
    this account.  Audit-logged.
    """
    account = await _get_owned(db, account_id, current)
    probe = await activate_on_gateway(
        db, gateway, account,
        password_override=payload.password if payload else None,
    )

    await audit_record(
        "account_activate",
        actor=_actor(current, request),
        target_type="mt5_account",
        target_id=str(account.id),
        params={"login": account.login, "server": account.server,
                "account_type": probe.account_type},
        db=db,
    )
    await db.commit()
    await db.refresh(account)
    return MT5AccountOut.model_validate(account)


@router.post("/{account_id}/algo-trading", response_model=AlgoTradingResponse)
async def set_algo_trading(
    account_id: uuid.UUID,
    payload: AlgoTradingRequest,
    request: Request,
    current: CurrentUser,
    db: DBSession,
    gateway: GatewayDep,
) -> AlgoTradingResponse:
    """允许/禁止该 MT5 账户的算法（策略）交易，并同步终端本体按钮。

    平台闸：False 时 live manager 前置闸拒绝该账户所有策略自动下单
    （error_code=ALGO_TRADING_DISABLED）；手动交易不受限。写审计链
    （flag_change）。终端同步：通过网关给 MT5 终端窗口注入 Ctrl+E 拨动
    本体「算法交易」按钮并复读验证（EA 桥节点无网关 HTTP → 跳过）。
    """
    account = await _get_owned(db, account_id, current)
    if account.algo_trading_enabled != payload.enabled:
        account.algo_trading_enabled = payload.enabled
        await audit_record(
            "flag_change",
            actor=_actor(current, request),
            target_type="mt5_account",
            target_id=str(account.id),
            params={
                "flag": "algo_trading_enabled",
                "enabled": payload.enabled,
                "login": account.login,
                "server": account.server,
            },
            db=db,
        )
        await db.commit()
        await db.refresh(account)

    node = await db.get(GatewayNode, account.gateway_node_id)
    if node is not None and node.type == "ea_bridge":
        terminal_sync = TerminalSyncInfo(
            ok=True, trade_allowed=None,
            message="EA 桥节点的终端由本机 EA 控制，无法远程拨动终端按钮 — 平台开关已生效",
        )
    else:
        try:
            r = await gateway.set_terminal_algo(payload.enabled, timeout=15.0)
            terminal_sync = TerminalSyncInfo(
                ok=True,
                trade_allowed=r.get("trade_allowed"),
                message=r.get("message") or None,
            )
        except GatewayError as exc:
            terminal_sync = TerminalSyncInfo(
                ok=False, trade_allowed=None,
                message=str(exc),  # 前端统一加「平台开关已保存，但终端按钮切换失败：」前缀
            )
    return AlgoTradingResponse(
        account=MT5AccountOut.model_validate(account),
        terminal_sync=terminal_sync,
    )


async def activate_on_gateway(
    db: AsyncSession,
    gateway: GatewayClient,
    account: MT5Account,
    password_override: str | None = None,
) -> AccountProbeResult:
    """Switch the terminal login to ``account`` and refresh its mirror fields.

    Shared by ``POST /accounts/{id}/activate`` and the MT5-account login flow.
    EA 桥节点无 HTTP 服务（其终端即该账户）：跳过网关切登录，仅用绑定行
    合成快照。  Does NOT commit.

    凭据来源优先级：绑定行加密存储（「记住密码」）→ ``password_override``
    （本次请求携带、不落库）→ 400 要求输入密码。
    """
    node = (await db.get(GatewayNode, account.gateway_node_id)
            if account.gateway_node_id is not None else None)
    if node is not None and node.type == "ea_bridge":
        probe = _probe_from_row(account)
    else:
        if account.password_encrypted:
            try:
                password = credentials.decrypt_secret(account.password_encrypted)
            except credentials.CredentialDecryptionError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        elif password_override:
            password = password_override
        else:
            raise HTTPException(
                status_code=400,
                detail="该账户未记住密码：请输入账户密码后重试",
            )
        probe = await _probe(
            AccountCredentials(
                login=account.login,
                password=password,
                server=account.server,
                gateway_node_id=account.gateway_node_id,
            ),
            gateway,
            db,
        )

    account.balance = probe.balance
    account.credit = probe.credit
    account.equity = probe.equity
    account.margin = probe.margin
    account.margin_free = probe.margin_free
    account.margin_level = probe.margin_level
    account.leverage = probe.leverage
    account.is_connected = True
    account.is_trade_allowed = probe.is_trade_allowed
    account.last_sync_at = datetime.now(timezone.utc)
    return probe


def _probe_from_row(acct: MT5Account) -> AccountProbeResult:
    """EA 桥节点无 HTTP 服务：用绑定行合成当前账户快照（节点即该账户）。"""
    return AccountProbeResult(
        login=acct.login,
        server=acct.server,
        name=acct.name,
        account_type=acct.account_type,  # type: ignore[arg-type]
        leverage=acct.leverage,
        currency=acct.currency,
        balance=float(acct.balance),
        credit=float(acct.credit),
        equity=float(acct.equity),
        margin=float(acct.margin),
        margin_free=float(acct.margin_free),
        margin_level=float(acct.margin_level),
        is_trade_allowed=acct.is_trade_allowed,
    )


@router.get("/active", response_model=ActiveAccountListResponse)
async def active_gateways(
    current: CurrentUser,
    db: DBSession,
) -> ActiveAccountListResponse:
    """Currently logged-in MT5 account per gateway node.

    python 节点：live probe of ``GET /health``（网关重启回退 .env 默认账户，
    下一次探活如实反映）；EA 桥节点：无 HTTP 服务，用绑定行合成快照。
    Never persisted.
    """
    rows = (await db.execute(
        select(GatewayNode, MT5Account)
        .join(MT5Account, MT5Account.gateway_node_id == GatewayNode.id)
        .where(MT5Account.user_id == uuid.UUID(current["sub"]))
        .order_by(MT5Account.created_at)
    )).all()
    items: list[ActiveAccountInfo] = []
    seen: set[uuid.UUID] = set()
    for node, bound in rows:
        if node.id in seen:  # 多账户同节点：python 节点以首次探活为准
            continue
        seen.add(node.id)
        if node.type == "ea_bridge":
            summary: AccountProbeResult | None = _probe_from_row(bound)
        else:
            client = GatewayClient(node_base_url(node.host, node.port))
            summary = None
            try:
                raw = (await client.health()).get("account")
                if raw:
                    summary = _map_probe(raw)
            except GatewayError:
                summary = None  # 节点不可达 → 前端显示离线
            finally:
                await client.aclose()
        items.append(
            ActiveAccountInfo(node_id=node.id, node_name=node.name, account=summary)
        )
    return ActiveAccountListResponse(items=items)
