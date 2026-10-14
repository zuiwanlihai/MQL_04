"""测试替身：先打若干心跳、再打一个真事件，然后正常退出的 runner。

用来验证心跳确实被父进程剥离——心跳是传输层保活，不是引擎事件。若它漏进
``result.events``，paper/live 管理器会把它当业务事件去持久化。
"""

from __future__ import annotations

import argparse
import time

from app.sandbox.runner_io import RunnerWriter


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="heartbeat_runner")
    parser.add_argument("--payload", required=True, help="payload directory")
    parser.parse_args(argv)

    writer = RunnerWriter(interval=0.05)
    writer.start_heartbeat()
    time.sleep(0.4)  # 期间应打出若干心跳
    writer.stop_heartbeat()
    writer.emit("log", {"message": "real-event"})
    writer.emit("done", {})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
