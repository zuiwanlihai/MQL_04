"""
MT5 Quant Platform — Central Configuration

All settings are loaded from environment variables or a ``.env`` file via
pydantic-settings.  Sensitive values (secrets, connection strings) default to
developer-friendly placeholders that **must** be overridden in production.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent  # backend/


class Settings(BaseSettings):
    """Application-wide settings with sensible defaults for development."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- Database -----------------------------------------------------------
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://mt5user:mt5pass@localhost:5432/mt5quant",
        description="Async SQLAlchemy connection string for PostgreSQL.",
    )

    DATABASE_SYNC_URL: str = Field(
        default="postgresql://mt5user:mt5pass@localhost:5432/mt5quant",
        description="Synchronous connection string (used by Alembic / migrations).",
    )

    DATABASE_POOL_SIZE: int = Field(default=20, ge=1, description="Async connection pool size.")
    DATABASE_MAX_OVERFLOW: int = Field(default=10, ge=0)
    DATABASE_POOL_RECYCLE: int = Field(default=3600, ge=60)

    # -- JWT / Authentication ------------------------------------------------
    JWT_SECRET_KEY: str = Field(
        default="CHANGE-ME-IN-PRODUCTION-USE-A-LONG-RANDOM-STRING",
        min_length=32,
    )
    JWT_ALGORITHM: str = Field(default="HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
        default=30, ge=1,
        validation_alias=AliasChoices(
            "ACCESS_TOKEN_EXPIRE_MINUTES", "JWT_ACCESS_TOKEN_EXPIRE_MINUTES"
        ),
    )
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(
        default=7, ge=1,
        validation_alias=AliasChoices(
            "REFRESH_TOKEN_EXPIRE_DAYS", "JWT_REFRESH_TOKEN_EXPIRE_DAYS"
        ),
    )

    # -- TOTP -----------------------------------------------------------------
    TOTP_ISSUER: str = Field(default="MT5Quant", min_length=1)
    TOTP_PREAUTH_TTL_SECONDS: int = Field(
        default=300, ge=60,
        description="Lifetime of the 2FA pre-auth token between password and code steps.",
    )

    # -- CORS -----------------------------------------------------------------
    CORS_ORIGINS: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
        ],
        description="Allowed origins for CORS middleware.",
    )

    # -- Sandbox --------------------------------------------------------------
    SANDBOX_MAX_MEMORY_MB: int = Field(default=512, ge=64)
    SANDBOX_CALLBACK_TIMEOUT_BACKTEST: int = Field(default=5, ge=1)
    SANDBOX_CALLBACK_TIMEOUT_LIVE: int = Field(default=1, ge=1)
    SANDBOX_ON_INIT_TIMEOUT: int = Field(default=60, ge=10)
    # 父侧兜底：长驻 runner 每 HEARTBEAT 秒写一行心跳，父进程连续 STALL 秒
    # 一行都没收到（连心跳都没有）就判定卡死并杀掉进程树。这覆盖子进程内熔断
    # 管不到的情况（回调霸占 GIL、卡在不返回的 C 调用里）。STALL 必须显著大于
    # HEARTBEAT，否则一次调度抖动就会误杀健康的实盘进程。
    SANDBOX_RUNNER_HEARTBEAT_SECONDS: int = Field(default=5, ge=1)
    SANDBOX_STALL_TIMEOUT_SECONDS: int = Field(default=60, ge=10)
    # 子进程内兜底：回调熔断触发后，异步异常若在这个宽限期内仍未交回控制权
    # （回调卡在不返回的 C 调用里），runner 自行结束进程。留一点余量是必要的
    # ——刚触发就硬退出会白白丢掉本可以正常上报的失败原因。
    SANDBOX_WEDGE_GRACE_SECONDS: int = Field(default=10, ge=1)

    # -- Backtest queue (M4) ---------------------------------------------------
    BACKTEST_ENABLED: bool = Field(default=True)
    BACKTEST_MAX_CONCURRENCY: int = Field(default=2, ge=1)
    BACKTEST_TIMEOUT_SECONDS: int = Field(default=600, ge=10)
    BACKTEST_MAX_BARS: int = Field(default=1_000_000, ge=1000)
    BACKTEST_RUNTIME_DIR: Path = Field(
        default=PROJECT_ROOT / ".runtime" / "backtests",
        description="Per-task payload/temp directory root.",
    )
    BACKTEST_STOP_OUT_LEVEL: float = Field(
        default=50.0, ge=1.0, description="Margin level (%) triggering stop-out."
    )
    BACKTEST_LOG_LIMIT: int = Field(default=5000, ge=100)

    # -- Research notebook (M18) ----------------------------------------------
    NOTEBOOK_ENABLED: bool = True
    NOTEBOOK_CELL_TIMEOUT_SECONDS: int = Field(default=30, ge=5)
    NOTEBOOK_MAX_CONCURRENCY: int = Field(default=2, ge=1)
    NOTEBOOK_SESSION_TTL_MINUTES: int = Field(default=30, ge=5)
    NOTEBOOK_MAX_BARS_PER_CALL: int = Field(default=50_000, ge=100)

    # -- Audit ----------------------------------------------------------------
    AUDIT_RETENTION_DAYS: int = Field(default=365, ge=1)

    # -- Gateway --------------------------------------------------------------
    GATEWAY_HEARTBEAT_INTERVAL: int = Field(default=5, ge=1)
    GATEWAY_OFFLINE_THRESHOLD: int = Field(default=30, ge=5)

    # -- Paper / Observation trading ------------------------------------------
    DEFAULT_PAPER_DURATION_DAYS: int = Field(default=14, ge=1)
    DEFAULT_OBSERVATION_DAYS: int = Field(default=7, ge=1)
    DEFAULT_OBSERVATION_CAPITAL_PCT: int = Field(default=10, ge=1, le=100)

    # -- Paper trading queue (M5) ---------------------------------------------
    PAPER_ENABLED: bool = Field(default=True)
    PAPER_MAX_CONCURRENT: int = Field(default=5, ge=1)
    PAPER_POLL_SECONDS: int = Field(
        default=10, ge=2, description="Gateway closed-bar polling interval."
    )
    PAPER_HISTORY_BARS: int = Field(
        default=100, ge=1,
        description="Extra history bars beyond warmup fetched at run start.",
    )
    PAPER_RUNTIME_DIR: Path = Field(
        default=PROJECT_ROOT / ".runtime" / "paper",
        description="Per-run payload/storage directory root.",
    )
    PAPER_STOP_OUT_LEVEL: float = Field(
        default=50.0, ge=1.0, description="Margin level (%) triggering stop-out."
    )
    PAPER_LOG_LIMIT: int = Field(default=5000, ge=100)
    PAPER_EQUITY_POINTS_MAX: int = Field(default=5000, ge=100)

    # -- Live trading queue (M6) ----------------------------------------------
    LIVE_ENABLED: bool = Field(
        default=False,
        description="Feature flag: live execution off by default (灰度).",
    )
    LIVE_MAX_CONCURRENT: int = Field(default=5, ge=1)
    LIVE_POLL_SECONDS: int = Field(default=10, ge=2)
    LIVE_HISTORY_BARS: int = Field(default=100, ge=1)
    LIVE_RUNTIME_DIR: Path = Field(
        default=PROJECT_ROOT / ".runtime" / "live",
        description="Per-run payload/storage directory root.",
    )
    LIVE_LOG_LIMIT: int = Field(default=5000, ge=100)
    LIVE_EQUITY_POINTS_MAX: int = Field(default=5000, ge=100)
    # Order round-trip: parent→gateway must finish before the child's RPC wait.
    LIVE_ORDER_TIMEOUT: float = Field(
        default=8.0, ge=1.0, description="Gateway order_send HTTP timeout (parent)."
    )
    LIVE_RPC_TIMEOUT: float = Field(
        default=15.0, ge=2.0, description="Child wait for order_result (must exceed order timeout)."
    )
    LIVE_CALLBACK_TIMEOUT: float = Field(
        default=60.0, ge=5.0,
        description="Per-callback timeout in live (blocks on RPC; NOT the 1s backtest cap).",
    )
    LIVE_MAGIC_BASE: int = Field(
        default=90000000, ge=0,
        description="magic = LIVE_MAGIC_BASE + run ordinal; used for reconciliation ownership.",
    )
    LIVE_RECONCILE_SECONDS: int = Field(default=300, ge=30, description="Reconcile interval.")
    LIVE_GATEWAY_OFFLINE_ALERT_SECONDS: int = Field(
        default=300, ge=30, description="Offline duration before 风险真空 alert."
    )
    LIVE_OBSERVATION_DAYS: int = Field(default=7, ge=1)
    LIVE_OBSERVATION_CAPITAL_PCT: int = Field(default=10, ge=1, le=100)
    LIVE_ADMISSION_MIN_PAPER_DAYS: int = Field(default=14, ge=1, le=60)

    # -- on_tick tick polling (SDK v1.1.0, paper/live only) -------------------
    TICK_POLL_SECONDS: int = Field(
        default=1, ge=1,
        description="on_tick 策略的网关 tick 轮询间隔（秒），独立于 bar 轮询。",
    )
    TICK_MAX_ROWS_PER_SEND: int = Field(
        default=2000, ge=1,
        description="单条 stdin ticks 消息最大行数（防管道超长行）。",
    )
    TICK_GATEWAY_LIMIT: int = Field(
        default=200_000, ge=1, le=200_000,
        description="ticks/range 单次拉取行数上限（网关上限 200000）。",
    )

    # -- Manual trading panel (M14) --------------------------------------------
    MANUAL_TRADE_ENABLED: bool = Field(
        default=False,
        description="Feature flag: 手动交易面板下单/平仓/撤单（真实资金，灰度默认关）。",
    )
    MANUAL_TRADE_MAX_LOT: float = Field(
        default=1.0, gt=0,
        description="手动开仓单笔手数硬上限（风控闸之外的平台兜底，防止误操作巨量下单）。",
    )

    # -- EA bridge (P4 方案 B, EB-1) -------------------------------------------
    EA_BRIDGE_ENABLED: bool = Field(
        default=True,
        description="EA 桥灰度开关：关闭时 /api/v1/ea/* 一律 404。",
    )
    EA_SYNC_MAX_TICKS: int = Field(
        default=5_000, ge=1,
        description="单次 /ea/sync 接受的最大 tick 行数（超出截断）。"
    )
    EA_TICK_BUFFER_MAX: int = Field(
        default=20_000, ge=100,
        description="每 node+symbol tick 缓冲上限（超出丢最旧并计数告警）。"
    )
    EA_RESULT_TIMEOUT: float = Field(
        default=30.0, ge=1.0,
        description="EA 命令 result future 等待超时（秒），超时自动 fail。"
    )

    # -- Gateway / Market -----------------------------------------------------
    GATEWAY_URL: str = Field(
        default="http://127.0.0.1:8090",
        description="Base URL of the local MT5 gateway service.",
    )
    GATEWAY_API_KEY: str = Field(
        default="dev-gateway-key",
        description="Shared secret sent as X-Gateway-Key (must match gateway/.env).",
    )
    GATEWAY_TIMEOUT_SECONDS: float = Field(default=10.0, ge=1.0)
    GATEWAY_HEARTBEAT_ENABLED: bool = Field(default=True)
    GATEWAY_MUTEX_PROBE_ENABLED: bool = Field(
        default=True,
        description="实盘/模拟互斥：创建 paper/live 时探活节点当前登录账户类型"
        "（测试套件关闭以保持封闭性）.",
    )
    HEALTH_LOG_SAMPLE_INTERVAL: int = Field(default=30, ge=1)
    HEALTH_LOG_RETENTION_HOURS: int = Field(default=24, ge=1)
    CREDENTIALS_KEY: str = Field(
        default="",
        description="Fernet key for account passwords; derived from JWT_SECRET_KEY when empty.",
    )

    # -- Notification channels (M10) ------------------------------------------
    SMTP_HOST: str = Field(default="", description="Empty = email channel disabled.")
    SMTP_PORT: int = Field(default=465, ge=1, le=65535)
    SMTP_USER: str = Field(default="")
    SMTP_PASSWORD: str = Field(default="", repr=False)
    SMTP_FROM: str = Field(default="", description="发件人地址（空则用 SMTP_USER）")
    SMTP_TLS: bool = Field(default=True, description="true=SMTPS(隐式TLS)；false=STARTTLS")
    NOTIFY_TIMEOUT_SECONDS: float = Field(
        default=10.0, ge=2.0, description="Per-channel send timeout (email/webhook).")

    # -- Alert rule engine (M15) -----------------------------------------------
    ALERT_RULES_ENABLED: bool = Field(
        default=True, description="Background loop evaluating user-defined alert rules.")
    ALERT_RULES_INTERVAL_SECONDS: int = Field(
        default=30, ge=5, description="Alert-rule evaluation cadence.")

    # -- Audit-chain patrol (M16) ----------------------------------------------
    AUDIT_PATROL_ENABLED: bool = Field(
        default=True, description="Scheduled audit hash-chain verification.")
    AUDIT_PATROL_INTERVAL_SECONDS: int = Field(
        default=21600, ge=60, description="Patrol cadence (default 6h).")

    # -- Gateway pairing (M17) ---------------------------------------------------
    PAIR_SERVER_URL: str = Field(
        default="http://127.0.0.1:8000",
        description="Backend URL embedded in pairing QR codes / CLI hints.")

    # -- Tick data sync (M-Tick, 数据中心 P1) ------------------------------------
    TICK_SYNC_ENABLED: bool = Field(
        default=True, description="Serial in-process executor for tick sync jobs.")
    TICK_SYNC_MAX_SPAN_DAYS: int = Field(
        default=7, ge=1, le=90,
        description="Max window span per sync request (days).")
    TICK_SYNC_CHUNK_SECONDS: int = Field(
        default=21600, ge=600,
        description="Gateway pull granularity (default 6h slices).")

    # -- Quote push (行情页实时化) -----------------------------------------------
    MARKET_PUSH_ENABLED: bool = Field(
        default=True, description="WS quote-push hub + lazy gateway poll pump.")
    MARKET_PUSH_POLL_SECONDS: float = Field(
        default=0.5, ge=0.2,
        description="Gateway poll cadence while at least one WS client is subscribed.")
    MARKET_PUSH_MAX_SYMBOLS: int = Field(
        default=50, ge=1,
        description="Max symbols per WS connection (mirrors the REST /quotes limit).")

    # -- Market history management (M2, 数据中心) --------------------------------
    MARKET_HISTORY_SYNC_ENABLED: bool = Field(
        default=True, description="Background incremental sync loop for tracked ranges.")
    MARKET_HISTORY_SYNC_INTERVAL_SECONDS: int = Field(
        default=300, ge=30, description="Sync-loop cadence (default 5min).")
    MARKET_HISTORY_SYNC_BATCH: int = Field(
        default=20, ge=1, description="Pairs advanced per sync round (rotation by last_synced_at).")
    MARKET_HISTORY_SYNC_PAIR_DELAY: float = Field(
        default=0.05, ge=0, description="Sleep between pairs within one round.")
    MARKET_HISTORY_PROBE_TIMEOUT: float = Field(
        default=30.0, ge=1.0, description="Gateway probe HTTP timeout.")
    MARKET_HISTORY_BACKFILL_TIMEOUT: float = Field(
        default=60.0, ge=1.0, description="Per-window gateway backfill HTTP timeout.")
    MARKET_HISTORY_BACKFILL_WINDOW_BARS: int = Field(
        default=50_000, ge=100, description="Bars per backfill sub-window.")
    MARKET_HISTORY_BACKFILL_MAX_BARS: int = Field(
        default=200_000, ge=1000, description="Max bars per backfill request (span guard).")
    MARKET_HISTORY_BACKFILL_MAX_ATTEMPTS: int = Field(
        default=6, ge=1, le=20, description="Retries passed to the gateway per window.")

    # -- Application ----------------------------------------------------------
    APP_TITLE: str = "MT5 Quant Platform"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = Field(default=False)
    LOG_LEVEL: str = Field(default="INFO")

    @field_validator("DATABASE_SYNC_URL", mode="before")
    @classmethod
    def _derive_sync_url(cls, v: str | None, info) -> str:
        if v is not None:
            return v
        # Derive from async URL if not explicitly set
        async_url = info.data.get("DATABASE_URL", "")
        return async_url.replace("+asyncpg", "").replace("postgresql+asyncpg://", "postgresql://")


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
settings = Settings()
