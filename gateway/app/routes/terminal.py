"""POST /terminal/algo-trading — 拨动 MT5 终端本体的「算法交易」按钮。

MT5 无编程切换 AutoTrading 的 API → 网关以同机身份：读当前状态 → 未达标时
前台化终端窗口注入 Ctrl+E → 复读 ``terminal_info().trade_allowed`` 验证。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException

from app import win32_keys
from app.mt5_client import client
from app.schemas import TerminalAlgoRequest, TerminalAlgoResult
from app.security import require_gateway_key

logger = logging.getLogger("gateway.terminal")

router = APIRouter(prefix="/terminal", dependencies=[Depends(require_gateway_key)])

_VERIFY_ROUNDS = 12
_VERIFY_INTERVAL = 0.25  # 3s 内轮询确认按钮状态翻转
_REINJECT_AT = 7  # ~1.75s 仍未翻转 → 补发一次 Ctrl+E（偶发焦点竞争）


def _read_trade_allowed() -> bool:
    t = client._mt5.terminal_info()  # noqa: SLF001 — 与路由层其他 sync 读一致
    if t is None:
        raise HTTPException(status_code=409, detail="MT5 终端不可达")
    return bool(getattr(t, "trade_allowed", False))


def _inject_once() -> None:
    hwnd = win32_keys.find_terminal_window()
    if hwnd is None:
        raise HTTPException(
            status_code=502,
            detail="未找到 MT5 终端窗口 — 请在 MT5 终端工具栏点「算法交易」按钮（Ctrl+E）",
        )
    win32_keys.send_ctrl_e(hwnd)


@router.post("/algo-trading", response_model=TerminalAlgoResult)
async def set_algo_trading(payload: TerminalAlgoRequest) -> TerminalAlgoResult:
    current = await client.run(_read_trade_allowed)
    if current == payload.enabled:
        return TerminalAlgoResult(
            changed=False,
            trade_allowed=current,
            message="终端算法交易已处于目标状态" if current else "终端算法交易已是关闭状态",
        )

    try:
        await client.run(_inject_once)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — 键击注入失败要可诊断
        raise HTTPException(
            status_code=502,
            detail=f"切换终端按钮失败：{exc} — 请在 MT5 终端手动按 Ctrl+E",
        ) from exc

    reinjected = False
    for i in range(_VERIFY_ROUNDS):
        await asyncio.sleep(_VERIFY_INTERVAL)
        now = await client.run(_read_trade_allowed)
        if now == payload.enabled:
            return TerminalAlgoResult(
                changed=True,
                trade_allowed=now,
                message="MT5 终端算法交易已开启" if now else "MT5 终端算法交易已关闭",
            )
        if i >= _REINJECT_AT and not reinjected:
            reinjected = True  # 第二次尽力而为；失败不掩盖后续轮询结果
            try:
                await client.run(_inject_once)
            except Exception:  # noqa: BLE001
                logger.warning("re-inject Ctrl+E failed", exc_info=True)
    raise HTTPException(
        status_code=502,
        detail=(
            "Ctrl+E 已发送但终端状态未变化 — MT5 可能失焦或被系统拦截，"
            "请在 MT5 终端手动点「算法交易」按钮（Ctrl+E）"
        ),
    )
