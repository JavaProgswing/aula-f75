"""
Mouse input injection via the Aula F75 wireless dongle.

The dongle (VID=0x3554 PID=0xFA09) exposes a composite HID device that
includes a Mouse interface (usage_page=0x0001, usage=0x0002). On Windows,
mouhid.sys claims this interface exclusively — direct HID writes are blocked.

Instead, we inject via Windows SendInput(), which feeds into the same HID
input stack the dongle's mouse interface uses. From the OS perspective the
inputs are indistinguishable from physical mouse hardware.

Usage:
    python mouse.py move 100 50          # move cursor +100x +50y
    python mouse.py click left           # left click
    python mouse.py click right          # right click
    python mouse.py scroll 3             # scroll up 3 ticks
    python mouse.py drag 200 0           # left-hold + move + release

Python API:
    from mouse import MouseController
    m = MouseController()
    m.move(100, 50)
    m.click_left()
    m.move_smooth(500, 300, steps=30)
"""

import ctypes
import ctypes.wintypes
import time
import sys

# Windows INPUT structure constants
INPUT_MOUSE    = 0
MOUSEEVENTF_MOVE        = 0x0001
MOUSEEVENTF_LEFTDOWN    = 0x0002
MOUSEEVENTF_LEFTUP      = 0x0004
MOUSEEVENTF_RIGHTDOWN   = 0x0008
MOUSEEVENTF_RIGHTUP     = 0x0010
MOUSEEVENTF_MIDDLEDOWN  = 0x0020
MOUSEEVENTF_MIDDLEUP    = 0x0040
MOUSEEVENTF_WHEEL       = 0x0800
MOUSEEVENTF_ABSOLUTE    = 0x8000
WHEEL_DELTA             = 120


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx",          ctypes.wintypes.LONG),
        ("dy",          ctypes.wintypes.LONG),
        ("mouseData",   ctypes.wintypes.DWORD),
        ("dwFlags",     ctypes.wintypes.DWORD),
        ("time",        ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.wintypes.DWORD),
        ("_input", _INPUT_UNION),
    ]


def _send_mouse_input(flags: int, dx: int = 0, dy: int = 0, data: int = 0):
    inp = INPUT(type=INPUT_MOUSE)
    inp._input.mi = MOUSEINPUT(
        dx=dx, dy=dy,
        mouseData=data,
        dwFlags=flags,
        time=0,
        dwExtraInfo=None,
    )
    n = ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    if n != 1:
        raise RuntimeError(f"SendInput failed: {ctypes.GetLastError()}")


class MouseController:
    """
    Injects mouse events via Windows SendInput (same HID input stack as the
    F75 dongle's physical mouse interface).
    """

    def move(self, dx: int, dy: int):
        """Relative move by (dx, dy) pixels."""
        _send_mouse_input(MOUSEEVENTF_MOVE, dx=dx, dy=dy)

    def move_to(self, x: int, y: int):
        """Absolute move to screen coordinate (x, y)."""
        # Normalize to 0-65535 range required by MOUSEEVENTF_ABSOLUTE
        sw = ctypes.windll.user32.GetSystemMetrics(0)  # screen width
        sh = ctypes.windll.user32.GetSystemMetrics(1)  # screen height
        ax = int(x * 65535 / sw)
        ay = int(y * 65535 / sh)
        _send_mouse_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, dx=ax, dy=ay)

    def move_smooth(self, dx: int, dy: int, steps: int = 20, delay: float = 0.01):
        """Smooth relative move split into `steps` micro-movements."""
        for i in range(steps):
            sx = round(dx * (i + 1) / steps) - round(dx * i / steps)
            sy = round(dy * (i + 1) / steps) - round(dy * i / steps)
            if sx or sy:
                self.move(sx, sy)
            time.sleep(delay)

    def click_left(self, double: bool = False):
        """Left click (or double-click)."""
        _send_mouse_input(MOUSEEVENTF_LEFTDOWN)
        _send_mouse_input(MOUSEEVENTF_LEFTUP)
        if double:
            time.sleep(0.05)
            _send_mouse_input(MOUSEEVENTF_LEFTDOWN)
            _send_mouse_input(MOUSEEVENTF_LEFTUP)

    def click_right(self):
        """Right click."""
        _send_mouse_input(MOUSEEVENTF_RIGHTDOWN)
        _send_mouse_input(MOUSEEVENTF_RIGHTUP)

    def click_middle(self):
        """Middle click."""
        _send_mouse_input(MOUSEEVENTF_MIDDLEDOWN)
        _send_mouse_input(MOUSEEVENTF_MIDDLEUP)

    def down_left(self):
        """Hold left button down."""
        _send_mouse_input(MOUSEEVENTF_LEFTDOWN)

    def up_left(self):
        """Release left button."""
        _send_mouse_input(MOUSEEVENTF_LEFTUP)

    def drag(self, dx: int, dy: int, steps: int = 20, delay: float = 0.01):
        """Left-drag: hold, move, release."""
        self.down_left()
        time.sleep(0.05)
        self.move_smooth(dx, dy, steps=steps, delay=delay)
        time.sleep(0.05)
        self.up_left()

    def scroll(self, ticks: int):
        """
        Scroll wheel. Positive = up, negative = down.
        1 tick = WHEEL_DELTA (120) = one detent on most mice.
        """
        _send_mouse_input(MOUSEEVENTF_WHEEL, data=ticks * WHEEL_DELTA)

    def get_pos(self) -> tuple[int, int]:
        """Return current cursor position (x, y)."""
        pt = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return (pt.x, pt.y)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    m = MouseController()

    def usage():
        print("Usage:")
        print("  python mouse.py move <dx> <dy>          # relative move")
        print("  python mouse.py moveto <x> <y>          # absolute move")
        print("  python mouse.py smooth <dx> <dy>        # smooth move")
        print("  python mouse.py click [left|right|mid]  # click")
        print("  python mouse.py dclick                  # double-click")
        print("  python mouse.py drag <dx> <dy>          # drag")
        print("  python mouse.py scroll <ticks>          # scroll (+up/-down)")
        print("  python mouse.py pos                     # print cursor pos")
        print("  python mouse.py demo                    # run demo")

    if len(sys.argv) < 2:
        usage()
        sys.exit(0)

    cmd = sys.argv[1].lower()

    if cmd == "move" and len(sys.argv) == 4:
        m.move(int(sys.argv[2]), int(sys.argv[3]))

    elif cmd == "moveto" and len(sys.argv) == 4:
        m.move_to(int(sys.argv[2]), int(sys.argv[3]))

    elif cmd == "smooth" and len(sys.argv) == 4:
        m.move_smooth(int(sys.argv[2]), int(sys.argv[3]))

    elif cmd == "click":
        btn = sys.argv[2].lower() if len(sys.argv) > 2 else "left"
        if btn == "left":
            m.click_left()
        elif btn == "right":
            m.click_right()
        elif btn in ("mid", "middle"):
            m.click_middle()

    elif cmd == "dclick":
        m.click_left(double=True)

    elif cmd == "drag" and len(sys.argv) == 4:
        m.drag(int(sys.argv[2]), int(sys.argv[3]))

    elif cmd == "scroll" and len(sys.argv) == 3:
        m.scroll(int(sys.argv[2]))

    elif cmd == "pos":
        x, y = m.get_pos()
        print(f"{x} {y}")

    elif cmd == "demo":
        print("Demo: move right 200, click, scroll up 3, drag left 100")
        time.sleep(1)
        m.move_smooth(200, 0)
        time.sleep(0.2)
        m.click_left()
        time.sleep(0.2)
        m.scroll(3)
        time.sleep(0.2)
        m.drag(-100, 0)
        print("Done.")

    else:
        usage()
