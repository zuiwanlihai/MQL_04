"""Gateway trading tests — fake MetaTrader5 module, no real terminal needed.

Covers: priority queue (trade jumps market polls), order_send request shape,
retcode mapping, volume normalisation, the server-side trade gate
(flag / login whitelist / algo switches) and position/order/deal mapping.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
import types
from pathlib import Path

import pytest

# -- fake MetaTrader5 -----------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


FAKE = _NS(
    terminal=None, account=None, orders=[], positions=[], deals=[],
    last_send=None, send_result=None, fail_order_send=False,
)


def _make_mt5_module() -> types.ModuleType:
    m = types.ModuleType("MetaTrader5")
    m.__version__ = "5.0.0-fake"
    m.TRADE_ACTION_DEAL = 1
    m.TRADE_ACTION_PENDING = 2
    m.TRADE_ACTION_REMOVE = 6
    m.ORDER_TYPE_BUY = 0
    m.ORDER_TYPE_SELL = 1
    m.ORDER_TIME_GTC = 0
    m.ORDER_FILLING_FOK = 0
    m.ORDER_FILLING_IOC = 1
    m.ORDER_FILLING_RETURN = 2

    def initialize(**kw):
        return True

    def shutdown():
        return None

    def last_error():
        return (1, "fake")

    def terminal_info():
        return FAKE.terminal

    def account_info():
        return FAKE.account

    def symbol_info(name):
        return _NS(
            name=name, digits=5, point=0.00001, spread=20, volume_min=0.01,
            volume_max=100.0, volume_step=0.01, volume_digits=2,
            filling_mode=3, contract_size=100000.0,
        )

    def symbol_info_tick(name):
        return _NS(bid=1.10000, ask=1.10002, time=int(time.time()), volume=0)

    def order_send(request):
        FAKE.last_send = request
        if FAKE.fail_order_send:
            return None
        return FAKE.send_result

    def positions_get(**kw):
        return list(FAKE.positions)

    def orders_get(**kw):
        return list(FAKE.orders)

    def history_deals_get(date_from, date_to):
        return list(FAKE.deals)

    def symbols_get():
        return []

    def symbol_select(name, enable):
        return True

    def symbols_total():
        return 10

    def copy_rates_range(*a, **k):
        return None

    for fn in (
        initialize, shutdown, last_error, terminal_info, account_info,
        symbol_info, symbol_info_tick, order_send, positions_get, orders_get,
        history_deals_get, symbols_get, symbol_select, symbols_total,
        copy_rates_range,
    ):
        setattr(m, fn.__name__, fn)
    return m


sys.modules["MetaTrader5"] = _make_mt5_module()

from app import mt5_client as mc  # noqa: E402


@pytest.fixture
def client():
    # Re-bind this file's fake MetaTrader5 before constructing the client so
    # cross-module collection order can't swap in another test file's FAKE.
    sys.modules["MetaTrader5"] = _make_mt5_module()
    c = mc.MT5Client()
    yield c
    _run(c.shutdown())


def _run(coro):
    return asyncio.run(coro)


def _ok_terminal():
    FAKE.terminal = _NS(connected=True, trade_allowed=True)
    FAKE.account = _NS(
        login=91350, server="MetaQuotes-Demo", name="", currency="USD",
        leverage=100, balance=10000.0, credit=0.0, equity=10000.0, margin=0.0,
        margin_free=10000.0, margin_level=0.0, trade_allowed=True, trade_mode=0,
    )


# ── priority queue ─────────────────────────────────────────────


def test_trade_priority_jumps_market_queue(client):
    """A trade job queued while a slow market poll runs executes first after
    the poll, and a *second* normal job queued later must not overtake it."""
    order: list[str] = []
    gate = threading.Event()

    def slow_market():
        order.append("market")
        gate.wait(2.0)
        return "m"

    def trade():
        order.append("trade")
        return "t"

    async def scenario():
        t1 = asyncio.get_running_loop()
        fut_market = t1.create_task(client.run(slow_market))
        await asyncio.sleep(0.05)  # market is executing
        fut_normal = t1.create_task(client.run(trade))  # normal priority
        fut_trade = t1.create_task(
            client.run(trade, priority=mc.PRIORITY_TRADE)
        )
        await asyncio.sleep(0.05)
        gate.set()
        await fut_market
        await fut_trade
        await fut_normal

    _run(scenario())
    assert order == ["market", "trade", "trade"]  # both queued jobs ran
    # the trade-priority job was dequeued before the normal one
    assert order[1] == "trade"


# ── order_send request shape ───────────────────────────────────


def test_place_market_buy_request(client):
    _ok_terminal()
    FAKE.send_result = _NS(retcode=10009, deal=555, order=777, price=1.10002,
                           volume=0.10, comment="")
    out = _run(client.place_market("EURUSD", 0.1, "buy", magic=42, comment="x"))
    req = FAKE.last_send
    assert req["action"] == 1
    assert req["type"] == 0  # ORDER_TYPE_BUY
    assert req["symbol"] == "EURUSD"
    assert req["volume"] == pytest.approx(0.1)
    assert req["price"] == pytest.approx(1.10002)  # ask for buy
    assert req["magic"] == 42
    assert out["success"] is True
    assert out["deal"] == 555
    assert out["retcode_text"] == "交易请求已完成"
    # M14 regression: the route declares response_model=TradeResult (extra=
    # "forbid"); the result dict must validate against it or FastAPI raises a
    # ResponseValidationError → HTTP 500 on every SUCCESSFUL trade.  (A debug
    # "request" echo key once broke exactly this and slipped past these tests
    # because they call client.place_market directly, bypassing the model.)
    from app.schemas import TradeResult

    TradeResult(**out)  # raises pydantic ValidationError on any extra key


def test_place_market_rejects_when_trade_not_allowed(client, monkeypatch):
    _ok_terminal()
    FAKE.terminal = _NS(connected=True, trade_allowed=False)
    with pytest.raises(mc.MT5TradeNotAllowed):
        _run(client.place_market("EURUSD", 0.1, "buy"))


def test_login_whitelist_blocks_unknown_account(client, monkeypatch):
    _ok_terminal()
    monkeypatch.setattr(mc.settings, "TRADE_ALLOWED_LOGINS", "111,222")
    with pytest.raises(mc.MT5TradeNotAllowed, match="白名单"):
        _run(client.place_market("EURUSD", 0.1, "buy"))


def test_feature_flag_blocks_all_trades(client, monkeypatch):
    _ok_terminal()
    monkeypatch.setattr(mc.settings, "TRADE_ENABLED", False)
    with pytest.raises(mc.MT5TradeNotAllowed, match="未启用"):
        _run(client.place_market("EURUSD", 0.1, "buy"))


def test_volume_normalised_down_to_step(client):
    _ok_terminal()
    FAKE.send_result = _NS(retcode=10009, deal=1, order=1, price=1.1,
                           volume=0.18, comment="")
    _run(client.place_market("EURUSD", 0.189, "buy"))
    assert FAKE.last_send["volume"] == pytest.approx(0.18)


def test_volume_out_of_range_422(client):
    _ok_terminal()
    with pytest.raises(mc.MT5Error) as ei:
        _run(client.place_market("EURUSD", 0.001, "buy"))
    assert ei.value.status_code == 422


def test_deviation_clamped(client):
    _ok_terminal()
    FAKE.send_result = _NS(retcode=10009, deal=1, order=1, price=1.1,
                           volume=0.1, comment="")
    _run(client.place_market("EURUSD", 0.1, "buy", deviation=99999))
    assert FAKE.last_send["deviation"] == mc.settings.TRADE_MAX_DEVIATION


def test_retcode_rejected_maps_text(client):
    _ok_terminal()
    FAKE.send_result = _NS(retcode=10018, deal=0, order=0, price=0.0,
                           volume=0.0, comment="")
    out = _run(client.place_market("EURUSD", 0.1, "buy"))
    assert out["success"] is False
    assert out["retcode_text"] == "资金不足"


def test_order_send_none_raises(client):
    _ok_terminal()
    FAKE.fail_order_send = True
    with pytest.raises(mc.MT5Error, match="order_send"):
        _run(client.place_market("EURUSD", 0.1, "buy"))
    FAKE.fail_order_send = False


# ── close / cancel ─────────────────────────────────────────────


def test_close_position_full(client):
    _ok_terminal()
    FAKE.positions = [_NS(ticket=100, symbol="EURUSD", type=0, volume=0.2,
                          price_open=1.09, price_current=1.1, sl=0.0, tp=0.0,
                          profit=20.0, swap=0.0, commission=0.0, time=1,
                          magic=42, identifier=100, comment="")]
    FAKE.send_result = _NS(retcode=10009, deal=888, order=999, price=1.1,
                           volume=0.2, comment="close")
    out = _run(client.close_position(100, None))
    req = FAKE.last_send
    assert req["type"] == 1  # closing a buy → sell
    assert req["position"] == 100
    assert req["price"] == pytest.approx(1.10000)  # bid when selling
    assert out["deal"] == 888


def test_close_missing_position_404(client):
    _ok_terminal()
    FAKE.positions = []
    with pytest.raises(mc.MT5Error) as ei:
        _run(client.close_position(12345))
    assert ei.value.status_code == 404


# ── slippage snapshot (M4 calibration) ─────────────────────────


def test_place_market_returns_request_quote_snapshot(client):
    """buy: requested_price = ask; the bid/ask pair rides on every result."""
    _ok_terminal()
    FAKE.send_result = _NS(retcode=10009, deal=5, order=5, price=1.10004,
                           volume=0.1, comment="")
    out = _run(client.place_market("EURUSD", 0.1, "buy"))
    assert out["ask"] == pytest.approx(1.10002)
    assert out["bid"] == pytest.approx(1.10000)
    from app.schemas import TradeResult

    TradeResult(**out)


def test_close_position_returns_request_quote_snapshot(client):
    """Closes carry the same snapshot (closing buy → sell at bid)."""
    _ok_terminal()
    FAKE.positions = [_NS(ticket=100, symbol="EURUSD", type=0, volume=0.2,
                          price_open=1.09, price_current=1.1, sl=0.0, tp=0.0,
                          profit=20.0, swap=0.0, commission=0.0, time=1,
                          magic=42, identifier=100, comment="")]
    FAKE.send_result = _NS(retcode=10009, deal=9, order=9, price=1.1,
                           volume=0.2, comment="close")
    out = _run(client.close_position(100, None))
    assert out["bid"] == pytest.approx(1.10000)
    assert out["ask"] == pytest.approx(1.10002)
    from app.schemas import TradeResult

    TradeResult(**out)


def test_cancel_order_request(client):
    _ok_terminal()
    FAKE.send_result = _NS(retcode=10009, deal=0, order=55, price=0.0,
                           volume=0.0, comment="")
    out = _run(client.cancel_order(55))
    assert FAKE.last_send["action"] == 6  # TRADE_ACTION_REMOVE（包里没有 CANCEL）
    assert FAKE.last_send["order"] == 55
    assert out["success"] is True


# ── positions / orders / deals mapping ─────────────────────────


def test_positions_mapping(client):
    FAKE.positions = [_NS(ticket=1, symbol="EURUSD", type=1, volume=0.1,
                          price_open=1.1, price_current=1.09, sl=0.0, tp=0.0,
                          profit=10.0, swap=0.0, commission=-0.5, time=1791590100,
                          magic=7, identifier=1, comment="c")]
    rows = _run(client.positions())
    assert rows[0]["type"] == "sell"
    assert rows[0]["ticket"] == 1
    assert rows[0]["profit"] == pytest.approx(10.0)


def test_deals_symbol_filter(client):
    FAKE.deals = [
        _NS(ticket=1, order=1, time=1, symbol="EURUSD", type=0, entry=0,
            volume=0.1, price=1.1, commission=-0.1, swap=0.0, profit=0.0,
            balance=10000.0, magic=7, comment=""),
        _NS(ticket=2, order=2, time=2, symbol="GBPUSD", type=1, entry=1,
            volume=0.1, price=1.2, commission=-0.1, swap=0.0, profit=5.0,
            balance=10005.0, magic=7, comment=""),
    ]
    rows = _run(client.deals(0, int(time.time()) + 10, symbol="GBPUSD"))
    assert len(rows) == 1
    assert rows[0]["symbol"] == "GBPUSD"
