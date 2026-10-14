"""on_tick 数据面公共取数 helper（paper/live 共用，SDK v1.1.0 / P4 EB-1）。

两条 tick 来源，游标语义一致（``last_tick_msc``，只发 ``> cursor`` 的行）：
  * python 网关节点（方案 A）— ``GatewayClient.ticks_range`` 轮询，秒级粒度；
  * ea_bridge 节点（方案 B）— 读 :mod:`app.services.ea_bridge` 注册表缓冲，
    EA 每次 sync 推入，破坏性取出（去重仍由调用方游标兜底）。
"""

from __future__ import annotations

from app.config import settings
from app.services.gateway_client import GatewayClient


async def fetch_gateway_ticks(
    client: GatewayClient, symbol: str, start_s: int, now_s: int,
) -> list[list]:
    """python 节点：网关 ticks/range → ``[[time_msc, bid, ask], …]``。"""
    rows = await client.ticks_range(
        symbol, start_s, now_s, limit=settings.TICK_GATEWAY_LIMIT,
    )
    return [[int(r["time_msc"]), float(r["bid"]), float(r["ask"])] for r in rows]


def fetch_ea_buffer_ticks(node_id: str, symbol: str) -> list[list]:
    """ea_bridge 节点：取出注册表缓冲的 tick（同步、破坏性）。"""
    from app.services.ea_bridge import registry  # 局部导入避免环

    return registry.take_ticks(node_id, symbol)
