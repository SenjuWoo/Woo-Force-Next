"""Keep Ikemen test windows off the user's desktop.

park(proc) waits for the process's first visible window and moves it past the left edge of all monitors,
without activating it: the game renders and screenshots as usual but never covers the user's work.
Ikemen has no window-position setting (system_sdl.go CreateWindow). Launch with no_focus_env() so the
window does not take focus in the ~0.15 s before it is moved.
"""
import os
import threading
import time


def no_focus_env():
    return dict(os.environ, SDL_WINDOW_NO_ACTIVATION_WHEN_SHOWN="1")  # SDL 2.0.22+ hint


def _park(proc):
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    hits = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def found(h, _):
        p = wintypes.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == proc.pid and u.IsWindowVisible(h):
            hits.append(h)
        return True

    x = u.GetSystemMetrics(76) - 4000  # SM_XVIRTUALSCREEN: left edge of all monitors
    for _ in range(1500):
        u.EnumWindows(found, 0)
        if hits or proc.poll() is not None:
            break
        time.sleep(0.02)
    for h in hits:
        u.SetWindowPos(h, 0, x, 0, 0, 0, 0x15)  # SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE


def park(proc):
    if os.name == "nt":
        threading.Thread(target=_park, args=(proc,), daemon=True).start()
