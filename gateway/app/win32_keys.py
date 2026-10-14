"""Windows 键击注入：拨动 MT5 终端本体的「算法交易」按钮。

MT5 出于安全设计没有任何 API 能编程切换 AutoTrading（MQL5 与 Python 包都只能
读 ``terminal_info().trade_allowed``）。但网关与终端同机——可以：找到终端窗口
→ 前台化 → SendInput 注入快捷键 Ctrl+E（算法交易开关）→ 由调用方复读状态验证。

仅 Windows 可用；非 Windows 或注入失败由调用方兜底（提示手动 Ctrl+E）。
"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes

logger = logging.getLogger(__name__)

TITLE_PREFIX = "MetaTrader"  # 终端主窗口标题（"MetaTrader 5 - ..."）
EXE_HINT = "terminal64.exe"

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_E = 0x45


def _user32() -> ctypes.WinDLL | None:
    try:
        return ctypes.windll.user32
    except AttributeError:  # 非 Windows
        return None


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("padding", ctypes.c_ubyte * 32)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _key(vk: int, up: bool = False) -> _INPUT:
    return _INPUT(
        type=INPUT_KEYBOARD,
        ki=_KEYBDINPUT(wVk=vk, dwFlags=KEYEVENTF_KEYUP if up else 0),
    )


def find_terminal_window() -> int | None:
    """找 MT5 终端主窗口句柄。

    标题不可靠（登录后是「{login} - {server}: ...」而非 "MetaTrader" 前缀，
    且随语言/券商变化）→ 以进程消歧为主：优先 settings.MT5_PATH 指定的
    终端实例（多开时 Python API 连的就是它），其次任意 terminal64.exe，
    最后才退回标题匹配。
    """
    user32 = _user32()
    if user32 is None:
        return None

    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    visible: list[tuple[int, int, str]] = []  # (hwnd, pid, title)

    def _cb(hwnd: int, _lparam: int) -> bool:
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                pid = wintypes.DWORD(0)
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                visible.append((hwnd, pid.value, buf.value))
        return True

    user32.EnumWindows(proto(_cb), 0)
    if not visible:
        return None

    import os

    import psutil

    from app.config import settings

    def _exe_of(pid: int) -> str | None:
        try:
            return os.path.normcase(psutil.Process(pid).exe())
        except Exception:  # noqa: BLE001 — 进程可能已退出
            return None

    def _pick(match) -> int | None:
        for hwnd, pid, _title in visible:
            exe = _exe_of(pid)
            if exe and match(exe):
                return hwnd
        return None

    target = os.path.normcase(str(settings.MT5_PATH)) if settings.MT5_PATH else None
    if target:
        hwnd = _pick(lambda exe: exe == target)
        if hwnd:
            return hwnd
    hwnd = _pick(lambda exe: exe.endswith(EXE_HINT))
    if hwnd:
        return hwnd
    # 兜底：标题含 "MetaTrader"（未登录时终端标题才有此前缀）
    for hwnd, _pid, title in visible:
        if TITLE_PREFIX.lower() in title.lower():
            return hwnd
    return None


SW_RESTORE = 9
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
VK_MENU = 0x12


def _force_foreground(hwnd: int) -> bool:
    """把终端窗口强制拉到前台并拿到键盘焦点。

    Windows 前台锁：非前台进程调 SetForegroundWindow 会被静默拒绝 →
    多招连用（SwitchToThisWindow / 最小化恢复 / ALT 键解锁 + AttachThreadInput /
    TOPMOST 闪烁），每招后验证 GetForegroundWindow，成功即止。
    """
    user32 = _user32()
    if user32 is None:
        return False
    kernel32 = ctypes.windll.kernel32

    def _fg() -> bool:
        return user32.GetForegroundWindow() == hwnd

    if _fg():
        return True

    # 招 1：SwitchToThisWindow（专为切换设计，带 ALT 解锁语义）
    user32.SwitchToThisWindow(hwnd, True)
    time.sleep(0.08)
    if _fg():
        return True

    # 招 2：最小化 → 恢复（恢复窗口会成为前台）
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.08)
        if _fg():
            return True

    # 招 3：ALT 键击解锁前台锁 + AttachThreadInput 借权限 + SetForegroundWindow
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    cur_tid = kernel32.GetCurrentThreadId()
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    tgt_tid = user32.GetWindowThreadProcessId(hwnd, None)
    attached: list[tuple[int, int]] = []
    if fg_tid and fg_tid != cur_tid:
        attached.append((cur_tid, fg_tid))
    if tgt_tid and tgt_tid != cur_tid:
        attached.append((cur_tid, tgt_tid))
    for a, b in attached:
        user32.AttachThreadInput(a, b, True)
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetActiveWindow(hwnd)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.08)
        if _fg():
            return True
        # 招 4：TOPMOST 闪烁（置顶再取消，常能把窗口顶到前台）
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
        user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
        time.sleep(0.08)
        return _fg()
    finally:
        for a, b in attached:
            user32.AttachThreadInput(a, b, False)


def send_ctrl_e(hwnd: int) -> None:
    """前台化终端窗口并注入 Ctrl+E（算法交易开关快捷键）。

    前台化失败 → OSError（调用方如实报错，注入给没焦点的窗口必被吞掉）。
    """
    user32 = _user32()
    if user32 is None or not hwnd:
        raise OSError("win32 键击注入仅在 Windows 可用")

    if not _force_foreground(hwnd):
        fg = user32.GetForegroundWindow()
        raise OSError(
            f"终端窗口无法获得前台焦点（当前前台 hwnd={fg:#x}）— Ctrl+E 需要终端在前台"
        )

    time.sleep(0.1)  # 等终端内部设置键盘焦点
    seq = (_INPUT * 4)(
        _key(VK_CONTROL), _key(VK_E), _key(VK_E, up=True), _key(VK_CONTROL, up=True)
    )
    sent = user32.SendInput(4, seq, ctypes.sizeof(_INPUT))
    if sent != 4:
        raise OSError(f"SendInput 仅注入 {sent}/4 个键击")
    logger.info("Ctrl+E injected into terminal window %#x", hwnd)
