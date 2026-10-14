"""EA bridge (P4 方案 B) request/response schemas.

Three endpoints driven by the MQL5 EA via WebRequest(HTTPS):
``POST /ea/pair`` (one-shot code → node_key), ``POST /ea/sync`` (tick upload +
command download), ``POST /ea/result`` (command RPC result).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class EAPairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pair_code: str = Field(min_length=6, max_length=12)
    mt5_login: int = Field(gt=0)
    mt5_server: str = Field(min_length=1, max_length=120)
    terminal_info: dict[str, str] = Field(
        default_factory=dict,
        description="Optional: mt5_version / os / terminal name for display.",
    )


class EAPairResponse(BaseModel):
    node_id: str
    node_key: str = Field(
        description="Random 64-hex credential for X-EA-Key; shown once, "
                    "persisted only in the terminal's global variables.",
    )
    backend_version: str
    sync_interval_ms: int = Field(default=500)


class EASyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cursor_last_msc: int = Field(
        default=0, description="EA-side send cursor (ms) — informational.",
    )
    ticks: dict[str, list[list]] = Field(
        default_factory=dict,
        description="symbol → [[time_msc, bid, ask], …] (skew-normalized).",
    )
    account: dict | None = Field(
        default=None, description="Optional broker snapshot (EB-2 wires it).",
    )


class EACommand(BaseModel):
    type: str = Field(description="rates_req | account_req | order_request | close_all")
    req_id: str
    params: dict = Field(default_factory=dict)


class EASyncResponse(BaseModel):
    commands: list[EACommand] = Field(default_factory=list)
    server_time_ms: int = Field(
        description="Backend unix ms — EA derives/refreshes its tz-skew from it.",
    )
    next_wait_ms: int = Field(default=500)


class EAResultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    req_id: str = Field(min_length=4, max_length=64)
    ok: bool
    payload: dict | None = None
    error: str | None = None


class EAResultResponse(BaseModel):
    resolved: bool = Field(
        description="False → req_id unknown/expired (already timed out).",
    )
