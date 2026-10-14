"""测试替身：模拟「熔断已触发、但异步异常投递不进去」的 runner。

真实场景是回调卡在不返回的 C 调用里（灾难性回溯的 ``re`` 是典型）：看门狗已经
判定超时，但异常只有回到字节码边界才生效，于是永远悬着。这里用一个恒定的探针
直接复现那个状态，从而不必真的构造一次指数级回溯。

它验证三件容易悄悄坏掉的事：reaper 确实会结束进程、失败事件在 ``os._exit``
之前已经写出去、退出码 2 会被父进程归类为失败。
"""

from __future__ import annotations

import argparse
import time

from app.sandbox.runner_io import RunnerWriter, start_wedge_reaper


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wedge_runner")
    parser.add_argument("--payload", required=True, help="payload directory")
    parser.parse_args(argv)

    writer = RunnerWriter(interval=0)
    writer.emit("started", {})
    # 恒定远大于宽限期的读数 ⇒ 第一次轮询就判定「救不回来了」。
    start_wedge_reaper(writer, lambda: 999.0)

    time.sleep(120)  # reaper 正常时走不到这里
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
