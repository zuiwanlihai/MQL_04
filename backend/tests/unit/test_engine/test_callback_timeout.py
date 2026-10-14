"""2.1 — 回调超时必须真的**中断**执行，而不是事后记账。

``place_order`` 是同步回调进策略的：``on_bar`` 里的每一次 ``ctx.buy()`` 都会在
``_call("on_bar")`` 内部再套一层 ``_call("on_order")``。内层的 ``finally``
无条件执行 ``signal.setitimer(ITIMER_REAL, 0)``，把**外层**刚装好的引信整个
拆掉——所以「下单成功之后的 on_bar 剩余部分毫无熔断」在 Linux 上同样成立，
不只是 Windows 没有 ``SIGALRM`` 的问题。

预算属于**最外层**回调：嵌套调用既不能重新计时，也不能撤销计时。

这三条测试各自盯住一个可观察的差异：

* 超时被中断 → 文案是「被强制中断」；只是事后对比耗时 → 文案是「回调耗时」。
  纯 Python 忙等永远不会到达后者之外的结果，所以这个断言与机器快慢无关。
* 没有下单的纯忙等也必须被中断（Windows 上本来就是零保护）。
* ``_fail()`` 里调用 ``on_stop`` 的那条路径绕过了熔断，必须一并纳入。
"""

from __future__ import annotations

import pytest

# ``sdk`` 只有先 import 过 ``app.sandbox.*`` 才在 sys.path 上——那是
# ``app.sandbox/__init__.py`` 注册 shared/ 的副作用。直接写
# ``from sdk.errors import ...`` 会被 ruff 的 isort 提到 app 之前而失效，
# 所以经由已经绑定它的 engine 模块取。
from app.sandbox.engine import BacktestEngine, ErrorCode
from app.sandbox.import_hook import uninstall_all
from tests.unit.bt_helpers import make_bars, make_payload

# 忙等的迭代次数必须远超超时预算（见下方 0.25s），但又要能在没有熔断时
# 自行结束——否则测试会挂死而不是失败。实测本机约 5-8s。
_SPIN_ITERATIONS = 120_000_000
_TIMEOUT = 0.25


@pytest.fixture(autouse=True)
def _clean_sandbox():
    yield
    uninstall_all()  # engine.run() installs meta_path finder + module views


def _engine(source: str, **kw) -> BacktestEngine:
    bars = kw.pop("bars", None)
    bars = make_bars(4) if bars is None else bars
    return BacktestEngine(make_payload(source, **kw), bars)


def _fails(events) -> list[dict]:
    return [e.data for e in events if e.kind == "fail"]


def _burn() -> str:
    """共享的忙等函数体，拼进策略源码。"""
    return f"""
def _burn():
    total = 0
    for i in range({_SPIN_ITERATIONS}):
        total += i
    return total
"""


BUY_THEN_SPIN = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1")

def on_bar(ctx, bar):
    if ctx.storage.get("done"):
        return
    ctx.storage["done"] = True
    ctx.buy("EURUSD", 0.1)
    _burn()

def on_order(ctx, order):
    pass
""" + _burn()


SPIN_ONLY = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1")

def on_bar(ctx, bar):
    if ctx.storage.get("done"):
        return
    ctx.storage["done"] = True
    _burn()

def on_order(ctx, order):
    pass
""" + _burn()


FAIL_THEN_HANG_IN_ON_STOP = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1")

def on_bar(ctx, bar):
    raise RuntimeError("boom")

def on_order(ctx, order):
    pass

def on_stop(ctx, reason):
    _burn()
""" + _burn()


HANG_IN_ON_ORDER = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1")

def on_bar(ctx, bar):
    if ctx.storage.get("done"):
        return
    ctx.storage["done"] = True
    ctx.buy("EURUSD", 0.1)

def on_order(ctx, order):
    _burn()
""" + _burn()


# 不下单也不忙等：用来数「一次最外层回调装了几枚引信」。
BUY_IN_FIRST_BAR = """
def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1")

def on_bar(ctx, bar):
    if ctx.storage.get("done"):
        return
    ctx.storage["done"] = True
    ctx.buy("EURUSD", 0.1)

def on_order(ctx, order):
    pass
"""


def test_order_inside_on_bar_does_not_disarm_the_fuse():
    """``ctx.buy()`` 嵌套一层 on_order 之后，on_bar 剩余部分仍在熔断之下。"""
    eng = _engine(BUY_THEN_SPIN, callback_timeout_seconds=_TIMEOUT)
    fails = _fails(list(eng.run()))

    assert len(fails) == 1, "超时回调必须终止回测"
    assert fails[0]["code"] == ErrorCode.RESOURCE_TIMEOUT.value
    assert "被强制中断" in fails[0]["message"], (
        f"应被中断执行，实际拿到的是事后记账：{fails[0]['message']!r}"
    )


def test_pure_spin_is_interrupted_without_any_order():
    """不下单的纯忙等同样要被熔断（Windows 上从来没有 SIGALRM 可用）。"""
    eng = _engine(SPIN_ONLY, callback_timeout_seconds=_TIMEOUT)
    fails = _fails(list(eng.run()))

    assert len(fails) == 1, "纯忙等回调必须终止回测"
    assert fails[0]["code"] == ErrorCode.RESOURCE_TIMEOUT.value
    assert "被强制中断" in fails[0]["message"]


def test_on_stop_in_the_failure_path_is_time_bounded():
    """``_fail()`` 里补救性调用 ``on_stop`` 时不能永久挂住——但也不许吞掉原始失败。"""
    eng = _engine(FAIL_THEN_HANG_IN_ON_STOP, callback_timeout_seconds=_TIMEOUT)
    events = list(eng.run())
    fails = _fails(events)

    assert len(fails) == 1, "原始失败必须照常上报"
    assert "boom" in fails[0]["message"]

    messages = [e.data["message"] for e in events if e.kind == "log"]
    assert any("on_stop" in m for m in messages), (
        f"on_stop 超时必须留下痕迹，实际日志：{messages}"
    )


def test_hang_inside_on_order_is_labelled_with_the_outer_callback():
    """预算属于 ``on_bar``：在嵌套的 ``on_order`` 里超时，报的也得是 ``on_bar``。"""
    eng = _engine(HANG_IN_ON_ORDER, callback_timeout_seconds=_TIMEOUT)
    fails = _fails(list(eng.run()))

    assert len(fails) == 1
    assert fails[0]["code"] == ErrorCode.RESOURCE_TIMEOUT.value
    assert "on_bar()" in fails[0]["message"], fails[0]["message"]


def test_clean_interrupt_leaves_no_unresolved_timeout():
    """熔断成功中断后探针必须归零。

    runner 的 reaper 靠它判断「救不回来了」；它要是在正常路径上留了残值，每次
    超时都会把整个沙箱进程硬杀掉，连带丢掉本该上报的失败原因。
    """
    eng = _engine(BUY_THEN_SPIN, callback_timeout_seconds=_TIMEOUT)
    _fails(list(eng.run()))

    assert eng.unresolved_timeout_seconds() == 0.0


def test_fuse_is_armed_once_per_outermost_callback():
    """一次最外层回调恰好一装一卸；嵌套的 ``on_order`` 完全不碰引信。

    这条断言与平台无关，因此它才是「嵌套」这个根因的回归防线——只看「有没有被
    中断」在 Windows 上区分不了「引信被内层拆掉」和「本来就装不上」。
    """
    bars = make_bars(4)
    eng = _engine(BUY_IN_FIRST_BAR, bars=bars)
    events = list(eng.run())

    assert _fails(events) == []
    # on_init 1 次 + 每根 K 线一次 on_bar；on_order 由 on_bar 里的 ctx.buy() 嵌套
    # 触发，不该计入。（本策略未定义 on_stop，缺失的回调在 _call 里直接返回。）
    expected = 1 + len(bars.time)
    assert eng._fuse.armed == expected, "嵌套回调不得再装一枚引信"
    assert eng._fuse.disarmed == expected, "每次装上的引信都必须卸下"
    assert eng._fuse.fired == 0
