"""Windows power-plan probes + fix commands (M11, PRD M1 电源管理).

实盘结构性前提：接通电源下系统不得进入睡眠/休眠（否则平台风控随终端一起失联，
即 PRD 风险 #7 的双重风险真空）。本模块只 **查询** 当前配置并 **生成** 修复命令；
真正执行必须显式 ``execute=True``（改用户系统设置属敏感操作，默认只给指引）。

All subprocess calls run on a worker thread with a timeout and degrade to
``warn`` on any failure (non-Windows dev box, missing powercfg, etc.) —
a probe must never crash the gateway.
"""

from __future__ import annotations

import asyncio
import subprocess

CMD_TIMEOUT = 8.0

# powercfg 官方符号名（比裸 GUID 稳：自定义方案下 GUID 直查会报「不存在」）
_SLEEP_ARGS = ["powercfg", "/query", "SCHEME_CURRENT", "SUB_SLEEP", "STANDBYIDLE"]
_HIBERNATE_ARGS = ["powercfg", "/query", "SCHEME_CURRENT", "SUB_SLEEP", "HIBERNATEIDLE"]


def smart_decode(raw: bytes) -> str:
    """Decode console output regardless of the host codepage.

    Windows consoles emit GBK (cp936) on zh-CN but UTF-8 under PowerShell 7 /
    chcp 65001.  Try UTF-8 strictly first (it rejects GBK high bytes), then
    GBK, then UTF-8 with replacement — so the Chinese detail never mojibakes.
    """
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=CMD_TIMEOUT)
        return proc.returncode, smart_decode((proc.stdout or b"") + (proc.stderr or b""))
    except FileNotFoundError:
        return -1, "命令不存在（非 Windows 环境？）"
    except subprocess.TimeoutExpired:
        return -1, f"命令超时（>{CMD_TIMEOUT:.0f}s）"
    except Exception as exc:  # noqa: BLE001 — probe must never raise
        return -1, f"{type(exc).__name__}: {exc}"


def _parse_current_value(output: str) -> int | None:
    """Extract the AC setting value (seconds) from ``powercfg /query`` output.

    中文/英文系统均匹配「当前交流电源设置索引 / Current AC Power Setting Index」，
    十六进制形如 ``0x00000000``。
    """
    import re

    m = re.search(
        r"(?:Current AC Power Setting Index|当前交流电源设置索引)\s*[:：]\s*0x([0-9a-fA-F]+)",
        output)
    if m:
        return int(m.group(1), 16)
    m = re.search(r"0x([0-9a-fA-F]{8})", output)
    return int(m.group(1), 16) if m else None


async def probe_power_plan() -> dict:
    """Query sleep/hibernate timeouts under AC power.

    Returns ``{ok, sleep_ac, hibernate_ac, detail}`` — ``ok`` only when both
    are 0 (never sleep / never hibernate).  ``sleep_ac=None`` = probe failed.
    """
    rc, out_sleep = await asyncio.to_thread(_run, _SLEEP_ARGS)
    rc2, out_hib = await asyncio.to_thread(_run, _HIBERNATE_ARGS)

    if rc != 0 and rc2 != 0:
        return {"ok": None, "sleep_ac": None, "hibernate_ac": None,
                "detail": f"powercfg 查询失败：{out_sleep[:120]}"}

    sleep_ac = _parse_current_value(out_sleep) if rc == 0 else None
    hibernate_ac = _parse_current_value(out_hib) if rc2 == 0 else None

    parts = []
    if sleep_ac is not None:
        parts.append(f"AC 睡眠={sleep_ac}s")
    if hibernate_ac is not None:
        parts.append(f"AC 休眠={hibernate_ac}s")
    ok = (sleep_ac == 0) and (hibernate_ac in (0, None))
    return {"ok": ok, "sleep_ac": sleep_ac, "hibernate_ac": hibernate_ac,
            "detail": "，".join(parts) if parts else "无法读取电源配置"}


def fix_commands() -> list[str]:
    """The one-click fix recipe (never run implicitly)."""
    return [
        "powercfg /change standby-timeout-ac 0",
        "powercfg /change hibernate-timeout-ac 0",
        "powercfg /change monitor-timeout-ac 15",
    ]


async def apply_fix(execute: bool) -> dict:
    """Return fix commands; only run them when ``execute=True``."""
    cmds = fix_commands()
    if not execute:
        return {"executed": False, "commands": cmds,
                "detail": "指引模式：请以管理员身份在 PowerShell 中执行以上命令"}
    results = []
    for cmd in cmds:
        rc, out = await asyncio.to_thread(_run, cmd.split())
        results.append({"cmd": cmd, "rc": rc, "out": out.strip()[:200]})
    ok = all(r["rc"] == 0 for r in results)
    return {"executed": True, "commands": cmds, "results": results,
            "detail": "电源方案已修复" if ok else "部分命令执行失败，见明细"}
