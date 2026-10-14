"""Gateway configuration — environment driven (``gateway/.env``)."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]  # gateway/


class GatewaySettings(BaseSettings):
    """Runtime settings for the local MT5 gateway."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -- HTTP ---------------------------------------------------------------
    GATEWAY_HOST: str = "127.0.0.1"
    GATEWAY_PORT: int = 8090
    # 网关访问密钥。**故意不给默认值**：默认值等于写进源码的公开口令，照文档装
    # 出来的网关会拿它对外服务。留空时启动自检直接拒绝启动
    # （见 app.security.assert_key_configured），配对流程负责写入随机值。
    GATEWAY_API_KEY: str = ""

    # -- MetaTrader 5 -------------------------------------------------------
    MT5_PATH: str = r"C:\Program Files\MetaTrader 5\terminal64.exe"
    # 可选：启动时自动登录（留空则等待后端/用户调用 /account/login）
    MT5_LOGIN: int | None = None
    MT5_PASSWORD: str | None = None
    MT5_SERVER: str | None = None

    # -- Trading (M6) -------------------------------------------------------
    TRADE_ENABLED: bool = True
    # 允许下单的 MT5 登录号白名单（空=不限制）。灰度期只放行 demo 账户。
    TRADE_ALLOWED_LOGINS: str = ""
    # 单笔最大允许滑点（points）
    TRADE_MAX_DEVIATION: int = 20

    # -- History management (M2 数据中心) --------------------------------------
    # 探测终端本地 K 线范围的分页大小与上限
    HISTORY_PROBE_PAGE_SIZE: int = 100_000
    HISTORY_PROBE_MAX_BARS: int = 500_000
    # 补数：单窗最大重试轮数与轮间隔（秒）——每轮之间释放 worker 供交易插队
    HISTORY_BACKFILL_MAX_ATTEMPTS: int = 6
    HISTORY_BACKFILL_RETRY_INTERVAL: float = 2.0

    # rates 尾部滞后自愈：未开图表品种终端返回陈旧缓存时的兜底重试。
    # 尾根落后 ≥2 根触发；重试耗尽仍陈旧（休市）写 COOLDOWN 秒冷却。
    RATES_TAIL_HEAL_MAX_ATTEMPTS: int = 2
    RATES_TAIL_HEAL_INTERVAL: float = 0.8
    RATES_TAIL_HEAL_COOLDOWN: float = 300.0

    # -- Ops (M11 常驻运维) ---------------------------------------------------
    RUNTIME_DIR_ROOT: Path = PROJECT_ROOT / ".runtime"
    # 终端守护循环：不可达时拉起终端，10 分钟窗口内最多次数
    TERMINAL_GUARD_INTERVAL_SECONDS: int = 30
    TERMINAL_GUARD_MAX_RESTARTS: int = 3
    TERMINAL_GUARD_WINDOW_SECONDS: int = 600

    @property
    def trade_allowed_logins(self) -> set[int]:
        raw = self.TRADE_ALLOWED_LOGINS.replace(" ", "")
        if not raw:
            return set()
        return {int(x) for x in raw.split(",") if x}


def add_trade_allowed_login(login: int) -> set[int]:
    """Add *login* to the whitelist: runtime (immediate effect) + persist to .env."""
    current = settings.trade_allowed_logins
    if login in current:
        return current
    updated = current | {login}
    settings.TRADE_ALLOWED_LOGINS = ",".join(str(x) for x in sorted(updated))
    persist_trade_env()
    return settings.trade_allowed_logins


def remove_trade_allowed_login(login: int) -> set[int]:
    """Remove *login* from the whitelist: runtime + persist to .env."""
    current = settings.trade_allowed_logins
    if login not in current:
        return current
    settings.TRADE_ALLOWED_LOGINS = ",".join(str(x) for x in sorted(current - {login}))
    persist_trade_env()
    return settings.trade_allowed_logins


def persist_trade_env() -> None:
    """Rewrite the TRADE_ALLOWED_LOGINS line in gateway/.env (create if absent)."""
    env_path = PROJECT_ROOT / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    out: list[str] = []
    replaced = False
    for ln in lines:
        if ln.startswith("TRADE_ALLOWED_LOGINS="):
            out.append(f"TRADE_ALLOWED_LOGINS={settings.TRADE_ALLOWED_LOGINS}")
            replaced = True
        else:
            out.append(ln)
    if not replaced:
        out.append(f"TRADE_ALLOWED_LOGINS={settings.TRADE_ALLOWED_LOGINS}")
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")


settings = GatewaySettings()
