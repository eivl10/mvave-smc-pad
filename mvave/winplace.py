"""Место окна на экране: рабочая область монитора и проверка сохранённой позиции.

Координаты — те же, что видит Tk в этом процессе (он не объявляет себя
DPI-aware, Windows отдаёт ему логические пиксели), поэтому пересчёта нет.
"""
import ctypes
from ctypes import wintypes

_MONITOR_DEFAULTTONULL = 0
_MONITOR_DEFAULTTONEAREST = 2


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def _user32():
    u = ctypes.windll.user32
    u.MonitorFromRect.restype = wintypes.HMONITOR
    u.MonitorFromRect.argtypes = [ctypes.POINTER(wintypes.RECT), wintypes.DWORD]
    u.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MONITORINFO)]
    return u


def work_area(x=0, y=0, w=1, h=1):
    """(left, top, width, height) рабочей области монитора под прямоугольником
    (без панели задач). Сбой API — None."""
    try:
        u = _user32()
        r = wintypes.RECT(x, y, x + w, y + h)
        mon = u.MonitorFromRect(ctypes.byref(r), _MONITOR_DEFAULTTONEAREST)
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(mi)
        if not u.GetMonitorInfoW(mon, ctypes.byref(mi)):
            return None
        a = mi.rcWork
        return a.left, a.top, a.right - a.left, a.bottom - a.top
    except Exception:
        return None


def on_screen(x, y, w, h):
    """Заголовок окна попадает на какой-либо монитор: за него можно схватить.

    Проверяется полоса заголовка, а не всё окно — окно, наполовину
    уехавшее за край, ещё можно вытащить, а без заголовка уже нет.
    """
    try:
        u = _user32()
        r = wintypes.RECT(x + 40, y, x + max(w - 40, 41), y + 30)
        return bool(u.MonitorFromRect(ctypes.byref(r), _MONITOR_DEFAULTTONULL))
    except Exception:
        return True


def parse(geom):
    """'WxH+X+Y' → (w, h, x, y) или None. Допускает минусовые координаты."""
    import re
    m = re.fullmatch(r"(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)", str(geom or ""))
    if not m:
        return None
    w, h = int(m.group(1)), int(m.group(2))
    x = int(m.group(3).replace("+-", "-").lstrip("+"))
    y = int(m.group(4).replace("+-", "-").lstrip("+"))
    return w, h, x, y
