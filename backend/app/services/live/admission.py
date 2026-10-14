"""Live admission four-gate evaluation (M6, PRD M5 准入闸门 P0).

Dimension: (strategy version × MT5 account).  Every gate is a hard blocker —
there is NO bypass path (no advanced mode, no direct API override).

    ① 权限闸  settings.LIVE_ENABLED ∧ account.live_allowed ∧ role ∈ {admin,trader}
    ② 回测闸  ≥1 completed BacktestTask for the *exact* frozen version
    ③ 时长闸  ≥N consecutive UTC days of paper_daily_stats attest for the
              version (uptime ≥ 90% of the day, zero violations).  Paper runs
              are account-agnostic in MVP, so the account dimension is
              recorded at deploy time; the version timer is what resets on a
              new freeze (a new version simply has no attest rows).
    ④ 记录闸  no RiskEvent and no sandbox-violation failure in the window

The returned snapshot (per-gate pass/fail + decision metrics) is frozen into
``LiveRun.admission_snapshot`` and mirrored into the audit log on pass.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    BacktestReport,
    BacktestTask,
    MT5Account,
    PaperDailyStats,
    RiskEvent,
    Strategy,
    StrategyVersion,
    User,
)

SANDBOX_CODES = ("SANDBOX_VIOLATION", "ILLEGAL_IMPORT", "RESOURCE_EXCEEDED",
                 "RESOURCE_TIMEOUT")


@dataclass
class GateResult:
    key: str
    label: str
    passed: bool
    reason: str = ""
    metrics: dict = field(default_factory=dict)


@dataclass
class AdmissionResult:
    strategy_id: str
    strategy_version: int
    account_id: str
    gates: list[GateResult]
    decision_metrics: dict = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return all(g.passed for g in self.gates)

    def snapshot(self) -> dict:
        return {
            "checked_at": datetime.now(UTC).isoformat(),
            "allowed": self.allowed,
            "gates": [asdict(g) for g in self.gates],
            "decision_metrics": self.decision_metrics,
        }


async def evaluate_admission(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    strategy_id: uuid.UUID,
    version: int,
    account_id: uuid.UUID,
) -> AdmissionResult:
    """Run all four gates; never raises for a failed gate — only for bad input."""
    strat = await db.get(Strategy, strategy_id)
    if strat is None or strat.user_id != user_id:
        raise LookupError("策略不存在")
    ver = (await db.execute(
        select(StrategyVersion).where(
            StrategyVersion.strategy_id == strategy_id,
            StrategyVersion.version == version,
        )
    )).scalar_one_or_none()
    if ver is None:
        raise LookupError(f"策略版本 v{version} 不存在（实盘只能部署已冻结版本）")
    acct = await db.get(MT5Account, account_id)
    if acct is None:
        raise LookupError("MT5 账户不存在")

    gates: list[GateResult] = []

    # ── ① 权限闸 ──
    user = await db.get(User, user_id)
    role = getattr(user, "role", "viewer") or "viewer"
    gate1 = GateResult("permission", "权限闸", False)
    if not settings.LIVE_ENABLED:
        gate1.reason = "实盘功能未开放（平台灰度开关 LIVE_ENABLED=false）"
    elif not acct.live_allowed:
        gate1.reason = f"账户 {acct.login} 未获得实盘资格（需管理员开通或解除降级）"
    elif acct.account_type not in ("real", "demo"):
        # 实盘/模拟互斥同帧校验：绕过创建端点的路径也无法通过权限闸。
        # 模拟账户允许实盘部署 —— 订单真实发到 MT5 模拟服务器，零资金风险。
        gate1.reason = (
            f"账户 {acct.login} 类型为 {acct.account_type}，"
            "实盘部署仅支持实盘/模拟账户；请在总览页切换登录"
        )
    elif role not in ("admin", "trader"):
        gate1.reason = f"角色 {role} 无实盘权限（仅 admin/trader 可部署）"
    else:
        gate1.passed = True
        gate1.reason = "功能开关、账户资格与角色权限均满足"
    gates.append(gate1)

    # ── ② 回测闸 ──
    bt = (await db.execute(
        select(BacktestTask)
        .where(
            BacktestTask.strategy_id == strategy_id,
            BacktestTask.strategy_version == version,
            BacktestTask.status == "completed",
        )
        .order_by(BacktestTask.completed_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    gate2 = GateResult("backtest", "回测闸", bt is not None)
    decision: dict = {}
    if bt is None:
        gate2.reason = f"v{version} 在本平台无成功回测记录，请先在回测中心完成一次回测"
    else:
        gate2.reason = f"v{version} 已有 {bt.completed_at:%Y-%m-%d} 成功回测"
        report = (await db.execute(
            select(BacktestReport).where(BacktestReport.task_id == bt.id)
        )).scalar_one_or_none()
        metrics = {}
        if report is not None:
            metrics = {
                "total_return_percent": float(report.total_return_percent),
                "annual_return_percent": float(report.annual_return_percent),
                "sharpe_ratio": float(report.sharpe_ratio),
                "max_drawdown_percent": float(report.max_drawdown_percent),
                "total_trades": report.total_trades,
                "win_rate": float(report.win_rate),
            }
        decision["backtest"] = {
            "task_id": str(bt.id),
            "completed_at": bt.completed_at.isoformat() if bt.completed_at else None,
            "symbol": bt.symbol, "timeframe": bt.timeframe,
            "execution_mode": bt.execution_mode,
            "metrics": metrics,
        }
    gates.append(gate2)

    # ── ③ 模拟时长闸 ──
    need_days = settings.LIVE_ADMISSION_MIN_PAPER_DAYS
    min_uptime = int(86400 * 0.9)
    rows = (await db.execute(
        select(PaperDailyStats.day, PaperDailyStats.uptime_seconds,
               PaperDailyStats.risk_triggers, PaperDailyStats.sandbox_violations)
        .where(
            PaperDailyStats.strategy_id == strategy_id,
            PaperDailyStats.strategy_version == version,
        )
        .order_by(PaperDailyStats.day.desc())
        .limit(need_days + 7)
    )).all()
    # consecutive days ending at the most recent attest day
    good_days: list[date] = []
    for d, uptime, risk, viol in rows:
        if uptime >= min_uptime and risk == 0 and viol == 0:
            if good_days and (good_days[-1] - d).days != 1:
                break
            good_days.append(d)
        elif good_days:
            break
    streak = len(good_days)
    gate3 = GateResult("paper_duration", "模拟时长闸", streak >= need_days)
    gate3.metrics = {"streak_days": streak, "required_days": need_days}
    gate3.reason = (
        f"v{version} 已连续模拟 {streak} 天（要求 ≥{need_days} 天，"
        "每日在线率 ≥90% 且无违规）"
    )
    decision["paper"] = {
        "streak_days": streak, "required_days": need_days,
        "last_attest_day": good_days[0].isoformat() if good_days else None,
    }
    gates.append(gate3)

    # ── ④ 记录闸 ──
    # 记录闸 looks at the paper window: risk triggers recorded against paper
    # runs (source="paper") plus sandbox violations (already reflected in the
    # gate-3 attest rows).  Any hit blocks deployment until the window passes.
    window_start = datetime.now(UTC) - timedelta(days=need_days + 7)
    paper_risk = (await db.execute(
        select(func.count()).select_from(RiskEvent).where(
            RiskEvent.source == "paper",
            RiskEvent.created_at >= window_start,
        )
    )).scalar_one()
    gate4 = GateResult("record", "记录闸", paper_risk == 0)
    gate4.metrics = {"risk_events_in_window": paper_risk}
    gate4.reason = (
        "模拟期间无风控触发、无沙箱越界记录" if paper_risk == 0
        else f"模拟窗口内存在 {paper_risk} 条风控/违规记录"
    )
    decision["risk_events_in_window"] = paper_risk
    gates.append(gate4)

    return AdmissionResult(
        strategy_id=str(strategy_id), strategy_version=version,
        account_id=str(account_id), gates=gates, decision_metrics=decision,
    )
