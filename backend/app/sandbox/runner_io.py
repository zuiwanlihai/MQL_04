"""长驻 runner 的 stdout 写入器 + 心跳线程。

**为什么需要心跳**：paper/live runner 在两个命令之间阻塞在 stdin 上，这期间
一行 stdout 都不产生。父进程于是无法用「多久没输出」区分「行情清淡、本来就
没活干」和「回调卡死」——前者是常态，后者要杀。由一条**独立于引擎线程**的
线程定期写心跳行，判据就变得简单且正确：

    STALL 秒内一行都收不到（连心跳都没有）＝ 子进程真的卡住了

心跳线程与主线程并发写 stdout，因此每次写入都必须持同一把锁。否则两行会交错
把 JSONL 撕成两半，父进程只会看到解析失败并静默丢弃——那恰好会制造「看起来
还活着、实际已经丢了事件」的假信号。

心跳线程与回调熔断（见 :mod:`app.sandbox.engine`）是互补的两层：熔断能在纯
Python 循环里精确中断并留下失败原因；心跳这一层则覆盖熔断管不到的情况
（例如灾难性回溯的 ``re`` 霸占 GIL），代价是只能整体杀进程。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import Callable, Optional

# 父进程据此把心跳从引擎事件流里剔除（见 RunnerSupervisor.run）。
HEARTBEAT_EVENT = "heartbeat"

# 单行上限，与 runner 走线时的既有约定一致。
_MAX_LINE = 262_144

# 熔断触发后留给「异步异常投递生效」的宽限；超时即认定救不回来。
DEFAULT_WEDGE_GRACE_SECONDS = 10.0

# 与 runner 退出码约定一致：2 = 安全终止（supervisor 归类为 TERM_FAILED）。
EXIT_SAFETY = 2

# 卡死时补发的失败事件：父进程需要一个明确的失败原因，否则只能报
# 「沙箱进程异常退出」，运维看不出是回调卡死。
_WEDGE_CODE = "RESOURCE_TIMEOUT"


def _configured_interval() -> float:
    """心跳间隔；配置读不到就不心跳（父侧 stall 判定会因此更保守）。"""
    try:
        from app.config import settings

        return float(settings.SANDBOX_RUNNER_HEARTBEAT_SECONDS)
    except Exception:  # noqa: BLE001 — 配置导入绝不能拖垮 runner
        return 5.0


def _configured_wedge_grace() -> float:
    try:
        from app.config import settings

        return float(settings.SANDBOX_WEDGE_GRACE_SECONDS)
    except Exception:  # noqa: BLE001
        return DEFAULT_WEDGE_GRACE_SECONDS


class RunnerWriter:
    """JSONL stdout 写入器：整行原子化，可选后台心跳。

    所有 stdout 输出都必须经由 :meth:`emit`——包括 runner 自己的事件——否则
    锁形同虚设。
    """

    def __init__(self, interval: Optional[float] = None) -> None:
        self._lock = threading.Lock()
        self._interval = _configured_interval() if interval is None else interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def emit(self, kind: str, data: dict) -> None:
        line = json.dumps({"event": kind, **data}, ensure_ascii=False, default=str)
        with self._lock:
            sys.stdout.write(line[:_MAX_LINE] + "\n")
            sys.stdout.flush()

    # ── 心跳 ─────────────────────────────────────────────────────
    def start_heartbeat(self) -> None:
        if self._interval <= 0 or self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._beat, name="runner-heartbeat", daemon=True
        )
        self._thread.start()

    def stop_heartbeat(self) -> None:
        self._stop.set()

    def _beat(self) -> None:
        while not self._stop.wait(self._interval):
            self.emit(HEARTBEAT_EVENT, {"t": time.time()})


def start_wedge_reaper(
    writer: RunnerWriter,
    unresolved_seconds: Callable[[], float],
    grace: Optional[float] = None,
) -> None:
    """熔断投递不进去时的最后手段：结束整个 runner 进程。

    看门狗投递的是**异步异常**，只有回到字节码边界才生效。回调卡在不返回的
    C 调用里时（灾难性回溯的 ``re`` 是典型），熔断会一直悬着——异常投不进去，
    回调也返回不了。此时进程已从内部失去自救能力，唯一正确的做法是把它结束掉，
    由父进程记失败；否则实盘会带着一个再也不回调的策略无限期挂着。

    这里刻意放在 runner 而不是 engine：engine 是纯库，可能跑在 pytest 进程里，
    ``os._exit`` 在那里等于把测试框架一起杀掉。

    与父侧 stall 判定互补——两者谁先拿到调度谁生效：回调霸占 GIL 时心跳线程
    也停摆，由父进程杀；GIL 有释放但回调不返回时心跳照常，由这里杀。
    """

    def _watch() -> None:
        while True:
            time.sleep(1.0)
            hung = unresolved_seconds()
            if hung <= 0:
                continue
            limit = _configured_wedge_grace() if grace is None else grace
            if hung <= limit:
                continue
            # 先把失败原因送出去，再硬退出——os._exit 不做任何 flush。
            writer.emit("fail", {
                "code": _WEDGE_CODE,
                "message": (
                    f"策略回调超出时限后 {hung:.0f}s 仍无法中断，"
                    f"判定为卡在不释放 GIL 的调用中，已强制结束沙箱进程"
                ),
                "traceback": "",
            })
            os._exit(EXIT_SAFETY)

    threading.Thread(target=_watch, name="wedge-reaper", daemon=True).start()
