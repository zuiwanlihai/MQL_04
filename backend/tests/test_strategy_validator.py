"""AST validator unit tests (M3)."""

from __future__ import annotations

from app.services.strategy_validator import has_callback, validate_source

VALID = """
import math
import numpy as np
import pandas as pd

def on_init(ctx):
    ctx.declare(symbol="EURUSD", period="H1", params={"n": 20})

def on_bar(ctx, bar):
    closes = [b.close for b in ctx.get_bars(ctx.symbol, ctx.period, count=20)]
    if len(closes) < 20:
        return
    ma = sum(closes) / 20
    if bar.close > ma:
        ctx.buy(ctx.symbol, 0.1)

def on_order(ctx, order):
    ctx.log.info(f"filled {order.order_id}")

def on_stop(ctx, reason):
    ctx.log.info(reason)
"""


def _codes(result) -> set[str]:
    return {i.code for i in result.issues}


def test_valid_source_passes():
    result = validate_source(VALID)
    assert result.ok
    assert not any(i.severity == "error" for i in result.issues)


def test_syntax_error():
    result = validate_source("def on_init(ctx):\n  x = (")
    assert not result.ok
    assert "E001" in _codes(result)


def test_missing_callbacks():
    result = validate_source("def on_init(ctx):\n    pass\n")
    assert not result.ok
    codes = _codes(result)
    assert "E002" not in codes  # on_init 已实现
    assert {"E003", "E004"} <= codes


def test_wrong_arity():
    src = (
        "def on_init(ctx, extra):\n    pass\n"
        "def on_bar(ctx, bar):\n    pass\n"
        "def on_order(ctx, order):\n    pass\n"
    )
    assert "E005" in _codes(validate_source(src))


def test_forbidden_module():
    assert "I001" in _codes(validate_source("import socket\n" + VALID[VALID.index("\n") + 1:]))
    result = validate_source("import os\nimport requests\n")
    assert {"I001"} <= _codes(result)


def test_not_in_whitelist():
    result = validate_source("import sklearn\n")
    assert "I002" in _codes(result)


def test_star_import():
    result = validate_source("from numpy import *\n")
    assert "I003" in _codes(result)


def test_alias_escape_numpy_load():
    src = "import numpy as np\nx = np.load('a.npy')\n"
    result = validate_source(src)
    assert "F003" in _codes(result)


def test_direct_pandas_read_csv():
    src = "import pandas\npandas.read_csv('a.csv')\n"
    assert "F003" in _codes(validate_source(src))


def test_from_import_escape():
    src = "from pandas import read_csv\nread_csv('a.csv')\n"
    result = validate_source(src)
    assert "I004" in _codes(result)


def test_dangerous_builtins():
    src = "eval('1+1')\nexec('x=1')\nopen('f')\n"
    result = validate_source(src)
    assert "F001" in _codes(result)


def test_real_clock():
    src = "import datetime\nnow = datetime.datetime.now()\n"
    result = validate_source(src)
    assert {"F002", "I004"} & _codes(result)  # at least one clock rule fires
    assert not result.ok


def test_on_tick_warning():
    src = VALID + "\ndef on_tick(ctx, tick):\n    pass\n"
    result = validate_source(src)
    assert "W001" in _codes(result)
    assert result.ok  # warning only


def test_end_time_warning():
    src = VALID.replace("count=20)", "count=20, end_time=None)")
    result = validate_source(src)
    assert "W002" in _codes(result)


def test_top_level_statement_warning():
    src = VALID + "\nprint('hello')\n"
    result = validate_source(src)
    # print 是 Expr 顶层语句但 print 不在危险内建；W003 只针对非表达式语句
    assert "W003" not in _codes(result)  # Expr 允许（docstring 等）

    src2 = VALID + "\nfor i in range(3):\n    pass\n"
    assert "W003" in _codes(validate_source(src2))


def test_missing_on_stop_info():
    src = VALID.replace("def on_stop(ctx, reason):\n    ctx.log.info(reason)\n", "")
    result = validate_source(src)
    assert "W004" in _codes(result)
    assert result.ok


def test_empty_source():
    result = validate_source("")
    assert not result.ok
    assert {"E002", "E003", "E004"} <= _codes(result)


# ── on_tick (SDK v1.1.0) ──────────────────────────────────────

def test_on_tick_wrong_arity_is_error():
    src = VALID + "\ndef on_tick(ctx):\n    pass\n"
    result = validate_source(src)
    assert "E005" in _codes(result)
    assert not result.ok


def test_on_tick_hint_is_info_with_new_copy():
    src = VALID + "\ndef on_tick(ctx, tick):\n    pass\n"
    result = validate_source(src)
    w001 = [i for i in result.issues if i.code == "W001"]
    assert len(w001) == 1
    assert w001[0].severity == "info"  # SDK 1.1.0：warning → info
    assert "回测不调用" in w001[0].message
    assert result.ok


def test_has_callback_top_level_only():
    src = VALID + "\ndef on_tick(ctx, tick):\n    pass\n"
    assert has_callback(src, "on_tick") is True
    assert has_callback(VALID, "on_tick") is False
    # 函数体内部嵌套的同名 def 不算顶层回调
    nested = VALID + "\ndef wrap():\n    def on_tick(ctx, tick):\n        pass\n"
    assert has_callback(nested, "on_tick") is False
    # 语法错误 → False（部署前的 validate_source 会另行拦截 E001）
    assert has_callback("def on_tick(ctx:", "on_tick") is False
