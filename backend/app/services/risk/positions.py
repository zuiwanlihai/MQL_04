"""持仓登记表同步：终端按日期历史同步失效时的可靠风控数据源。

每次下单前（手动链路 _guard / 实盘管理器 order RPC）调用：
1. upsert 当前全部持仓票号；
2. 已登记但当前不在持仓列表的 → 视为已平仓，按票号直查成交
   （``history_deals_get(position=...)`` 服务端可靠）得到平仓净盈亏；
3. 返回 (closed_trades, open_times)：
   - closed_trades: [{time, profit}] 按平仓时间倒序（供连亏冷却重建连亏序列）；
   - open_times: 全部登记持仓的开仓时间（供窗口限频兜底计数）。

任何一步失败都返回 (None, None)，不阻断下单链路（引擎回退 deals 路径）。
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import cast

from sqlalchemy import select

from app.engine.session import AsyncSessionLocal
from app.models import RiskPositionLog
from app.services.gateway_client import GatewayClient

logger = logging.getLogger("mt5quant.risk.positions")

_MAX_ROWS_PER_ACCOUNT = 2000  # 安全上限：单账户登记超过此数先裁最旧


async def register_opened_position(
    account_id: str, ticket: int | None, symbol: str, open_time: int | None = None,
) -> None:
    """下单成功后立即登记票号（解决"开平都在两次风控检查之间"的盲区）。"""
    if not ticket or ticket <= 0:
        return
    try:
        async with AsyncSessionLocal() as db:
            exists = (
                await db.execute(
                    select(RiskPositionLog.id).where(
                        RiskPositionLog.account_id == uuid.UUID(account_id),
                        RiskPositionLog.ticket == int(ticket),
                    )
                )
            ).scalar_one_or_none()
            if exists is None:
                db.add(RiskPositionLog(
                    # SQLAlchemy 类型注解把 UUID 列映射为 str；cast 绕过。
                    account_id=cast(str, uuid.UUID(account_id)),
                    ticket=int(ticket),
                    symbol=symbol[:32], open_time=int(open_time or time.time()),
                ))
                await db.commit()
    except Exception:  # noqa: BLE001 — 登记失败不影响交易结果返回
        logger.exception("risk registry: register ticket %s failed", ticket)


async def sync_position_registry(
    client: GatewayClient, account_id: str, positions: list[dict] | None,
) -> tuple[list[dict] | None, list[int] | None]:
    """同步登记表并返回 (closed_trades_desc, open_times)；失败返回 (None, None)。"""
    if positions is None:
        return None, None
    acct = uuid.UUID(account_id)
    try:
        async with AsyncSessionLocal() as db:
            known = (
                await db.execute(
                    select(RiskPositionLog).where(RiskPositionLog.account_id == acct)
                )
            ).scalars().all()
            known_map = {row.ticket: row for row in known}

            now_positions = {int(p.get("ticket", 0)): p for p in positions if p.get("ticket")}

            # 1) upsert 当前持仓
            new_rows: list[RiskPositionLog] = []
            for ticket, p in now_positions.items():
                if ticket in known_map:
                    continue
                new_rows.append(RiskPositionLog(
                    # SQLAlchemy 类型注解把 UUID 列映射为 str；cast 绕过。
                    account_id=cast(str, acct),
                    ticket=ticket,
                    symbol=str(p.get("symbol") or ""),
                    open_time=int(p.get("time") or 0),
                ))
            if new_rows:
                db.add_all(new_rows)

            # 2) 已平仓：不在当前持仓列表的未结记录 → 按票号补查成交
            newly_closed = 0
            for row in known:
                if row.closed_at is not None or row.ticket in now_positions:
                    continue
                deals = await client.deals(0, 0, position=row.ticket)
                out_deals = [d for d in deals if int(d.get("entry", -1)) in (1, 3)]
                if not out_deals:
                    continue  # 查不到平仓成交（极端时序），下次再补
                in_deals = [d for d in deals if int(d.get("entry", -1)) in (0, 2)]
                if in_deals and not row.open_time:
                    row.open_time = int(in_deals[-1].get("time") or 0)
                row.closed_at = max(int(d.get("time") or 0) for d in out_deals)
                row.closed_profit = round(sum(
                    float(d.get("profit") or 0.0)
                    + float(d.get("swap") or 0.0)
                    + float(d.get("commission") or 0.0)
                    for d in out_deals
                ), 2)
                newly_closed += 1

            await db.commit()

            # 3) 读出结果
            rows = (
                await db.execute(
                    select(RiskPositionLog).where(RiskPositionLog.account_id == acct)
                )
            ).scalars().all()
            if len(rows) > _MAX_ROWS_PER_ACCOUNT:
                rows = sorted(rows, key=lambda r: r.open_time)[-_MAX_ROWS_PER_ACCOUNT:]
            closed_trades = [
                {"time": r.closed_at, "profit": float(r.closed_profit or 0.0),
                 "ticket": r.ticket, "symbol": r.symbol}
                for r in rows if r.closed_at is not None
            ]
            closed_trades.sort(key=lambda d: cast(int, d["time"]), reverse=True)
            open_times = [r.open_time for r in rows]
            if newly_closed:
                logger.info("risk registry: acct=%s closed=%d total=%d",
                            account_id, newly_closed, len(rows))
            return closed_trades, open_times
    except Exception:  # noqa: BLE001 — 登记失败不阻断下单
        logger.exception("risk registry sync failed for acct=%s", account_id)
        return None, None
