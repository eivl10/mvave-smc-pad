# -*- coding: utf-8 -*-
"""Свой индикатор громкости в стиле системного флайаута Windows 11.

Зачем он вообще нужен. Громкость крутилкой пишется прямо в микшер через
pycaw — так ушли «ступеньки», когда Windows терял часть нажатий медиа-клавиш
(см. FIXES.md, 2026-09-19). Но системный флайаут рисует оболочка Windows в
ответ на нажатие клавиши, а не на изменение уровня: прямая запись его не
вызывает, и управлять этим из программы нельзя. Значит индикатор либо свой,
либо назад к клавишам вместе с их багом. Выбран свой.

Геометрия снята с настоящего флайаута на экране владельца (2560x1440,
масштаб 100%) и вынесена в константы ниже — подгонять их, а не код.
"""

import ctypes
import ctypes.wintypes as wt
import tkinter as tk

try:
    import winreg
except ImportError:              # не Windows — модуль просто не заработает
    winreg = None

# ── Геометрия эталона ─────────────────────────────────────────────────────────
PLATE_W, PLATE_H = 190, 47       # размер плашки
GAP_ABOVE_TASKBAR = 14           # зазор между плашкой и панелью задач
ICON_CX = 22                     # центр иконки от левого края плашки
BAR_X0, BAR_X1 = 40, 152         # полоска уровня
BAR_H = 4
NUM_CX = 170                     # центр числа
HOLD_MS = 1800                   # сколько висит до начала гашения
FADE_STEPS = 12                  # ступеней затухания
FADE_STEP_MS = 30

# Замерено на эталоне: остаток полоски #767f80, заполнение #1d6978.
# Заполнение — не сам акцент (#459bac), а оттенок из палитры акцента:
# Windows держит в реестре восемь ступеней и под светлую тему берёт тёмную,
# под тёмную — светлую. Простое затемнение акцента сюда не попадает.
BAR_REST = "#7c8486"
ACCENT_SHADE_LIGHT = 4
ACCENT_SHADE_DARK = 2
# Плашка непрозрачная. Системная — акриловая и подхватывает цвет обоев, но
# Tk умеет делать полупрозрачным только всё окно целиком: вместе с фоном
# бледнели бы полоска и цифры, а их цвета замерены с эталона точно. Точность
# элементов важнее оттенка подложки.
BASE_ALPHA = 1.0
LIGHT = {"fg": "#1a1a1a", "plate": "#f3f3f3"}
DARK = {"fg": "#ffffff", "plate": "#202020"}

# Иконки — системный шрифт значков, чтобы динамик был ровно тот же.
ICON_FONTS = ("Segoe Fluent Icons", "Segoe MDL2 Assets")
ICON_MUTE = ""
ICON_LEVELS = ("", "", "", "")   # тихо → громко

# Ключевой цвет прозрачности: нужен только запасному пути отрисовки, когда
# системное скругление окна недоступно и углы плашки рисуются вручную.
CHROMA = "#ff00ff"

_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWCP_ROUND = 2


def _reg_dword(root, path, name):
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(root, path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def _is_light_theme():
    if winreg is None:
        return True
    v = _reg_dword(winreg.HKEY_CURRENT_USER,
                   r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                   "SystemUsesLightTheme")
    return bool(v) if v is not None else True


def _accent_shade(index):
    """Оттенок акцента из системной палитры: восемь ступеней по 4 байта RGBA."""
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent") as k:
            pal = winreg.QueryValueEx(k, "AccentPalette")[0]
    except OSError:
        return None
    off = index * 4
    if len(pal) < off + 3:
        return None
    return "#%02x%02x%02x" % (pal[off], pal[off + 1], pal[off + 2])


def _accent_color():
    """Запасной цвет заполнения, если палитры акцента в реестре нет."""
    if winreg is None:
        return "#0078d4"
    v = _reg_dword(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\DWM",
                   "ColorizationColor")
    if v is None:
        return "#0078d4"                      # системный синий по умолчанию
    return "#%02x%02x%02x" % ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)


def _current_colors():
    """(тема, цвет заполнения полоски) на текущий момент, а не на момент старта."""
    theme = LIGHT if _is_light_theme() else DARK
    shade = ACCENT_SHADE_LIGHT if theme is LIGHT else ACCENT_SHADE_DARK
    return theme, (_accent_shade(shade) or _accent_color())


def _taskbar_height():
    try:
        u = ctypes.windll.user32
        hwnd = u.FindWindowW("Shell_TrayWnd", None)
        if not hwnd:
            return 48
        r = wt.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        h = r.bottom - r.top
        return h if 0 < h < 200 else 48
    except Exception:
        return 48


def _pick_icon_font(widget):
    """Первый шрифт значков, который реально есть в системе."""
    try:
        import tkinter.font as tkfont
        have = {f.lower() for f in tkfont.families(widget)}
        for name in ICON_FONTS:
            if name.lower() in have:
                return name
    except Exception:
        pass
    return None


class VolumeOSD:
    """Плашка громкости. Живёт в потоке Tk, создаётся один раз при первом показе."""

    def __init__(self, master):
        self.master = master
        self._hide_job = None
        self._fade_job = None
        self._rounded_by_system = False
        self._visible = False
        self._geometry = None        # что уже выставлено, чтобы не дёргать зря

        self.theme, self.accent = _current_colors()

        self.win = tk.Toplevel(master)
        try:
            self.win.withdraw()
            self.win.overrideredirect(True)
            self.win.attributes("-topmost", True)
            self.win.attributes("-alpha", BASE_ALPHA)
            try:
                self.win.attributes("-toolwindow", True)   # не показывать в Alt+Tab
            except tk.TclError:
                pass

            self._place()
            # Углы скругляет сам Windows. Ключевой цвет прозрачности здесь не
            # годится: сглаженные края текста смешиваются с ним, и вокруг цифр
            # и значка проступает цветная кайма. Он остаётся запасным путём —
            # только если системное скругление не применилось.
            self._rounded_by_system = self._enable_rounding()

            self.canvas = tk.Canvas(self.win, width=PLATE_W, height=PLATE_H,
                                    highlightthickness=0, bd=0)
            self.canvas.pack()
            self.icon_font = _pick_icon_font(self.win)
            self._apply_theme()
        except BaseException:
            # Окно уже существует, а объект — ещё нет. Без этого недостроенный
            # Toplevel остался бы висеть, и следующее событие крутилки создало
            # бы ещё один: при тридцати событиях в секунду это лавина окон.
            try:
                self.win.destroy()
            except Exception:
                pass
            raise

    # ── Системные эффекты ────────────────────────────────────────────────────
    def _hwnd(self):
        self.win.update_idletasks()
        return ctypes.windll.user32.GetParent(self.win.winfo_id()) or self.win.winfo_id()

    def _enable_rounding(self):
        """Системное скругление углов окна. True, если Windows его применил.

        Вызов есть только с Windows 11; на более старых он вернёт ошибку, и
        тогда плашку со скруглением рисуем сами поверх прозрачного фона.
        """
        try:
            pref = ctypes.c_int(_DWMWCP_ROUND)
            hr = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                wt.HWND(self._hwnd()), _DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(pref), ctypes.sizeof(pref))
            return hr == 0
        except Exception:
            return False

    def _place(self):
        """Позиция считается перед каждым показом: экран и панель задач меняются."""
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        x = (sw - PLATE_W) // 2
        y = sh - _taskbar_height() - GAP_ABOVE_TASKBAR - PLATE_H
        geom = f"{PLATE_W}x{PLATE_H}+{x}+{y}"
        if geom != self._geometry:
            self.win.geometry(geom)
            self._geometry = geom

    # ── Отрисовка ────────────────────────────────────────────────────────────
    def _apply_theme(self):
        """Перекрасить плашку под текущую тему и акцент системы."""
        bg = self.theme["plate"] if self._rounded_by_system else CHROMA
        self.win.configure(bg=bg)
        if not self._rounded_by_system:
            self.win.attributes("-transparentcolor", CHROMA)
        self.canvas.configure(bg=bg)
        self._build()

    def _build(self):
        c = self.canvas
        c.delete("all")

        # Если Windows скруглил окно сам, фон уже нужного цвета. Иначе рисуем
        # плашку вручную: углы остаются ключевого цвета и потому прозрачны.
        if not self._rounded_by_system:
            self._rounded(0, 0, PLATE_W, PLATE_H, 10, self.theme["plate"])

        mid = PLATE_H // 2
        self._icon = c.create_text(
            ICON_CX, mid, text=ICON_LEVELS[-1], fill=self.theme["fg"],
            font=(self.icon_font, 12) if self.icon_font else ("Segoe UI", 12))

        self._bar_rest = self._bar(BAR_X0, BAR_X1, mid, BAR_REST)
        self._bar_fill = self._bar(BAR_X0, BAR_X1, mid, self.accent)

        self._num = c.create_text(NUM_CX, mid, text="0", fill=self.theme["fg"],
                                  font=("Segoe UI", 10))

    def _bar(self, x0, x1, cy, color):
        """Полоска со скруглёнными концами — как у системной."""
        r = BAR_H // 2
        return [
            self.canvas.create_rectangle(x0 + r, cy - r, x1 - r, cy + r,
                                         fill=color, outline=color),
            self.canvas.create_oval(x0, cy - r, x0 + BAR_H, cy + r,
                                    fill=color, outline=color),
            self.canvas.create_oval(x1 - BAR_H, cy - r, x1, cy + r,
                                    fill=color, outline=color),
        ]

    def _rounded(self, x0, y0, x1, y1, r, color):
        c = self.canvas
        c.create_rectangle(x0 + r, y0, x1 - r, y1, fill=color, outline=color)
        c.create_rectangle(x0, y0 + r, x1, y1 - r, fill=color, outline=color)
        for cx, cy in ((x0, y0), (x1 - 2 * r, y0), (x0, y1 - 2 * r), (x1 - 2 * r, y1 - 2 * r)):
            c.create_oval(cx, cy, cx + 2 * r, cy + 2 * r, fill=color, outline=color)

    def _set_level(self, level, muted=False):
        level = max(0, min(100, int(round(level))))
        mid = PLATE_H // 2
        r = BAR_H // 2
        filled = BAR_X0 + (BAR_X1 - BAR_X0) * level / 100.0

        rect, left, right = self._bar_fill
        # Полоска нулевой длины всё равно оставила бы круглый «огрызок»,
        # поэтому на нуле заполнение прячется целиком.
        if level <= 0:
            for item in self._bar_fill:
                self.canvas.itemconfigure(item, state="hidden")
        else:
            for item in self._bar_fill:
                self.canvas.itemconfigure(item, state="normal")
            self.canvas.coords(rect, BAR_X0 + r, mid - r, max(BAR_X0 + r, filled - r), mid + r)
            self.canvas.coords(left, BAR_X0, mid - r, BAR_X0 + BAR_H, mid + r)
            self.canvas.coords(right, max(BAR_X0, filled - BAR_H), mid - r,
                               max(BAR_X0 + BAR_H, filled), mid + r)

        if muted or level == 0:
            icon = ICON_MUTE
        else:
            # Четыре значка на диапазон 1..100 — по 25 единиц на ступень.
            # Делить на 34 нельзя: тогда самый тихий значок не достаётся
            # никакому уровню и градация молча вырождается в три ступени.
            icon = ICON_LEVELS[min(len(ICON_LEVELS) - 1, (level - 1) * len(ICON_LEVELS) // 100)]
        self.canvas.itemconfigure(self._icon, text=icon)
        self.canvas.itemconfigure(self._num, text=str(level))

    # ── Показ и гашение ──────────────────────────────────────────────────────
    def show(self, level, muted=False):
        for job in (self._hide_job, self._fade_job):
            if job:
                self.master.after_cancel(job)
        self._hide_job = self._fade_job = None

        theme, accent = _current_colors()
        if theme is not self.theme or accent != self.accent:
            self.theme, self.accent = theme, accent
            self._apply_theme()

        self._place()
        self._set_level(level, muted)

        # Поднимать окно на каждое событие незачем: крутилка шлёт их десятками
        # в секунду, а z-порядок пересчитывается оконным менеджером.
        if not self._visible:
            self.win.attributes("-alpha", BASE_ALPHA)
            self.win.deiconify()
            self.win.lift()
            self.win.attributes("-topmost", True)
            self._visible = True
        else:
            self.win.attributes("-alpha", BASE_ALPHA)

        self._hide_job = self.master.after(HOLD_MS, lambda: self._fade(FADE_STEPS))

    def _fade(self, left):
        self._hide_job = None
        if left <= 0:
            self.win.withdraw()
            self._visible = False
            self._fade_job = None
            return
        self.win.attributes("-alpha", BASE_ALPHA * left / FADE_STEPS)
        self._fade_job = self.master.after(FADE_STEP_MS, lambda: self._fade(left - 1))
