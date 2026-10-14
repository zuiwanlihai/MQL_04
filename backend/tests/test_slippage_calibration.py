"""Slippage calibration + live deviation report tests (M4 P1).

Covers the deal decomposition identity (slip + spread + drift == total),
calibration aggregation (p95 recommendation, insufficient sample, cross-user
isolation) and the two compute-on-read endpoints.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.services.slippage import (
    MIN_CALIBRATION_SAMPLES,
    _deviation_row,
    _percentile,
)

API_CAL = "/api/v1/backtests/slippage-calibration"

PT = Decimal("0.00001")
CS = Decimal("100000.0")
SYMBOL_INFO = {"point": 0.00001, "trade_contract_size": 100000.0}


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _token(client) -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={"email": f"sl-{uuid.uuid4().hex[:10]}@test.com",
              "username": f"sl{uuid.uuid4().hex[:10]}",
              "password": "Passw0rd!", "full_name": "滑点测试"},
    )
    assert r.status_code == 201
    return r.json()["access_token"]


def _uid(client, token) -> str:
    from jose import jwt

    from app.config import settings

    return jwt.decode(token, settings.JWT_SECRET_KEY,
                      algorithms=[settings.JWT_ALGORITHM])["sub"]


def _seed_run(client, token, *, symbol: str = "EURUSD") -> dict:
    async def _ins():
        from sqlalchemy import select as _select

        from app.engine.session import AsyncSessionLocal
        from app.models import GatewayNode, LiveRun, MT5Account, Strategy

        uid = uuid.UUID(_uid(client, token))
        async with AsyncSessionLocal() as db:
            node = (await db.execute(_select(GatewayNode).where(
                GatewayNode.host == "127.0.0.1", GatewayNode.port == 8090
            ))).scalars().first()
            if node is None:
                node = GatewayNode(host="127.0.0.1", port=8090, status="online")
                db.add(node)
                await db.flush()
            strat = Strategy(user_id=uid, name=f"sl-{uuid.uuid4().hex[:8]}",
                             timeframe="H1", status="active")
            db.add(strat)
            acct = MT5Account(
                login=830000 + (uuid.uuid4().int % 9000),
                server="MetaQuotes-Demo", name="t", account_type="demo",
                leverage=100, currency="USD", gateway_node_id=node.id,
                user_id=uid, live_allowed=True,
            )
            db.add(acct)
            await db.flush()
            run = LiveRun(
                user_id=uid, strategy_id=strat.id, account_id=acct.id,
                source_code="x", symbol=symbol, timeframe="H1",
                execution_mode="standard", allocated_capital=Decimal("1000"),
                leverage=100, magic=int(uuid.uuid4().int % 100000),
                status="stopped", balance=Decimal("1000"),
                equity=Decimal("1000"), peak_equity=Decimal("1000"),
                day_start_equity=Decimal("1000"), day_start_date="2026-10-11",
                margin_free=Decimal("1000"),
                config_snapshot={"symbol_info": SYMBOL_INFO},
            )
            db.add(run)
            await db.commit()
            return {"run_id": str(run.id), "account_id": str(acct.id)}
    return client.portal.call(_ins)


def _deal_factory(run_id: str):
    """Deal row builder with realistic defaults (EURUSD, buy/in)."""
    base = datetime(2026, 10, 11, 8, 0, tzinfo=UTC)

    def make(ticket: int, **over) -> dict:
        row = {
            "run_id": uuid.UUID(run_id),
            "deal_ticket": ticket,
            "order_ticket": ticket,
            "direction": "buy",
            "entry": "in",
            "volume": Decimal("0.1"),
            "open_time": base + timedelta(minutes=ticket),
            "price": Decimal("1.10003"),
            "bid_at_request": Decimal("1.10000"),
            "ask_at_request": Decimal("1.10002"),
            "signal_price": Decimal("1.10000"),
            "signal_spread_points": Decimal("20"),
            "signal_bar_time": (
                base + timedelta(minutes=ticket) - timedelta(seconds=3)
            ),
            "retcode": 10009,
        }
        row.update(over)
        return row
    return make


def _insert_deals(client, rows: list[dict]) -> None:
    async def _ins():
        from app.engine.session import AsyncSessionLocal
        from app.models import LiveTrade

        async with AsyncSessionLocal() as db:
            for r in rows:
                db.add(LiveTrade(**r))
            await db.commit()
    client.portal.call(_ins)


# ── pure-function decomposition ───────────────────────────────


def _mk_deal(**over):
    from app.models import LiveTrade

    base = datetime(2026, 10, 11, 8, 0, tzinfo=UTC)
    row = {
        "run_id": uuid.uuid4(), "deal_ticket": 1, "order_ticket": 1,
        "direction": "buy", "entry": "in", "volume": Decimal("0.1"),
        "open_time": base, "price": Decimal("1.10003"),
        "bid_at_request": Decimal("1.10000"),
        "ask_at_request": Decimal("1.10002"),
        "signal_price": Decimal("1.10000"),
        "signal_spread_points": Decimal("20"),
        "signal_bar_time": base - timedelta(seconds=3),
    }
    row.update(over)
    return LiveTrade(**row)


def test_percentile_interpolation():
    assert _percentile([1.0], 95) == 1.0
    assert _percentile([0.5, 1, 2, 3, 10], 95) == pytest.approx(8.6)
    assert _percentile([0.5, 1, 2, 3, 10], 50) == 2.0


def test_buy_decomposition_is_additive():
    row = _deviation_row(_mk_deal(), PT, CS)
    # theoretical = 1.10000 + 20pt = 1.10020
    assert row["theoretical_price"] == Decimal("1.10020000")
    # slip=(1.10003-1.10002)/1e-5=1.0, spread=(1.10002-1.10000)/1e-5=2.0,
    # drift=(1.10000-1.10020)/1e-5=-20.0  →  total=-17.0
    assert row["slippage_points"] == Decimal("1.000000")
    assert row["actual_spread_points"] == Decimal("2.000000")
    assert row["drift_points"] == Decimal("-20.000000")
    assert row["total_deviation_points"] == Decimal("-17.000000")
    total = (float(row["slippage_points"]) + float(row["actual_spread_points"])
             + float(row["drift_points"]))
    assert abs(total - float(row["total_deviation_points"])) < 1e-9
    assert row["latency_seconds"] == 3.0


def test_sell_decomposition_bid_side():
    row = _deviation_row(_mk_deal(
        direction="sell", price=Decimal("1.09995"),
        bid_at_request=Decimal("1.09998"),
    ), PT, CS)
    # theoretical = signal close 1.10000 (sell fills at bid)
    assert row["theoretical_price"] == Decimal("1.10000000")
    assert row["actual_spread_points"] is None
    # slip=(1.09998-1.09995)/1e-5=3.0, drift=(1.10000-1.09998)/1e-5=2.0
    assert row["slippage_points"] == Decimal("3.000000")
    assert row["drift_points"] == Decimal("2.000000")
    assert row["total_deviation_points"] == Decimal("5.000000")


def test_deal_without_snapshot_folds_into_drift():
    row = _deviation_row(_mk_deal(
        bid_at_request=None, ask_at_request=None,
    ), PT, CS)
    assert row["slippage_points"] is None
    assert row["actual_spread_points"] is None
    assert row["drift_points"] == row["total_deviation_points"]


def test_deal_without_signal_reference_is_skipped():
    assert _deviation_row(_mk_deal(
        signal_price=None, signal_spread_points=None), PT, CS) is None


# ── calibration aggregation + endpoint ────────────────────────


def test_calibration_insufficient_and_isolation(client):
    token, other = _token(client), _token(client)
    seeded = _seed_run(client, token)
    mk = _deal_factory(seeded["run_id"])
    _insert_deals(client, [mk(1)])
    _seed_run(client, other)  # another user's run must not leak in

    r = client.get(f"{API_CAL}?symbol=EURUSD", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["sample_count"] == 1 and body["skipped_count"] == 0
    assert body["insufficient"] is True
    assert body["recommended_points"] is None
    assert body["sources"][0]["sample_count"] == 1
    assert client.get(f"{API_CAL}?symbol=EURUSD",
                      headers=_auth(other)).json()["sample_count"] == 0
    # unauthenticated / missing param
    assert client.get(f"{API_CAL}?symbol=EURUSD").status_code == 401
    assert client.get(API_CAL, headers=_auth(token)).status_code == 422


def test_calibration_p95_recommendation_and_skips(client):
    token = _token(client)
    seeded = _seed_run(client, token)
    mk = _deal_factory(seeded["run_id"])
    slips = [0.5, 1.0, 2.0, 3.0, 10.0]  # 5 samples → sufficient
    rows = []
    for i, slip_pts in enumerate(slips):
        if i % 2 == 0:  # buy: price = ask + slip*pt
            price = Decimal("1.10002") + Decimal(str(slip_pts)) * PT
            rows.append(mk(i + 1, direction="buy", price=price))
        else:  # sell: price = bid - slip*pt with the sell-side quote
            price = Decimal("1.09998") - Decimal(str(slip_pts)) * PT
            rows.append(mk(i + 1, direction="sell", price=price,
                           bid_at_request=Decimal("1.09998")))
    # one legacy row without a snapshot → skipped
    rows.append(mk(99, bid_at_request=None, ask_at_request=None))
    _insert_deals(client, rows)

    body = client.get(f"{API_CAL}?symbol=EURUSD",
                      headers=_auth(token)).json()
    assert body["sample_count"] == 5
    assert body["skipped_count"] == 1
    assert body["insufficient"] is False
    assert body["max_points"] == 10.0
    expected_p95 = _percentile(sorted(slips), 95)
    assert abs(float(body["p95_points"]) - expected_p95) < 1e-6
    # recommended = ceil(p95), floored at 0
    assert float(body["recommended_points"]) == int(expected_p95) + (
        1 if expected_p95 > int(expected_p95) else 0)
    assert body["sources"][0]["run_count"] == 1


# ── deviation report endpoint ─────────────────────────────────


def test_deviation_report_per_run(client):
    token = _token(client)
    seeded = _seed_run(client, token)
    mk = _deal_factory(seeded["run_id"])
    _insert_deals(client, [mk(1), mk(2, direction="sell",
                                 price=Decimal("1.09995"),
                                 bid_at_request=Decimal("1.09998"))])

    r = client.get(f"/api/v1/live/{seeded['run_id']}/deviation",
                   headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sample_count"] == 2 and body["skipped_count"] == 0
    assert body["symbol"] == "EURUSD"
    assert body["verdict"] == "insufficient"  # 2 < MIN_CALIBRATION_SAMPLES
    assert len(body["trades"]) == 2
    buy = next(t for t in body["trades"] if t["direction"] == "buy")
    assert buy["total_deviation_points"] == -17.0
    assert buy["latency_seconds"] == 3.0

    # cross-user → 404
    other = _token(client)
    assert client.get(f"/api/v1/live/{seeded['run_id']}/deviation",
                      headers=_auth(other)).status_code == 404


def test_deviation_verdict_thresholds(client):
    token = _token(client)
    seeded = _seed_run(client, token)
    mk = _deal_factory(seeded["run_id"])
    # 5 buys each 20pt adverse: price = theoretical + 20pt
    rows = [mk(i + 1, price=Decimal("1.10020") + Decimal("20") * PT)
            for i in range(5)]
    _insert_deals(client, rows)
    body = client.get(f"/api/v1/live/{seeded['run_id']}/deviation",
                      headers=_auth(token)).json()
    assert body["sample_count"] == MIN_CALIBRATION_SAMPLES
    assert body["verdict"] == "divergent"
    assert float(body["avg_total_deviation_points"]) == 20.0
