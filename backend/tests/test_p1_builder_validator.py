"""
P1 服务端可信校验 测试（P2 已适配结构化错误：list[dict]{targetId, message}）。

直接测 builder_validator.validate_builder_config 模块 + API 层集成（非法 DSL / 未知 version / AST error 都应 422）。
"""
from __future__ import annotations

import uuid

import pytest

from app.services.builder_validator import validate_builder_config

STRAT = "/api/v1/strategies"


def _msgs(errors: list[dict]) -> list[str]:
    """从结构化错误提取纯 message 列表，方便 any("xxx" in e for e in _msgs(...)) 风格断言。"""
    return [e.get("message", "") for e in errors]


def _ok_config() -> dict:
    """合法 V1 DSL 基座。"""
    return {
        "version": 1,
        "symbol": "XAUUSD",
        "timeframe": "H1",
        "volume": 0.1,
        "direction": "both",
        "indicators": [
            {
                "id": "ma_1",
                "kind": "MA",
                "period": 20,
            }
        ],
        "rules": [
            {
                "id": "rule_1",
                "op": "greater_than",
                "left": {"type": "indicator", "indicatorId": "ma_1", "output": "value"},
                "right": {"type": "close"},
                "open": "buy",
                "close": None,
            }
        ],
    }


def _skeleton() -> str:
    """通过 AST 校验的最小 Python 策略骨架。"""
    return (
        "def on_init(ctx):\n"
        '    ctx.declare(symbol="XAUUSD", period="H1", params={})\n'
        "\n"
        "def on_bar(ctx, bar):\n"
        "    pass\n"
        "\n"
        "def on_order(ctx, order):\n"
        "    pass\n"
        "\n"
        "def on_stop(ctx, reason):\n"
        "    pass\n"
    )


# ── builder_validator 模块单测 ─────────────────────────────────────


def test_validator_ok_config_passes():
    errors = validate_builder_config(_ok_config())
    assert errors == [], f"合法配置却报错: {errors}"


def test_validator_unknown_version_rejected():
    cfg = _ok_config()
    cfg["version"] = 99
    errors = validate_builder_config(cfg)
    assert any("未知 DSL 版本" in m for m in _msgs(errors)), errors
    assert all(e["targetId"] is None for e in errors)


def test_validator_symbol_empty_rejected():
    cfg = _ok_config()
    cfg["symbol"] = ""
    errors = validate_builder_config(cfg)
    assert any("品种" in m for m in _msgs(errors)), errors


def test_validator_volume_zero_rejected():
    cfg = _ok_config()
    cfg["volume"] = 0
    errors = validate_builder_config(cfg)
    assert any("手数" in m for m in _msgs(errors)), errors


def test_validator_macd_fast_too_small():
    cfg = _ok_config()
    cfg["indicators"] = [{"id": "m1", "kind": "MACD", "fast": 1, "slow": 26, "signal": 9}]
    errors = validate_builder_config(cfg)
    assert any("MACD 快线" in m for m in _msgs(errors)), errors
    # 指标校验的 targetId 应该是该指标的 id
    assert any(e["targetId"] == "m1" for e in errors), errors


def test_validator_macd_slow_not_greater_than_fast():
    cfg = _ok_config()
    cfg["indicators"] = [{"id": "m1", "kind": "MACD", "fast": 20, "slow": 10, "signal": 9}]
    errors = validate_builder_config(cfg)
    assert any("MACD 慢线" in m for m in _msgs(errors)), errors


def test_validator_period_zero_rejected():
    cfg = _ok_config()
    cfg["indicators"] = [{"id": "ma1", "kind": "MA", "period": 0}]
    errors = validate_builder_config(cfg)
    assert any("MA 周期" in m for m in _msgs(errors)), errors


def test_validator_boll_std_zero_rejected():
    cfg = _ok_config()
    cfg["indicators"] = [{"id": "b1", "kind": "BOLL", "period": 20, "std": 0}]
    errors = validate_builder_config(cfg)
    assert any("BOLL" in m for m in _msgs(errors)), errors


def test_validator_unknown_indicator_kind_rejected():
    cfg = _ok_config()
    cfg["indicators"] = [{"id": "x1", "kind": "FAKE", "period": 20}]
    errors = validate_builder_config(cfg)
    assert any("未知指标类型" in m for m in _msgs(errors)), errors


def test_validator_duplicate_indicator_id():
    cfg = _ok_config()
    cfg["indicators"] = [
        {"id": "dup", "kind": "MA", "period": 10},
        {"id": "dup", "kind": "EMA", "period": 20},
    ]
    errors = validate_builder_config(cfg)
    assert any("id 重复" in m for m in _msgs(errors)), errors


def test_validator_rule_no_action_rejected():
    cfg = _ok_config()
    cfg["rules"] = [
        {
            "id": "r1",
            "op": "greater_than",
            "left": {"type": "close"},
            "right": {"type": "number", "value": 1.0},
            "open": None,
            "close": None,
        }
    ]
    errors = validate_builder_config(cfg)
    assert any("至少配置一个动作" in m for m in _msgs(errors)), errors
    # 规则校验的 targetId 应该是该规则的 id
    assert any(e["targetId"] == "r1" for e in errors), errors


def test_validator_direction_conflict_long_only_vs_sell():
    cfg = _ok_config()
    cfg["direction"] = "long_only"
    cfg["rules"][0]["open"] = "sell"
    errors = validate_builder_config(cfg)
    assert any("仅做多" in m for m in _msgs(errors)), errors


def test_validator_direction_conflict_short_only_vs_buy():
    cfg = _ok_config()
    cfg["direction"] = "short_only"
    cfg["rules"][0]["open"] = "buy"
    errors = validate_builder_config(cfg)
    assert any("仅做空" in m for m in _msgs(errors)), errors


def test_validator_dangling_indicator_ref():
    cfg = _ok_config()
    cfg["rules"][0]["left"]["indicatorId"] = "nonexistent"
    errors = validate_builder_config(cfg)
    assert any("已删除的指标" in m for m in _msgs(errors)), errors
    # 悬空引用的错误 targetId 应该指向规则
    assert any(e["targetId"] == "rule_1" for e in errors), errors


def test_validator_invalid_output_for_kind():
    cfg = _ok_config()
    cfg["indicators"] = [{"id": "ma1", "kind": "MA", "period": 20}]
    cfg["rules"][0]["left"] = {"type": "indicator", "indicatorId": "ma1", "output": "upper"}
    errors = validate_builder_config(cfg)
    assert any("不支持输出通道" in m for m in _msgs(errors)), errors


def test_validator_dangling_indicator_ref_non_indicator_kind_ok():
    """close 操作数不引用 indicatorId，不报错。"""
    cfg = _ok_config()
    cfg["rules"][0]["left"] = {"type": "close"}
    cfg["rules"][0]["right"] = {"type": "number", "value": 1.5}
    cfg["rules"][0]["open"] = "buy"
    errors = validate_builder_config(cfg)
    assert errors == [], errors


# ── API 集成层 ─────────────────────────────────────────────────────


def _reg(client) -> str:
    s = uuid.uuid4().hex[:8]
    r = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"p2_{s}@test.com",
            "username": f"p2_{s}",
            "password": "Passw0rd!",
            "full_name": "P2 验证",
        },
    )
    assert r.status_code in (200, 201), f"注册: {r.status_code} {r.text}"
    return r.json()["access_token"]


def _auth(t: str):
    return {"Authorization": f"Bearer {t}"}


def _mk_strat(client, hdrs: dict) -> dict:
    r = client.post(
        STRAT + "/",
        json={
            "name": f"P2_{uuid.uuid4().hex[:6]}",
            "source_code": _skeleton(),
            "builder_config": None,
        },
        headers=hdrs,
    )
    assert r.status_code == 201, f"创建: {r.status_code} {r.text}"
    sid = r.json()["id"]
    return client.get(f"{STRAT}/{sid}/", headers=hdrs).json()


def test_p2_api_builder_save_rejects_bad_dsl(client):
    hdrs = _auth(_reg(client))
    s = _mk_strat(client, hdrs)
    bad_cfg = _ok_config()
    bad_cfg["version"] = 99  # 未知 version

    r = client.put(
        f"{STRAT}/{s['id']}/builder-save",
        json={
            "source_code": _skeleton(),
            "builder_config": bad_cfg,
            "base_updated_at": s["updated_at"],
        },
        headers=hdrs,
    )
    assert r.status_code == 422, f"期望 422，得到 {r.status_code}: {r.text}"
    body = r.json()["detail"]
    assert body["type"] == "builder_config"
    # P2：后端返回结构化 errors（list[dict]）
    assert isinstance(body["errors"], list)
    assert all("targetId" in e and "message" in e for e in body["errors"]), body["errors"]


def test_p2_api_builder_save_rejects_ast_error(client):
    hdrs = _auth(_reg(client))
    s = _mk_strat(client, hdrs)

    # 合法 DSL + 非法源码（没有 on_init）→ 422
    r = client.put(
        f"{STRAT}/{s['id']}/builder-save",
        json={
            "source_code": "x = 1\n",  # 无回调函数
            "builder_config": _ok_config(),
            "base_updated_at": s["updated_at"],
        },
        headers=hdrs,
    )
    assert r.status_code == 422, f"期望 422，得到 {r.status_code}: {r.text}"
    assert r.json()["detail"]["type"] == "source_code"


def test_p2_api_create_rejects_bad_dsl(client):
    hdrs = _auth(_reg(client))
    bad_cfg = _ok_config()
    bad_cfg["volume"] = -1

    r = client.post(
        STRAT + "/",
        json={
            "name": f"P2_bad_{uuid.uuid4().hex[:6]}",
            "source_code": _skeleton(),
            "builder_config": bad_cfg,
        },
        headers=hdrs,
    )
    assert r.status_code == 422, f"期望 422，得到 {r.status_code}: {r.text}"


def test_p2_api_create_accepts_null_builder_config(client):
    """旧策略/代码编辑器策略 builder_config=null 应该放行。"""
    hdrs = _auth(_reg(client))
    r = client.post(
        STRAT + "/",
        json={
            "name": f"P2_null_{uuid.uuid4().hex[:6]}",
            "source_code": _skeleton(),
            "builder_config": None,
        },
        headers=hdrs,
    )
    assert r.status_code == 201, f"null builder_config 应放行，得到 {r.status_code}: {r.text}"
