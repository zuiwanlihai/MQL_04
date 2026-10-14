"""测试替身：一个永远不产出任何 stdout 的 runner。

用来验证父侧 stall 判定确实会杀掉「连心跳都没有」的子进程。真实的 paper/live
runner 心跳正常时走不到这条路径，所以只能用一个沉默的假 runner 来复现。
"""

from __future__ import annotations

import argparse
import time


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="silent_runner")
    parser.add_argument("--payload", required=True, help="payload directory")
    parser.parse_args(argv)
    while True:  # 既不输出也不退出，就像卡死在回调里
        time.sleep(3600)


if __name__ == "__main__":
    raise SystemExit(main())
