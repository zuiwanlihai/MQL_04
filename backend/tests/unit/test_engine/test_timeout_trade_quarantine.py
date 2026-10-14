"""2.3 — 超时回调里已经产生的成交必须被标记，而不是当作正常成交泄漏出去。

超时判定天生滞后：回调只有交回控制权（或被打断）时才可能被判超时，那时它的下单
早已执行完、成交记录也已经躺在事件队列里了。回测里这无关痛痒（失败就没有报告），
但 paper 是**边跑边落库**的，那一笔就成了一条与其他成交毫无区别的 `paper_trades`
行——用户看到「运行失败」，却也看到一笔像是正常结束的成交。

这里钉住三件事：

* 该死回调里的成交被标成可疑，且**只**标它自己那一段（不能连坐前面的正常成交）；
* 没有成交的超时不得凭空造出标记；
* 失败文案要点出笔数，否则「哪几笔可疑」只能靠人翻库。

回滚不是选项：实盘里那是券商已经成交的既成事实，删记录只会让账对不上。
"""

from __future__ import annotations

import pytest

# ``sdk`` 只有先 import 过 ``app.sandbox.*`` 才在 sys.path 上——见
# ``test_callback_timeout`` 里同样的说明。
from app.sandbox.engine import BacktestEngine, ErrorCode
from app.sandbox.import_hook import uninstall_all
from tests.unit.bt_helpers import make_bars, make_payload

# 忙等必须远超超时预算，但没有熔断时要能自行结束（否则挂死而不是失败）。
_SPIN_ITERATIONS = 120_000_000
_TIMEOUT = 0.25


@pytest.fixture(autouse=True)
def _clean_sandbox():
    yield
    uninstall_all()


def _engine(source: str, **kw) -> BacktestEngine:
    bars = kw.pop("bars", None)
    bars = make_bars(4) if bars is None else bars
    return BacktestEngine(make_payload(source, **kw), bars)


def _trades(events) -> list[dict]:
    return [e.data for e in events if e.kind == "trade"]


def _fails(events) -> list[dict]:
    return [e.data for e in events if e.kind == "fail"]


def _burn() -> str:
    return f"""
def _burn():
    total = 0
    for i in range({_SPIN_ITERATIONS}):
        total += i
    return total
"""


# 第 1 根：建仓；第 2 根：平掉它，然后忙等到熔断。那笔平仓成交就出自被打断的回调。
CLOSE_THEN_SPIN = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1")

def on_bar(ctx, bar):
    if not ctx.storage.get("pid"):
        ctx.storage["pid"] = ctx.buy("EURUSD", 0.1).order_id
        return
    if ctx.storage.get("closed"):
        return
    ctx.storage["closed"] = True
    ctx.close(ctx.storage["pid"])
    _burn()

def on_order(ctx, order):
    pass
""" + _burn()


# 前两根正常开平（成交可信），第 3 根才忙等——用来验证标记的边界。
CLOSE_THEN_SPIN_LATER = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1")

def on_bar(ctx, bar):
    if not ctx.storage.get("pid"):
        ctx.storage["pid"] = ctx.buy("EURUSD", 0.1).order_id
        return
    if not ctx.storage.get("closed"):
        ctx.storage["closed"] = True
        ctx.close(ctx.storage["pid"])
        return
    if ctx.storage.get("burned"):
        return
    ctx.storage["burned"] = True
    _burn()

def on_order(ctx, order):
    pass
""" + _burn()


def test_trade_from_the_killed_callback_is_marked_suspect():
    eng = _engine(CLOSE_THEN_SPIN, callback_timeout_seconds=_TIMEOUT)
    events = list(eng.run())

    trades = _trades(events)
    assert len(trades) == 1, "本策略只该有一笔平仓成交"
    assert trades[0]["suspect"] is True, (
        "被打断回调里的成交仍是可疑的——它没被标记就会和平仓成交一模一样地落库"
    )
    assert "on_bar" in trades[0]["suspect_reason"], trades[0]["suspect_reason"]


def test_only_the_killed_callback_is_marked():
    """边界：更早、已正常结束的回调产出的成交不得连坐。"""
    eng = _engine(CLOSE_THEN_SPIN_LATER, callback_timeout_seconds=_TIMEOUT)
    events = list(eng.run())

    trades = _trades(events)
    assert len(trades) == 1
    assert "suspect" not in trades[0], (
        "标记越界了：这笔成交来自先前那个正常结束的回调"
    )


def test_timeout_without_any_trade_stays_clean():
    """不下单的忙等不得凭空造出可疑成交（也没有成交可标）。"""
    source = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1")

def on_bar(ctx, bar):
    if ctx.storage.get("burned"):
        return
    ctx.storage["burned"] = True
    _burn()

def on_order(ctx, order):
    pass
""" + _burn()
    eng = _engine(source, callback_timeout_seconds=_TIMEOUT)
    events = list(eng.run())

    assert _trades(events) == []
    fails = _fails(events)
    assert len(fails) == 1
    assert fails[0]["code"] == ErrorCode.RESOURCE_TIMEOUT.value
    assert "标记为可疑" not in fails[0]["message"], fails[0]["message"]


def test_failure_message_names_the_suspect_count():
    """文案要点出笔数：否则「哪几笔可疑」只能靠人翻库去猜。"""
    eng = _engine(CLOSE_THEN_SPIN, callback_timeout_seconds=_TIMEOUT)
    fails = _fails(list(eng.run()))

    assert len(fails) == 1
    assert "1 笔" in fails[0]["message"], fails[0]["message"]
