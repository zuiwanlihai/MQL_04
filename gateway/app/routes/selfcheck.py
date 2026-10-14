"""GET /selfcheck · POST /power/fix — one-click diagnostics (M11, PRD M1).

Keyed like the trading surface (``X-Gateway-Key``): the backend proxies these
to the Web UI.  ``/power/fix`` defaults to guidance-only; it mutates the host
power plan **only** when ``execute=true``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app import selfcheck as sc
from app.mt5_client import client
from app.power import apply_fix
from app.security import require_gateway_key

router = APIRouter(dependencies=[Depends(require_gateway_key)])


class PowerFixRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execute: bool = Field(default=False, description="false=仅返回命令指引；true=真实修改电源方案")


@router.get("/selfcheck")
async def selfcheck() -> dict:
    """Full one-click checklist (terminal / broker / power / time / disk)."""
    return await sc.run_selfcheck(client)


@router.post("/power/fix")
async def power_fix(payload: Annotated[PowerFixRequest, ...]) -> dict:
    return await apply_fix(payload.execute)
