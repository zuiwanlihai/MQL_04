"""长驻 runner 的公共骨架（P2-6 批次一）。

paper_runner 与 live_runner 的 main() 除三处差异外逐字节相同：sink 类型
（paper：模块级 RunnerWriter；live：带 stdin 分拣的 Transport）、读命令函数
（paper 丢弃非 cmd 行；live 从 inbox 解复用）、以及 live 独有的一条 account
命令分支。骨架、命令循环与 ``_emit_all`` 收集于此，差异全部作为参数传入。

**心跳必须先于任何可能挂住的步骤启动**——父进程用「STALL 秒内连心跳都没有」
判定卡死，晚启动会把启动耗时误判成卡死。

:class:`Transport`（live 的 order RPC + inbox 停泊）刻意不共享：它存在的理由
是「RPC 阻塞期间必须暂存 bars/stop，否则死锁」，paper 侧没有这条通道；backtest
runner 是第三种形态（单发、stdin beacon），同样不并入。
"""

from __future__ import annotations

import argparse
import os
import pathlib
import random
import sys
from typing import Any, Callable, Iterable, Optional, Protocol

from app.sandbox.engine import EngineEvent
from app.sandbox.incremental import IncrementalEngine
from app.sandbox.payload import BacktestPayload, BarArrays, load_payload
from app.sandbox.runner_io import (
    EXIT_DONE,
    EXIT_LOAD_FAILED,
    EXIT_SAFETY,
    RunnerWriter,
    start_wedge_reaper,
)


class EventSink(Protocol):
    """runner 事件出口；RunnerWriter 与 Transport 都满足此结构。"""

    def emit(self, kind: str, data: dict) -> None: ...

    def start_heartbeat(self) -> None: ...

    def stop_heartbeat(self) -> None: ...

    @property
    def writer(self) -> RunnerWriter: ...


def emit_all(sink: EventSink, events: Iterable[EngineEvent]) -> dict | None:
    """Drain an engine event iterator onto the sink; return the fail dict or None."""
    fail: dict | None = None
    for ev in events:
        if ev.kind == "fail":
            fail = ev.data
            sink.emit("fail", ev.data)
        else:
            sink.emit(ev.kind, ev.data)
    return fail


_BuildEngine = Callable[[BacktestPayload, BarArrays, pathlib.Path], IncrementalEngine]
_ExtraCommand = Callable[[IncrementalEngine, str, dict], bool]


def run_incremental_process(
    *,
    prog: str,
    build_engine: _BuildEngine,
    sink: EventSink,
    read_command: Callable[[], Optional[dict]],
    extra_command: Optional[_ExtraCommand] = None,
    argv: Optional[list[str]] = None,
) -> int:
    """paper/live runner 的公共 main 骨架：load → start → 阻塞命令循环。

    ``build_engine(payload, bars, storage_path)`` 造具体引擎；
    ``read_command`` 取一条 stdin 命令（各 runner 的分拣规则不同）；
    ``extra_command`` 处理子类独有分支（返回 True 表示已消费）。
    """
    parser = argparse.ArgumentParser(prog=prog)
    parser.add_argument("--payload", required=True, help="payload directory")
    args = parser.parse_args(argv)

    # 心跳必须早于任何可能挂住的步骤启动，否则父进程会把启动耗时误判成卡死。
    sink.start_heartbeat()

    payload_dir = pathlib.Path(args.payload)
    payload, bars = load_payload(payload_dir)
    random.seed(payload.seed)

    engine = build_engine(payload, bars, payload_dir / "storage.json")
    engine.restore_storage()
    start_wedge_reaper(sink.writer, engine.eng.unresolved_timeout_seconds)

    # start: load → on_init → replay startup history
    fail = emit_all(sink, engine.start())
    if fail is not None:
        return EXIT_LOAD_FAILED if fail.get("load_failed") else EXIT_SAFETY
    sink.emit("started", {})  # parent-side gate: ticks may be delivered from now on

    while True:
        cmd = read_command()
        if cmd is None:
            os._exit(143)  # stdin EOF — parent is gone
        name: Any = cmd.get("cmd")  # read_command 契约：返回的 dict 必有非空 cmd
        if name == "bars":
            if emit_all(sink, engine.feed(list(cmd.get("rows") or []))) is not None:
                return EXIT_SAFETY
        elif name == "ticks":
            if emit_all(sink, engine.feed_ticks(
                list(cmd.get("rows") or [])
            )) is not None:
                return EXIT_SAFETY
        elif extra_command is not None and extra_command(engine, name, cmd):
            continue  # 子类独有分支（live：broker 镜像推送）已处理
        elif name == "status":
            sink.emit("account", engine.account_event().data)
        elif name == "reset_anchor":
            new = engine.reset_anchor()
            if new is None:
                engine.eng.emit_log("warn", "重置锚点失败：暂无可用报价")
            else:
                info = engine.eng.symbol_info
                digits = info.digits if info is not None else 5
                engine.eng.emit_log(
                    "info", f"网格锚点已重置为当前价 {new:.{digits}f}"
                )
            emit_all(sink, engine.eng._drain())
        elif name == "stop":
            emit_all(sink, engine.stop(bool(cmd.get("close", True))))
            sink.stop_heartbeat()
            sys.stdout.flush()
            return EXIT_DONE
