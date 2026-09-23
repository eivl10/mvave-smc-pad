"""Палитра и шрифты интерфейса — в одном месте.

Цвета заданы явно, а не через тему CustomTkinter: часть виджетов остаётся
обычным Tk (метки, которым код меняет цвет на ходу, схема устройства),
и им нужен тот же набор значений, что и CTk-виджетам рядом.

Две палитры. Значения взяты из принятых владельцем тем проекта AGY-sub
(gui-switcher.ps1): светлая там сделана отдельно, а не инверсией тёмной.
Константы модуля переписывает set_mode(); виджеты читают их при создании,
поэтому смена темы на ходу — пересборка окна (App.rebuild_ui).
"""
from tkinter import ttk

DARK = {
    "BG": "#1c1c2e",          # окно
    "SURFACE": "#252540",     # карточки: схема, шапка, панель справа
    "SURFACE_2": "#303050",   # поля, список, вторичные кнопки
    "SURFACE_3": "#3d3d60",   # наведение
    "BORDER": "#3e3e5c",      # рамка вторичной кнопки, разделители
    "ELEVATED": "#2e2e50",    # меню, диалоги, тосты — над карточками
    "HOVER": "#3d3d60",       # строка меню под мышью
    "SEG_ON": "#7c5cfc",      # выбранный сегмент переключателя
    "SEG_ON_HOVER": "#9b82ff",
    "SEG_TEXT": "#ececf0",
    "TEXT": "#ececf0",
    "MUTED": "#8888a8",
    "DIM": "#5c5c78",
    "ACCENT": "#7c5cfc",
    "ACCENT_HOVER": "#9b82ff",
    "ON_ACCENT": "#ffffff",
    "OK": "#4ade80",
    "OK_DARK": "#1f3a2c",
    "WARN": "#f59e0b",
    "DANGER": "#b4544a",      # терракота: красный приглушён намеренно
    "DANGER_HOVER": "#a85446",
    "DANGER_DARK": "#33201c",
    # схема устройства
    "KNOB_BODY": "#1c1c2e",
    "KNOB_RING": "#4a4a68",
    "KNOB_TICK": "#ececf0",
    "BTN_BG": "#303050",      # рабочие кнопки справа
    "BTN_ICON": "#c9bcff",
    "FW_BG": "#212138",       # кнопки, которые обрабатывает прошивка
    "FW_TEXT": "#5c5c78",
    "SHADOW": "#000000",
}

LIGHT = {
    "BG": "#e4e4f2",
    "SURFACE": "#f6f6fd",
    "SURFACE_2": "#e8e8f8",
    "SURFACE_3": "#d8d8f0",
    "BORDER": "#b0b0d0",
    "ELEVATED": "#ffffff",
    "HOVER": "#ececfa",
    # в светлой теме выбранный сегмент — белая «таблетка» с тёмным текстом:
    # у CTkSegmentedButton один цвет текста, на фиолетовом он бы потерялся
    "SEG_ON": "#ffffff",
    "SEG_ON_HOVER": "#ffffff",
    "SEG_TEXT": "#1a1a2e",
    "TEXT": "#1a1a2e",
    "MUTED": "#6060a0",
    "DIM": "#9090b8",
    "ACCENT": "#6040e0",
    "ACCENT_HOVER": "#7c5cfc",
    "ON_ACCENT": "#ffffff",
    "OK": "#16a34a",
    "OK_DARK": "#d6f0df",
    "WARN": "#d97706",
    "DANGER": "#a24a3e",
    "DANGER_HOVER": "#9a4a3c",
    "DANGER_DARK": "#f0dbd5",
    "KNOB_BODY": "#ececf8",
    "KNOB_RING": "#a0a0c8",
    "KNOB_TICK": "#1a1a2e",
    "BTN_BG": "#e2e2f6",
    "BTN_ICON": "#6040e0",
    "FW_BG": "#ececf5",
    "FW_TEXT": "#a8a8c4",
    "SHADOW": "#2a2a55",
}

MODES = ("system", "dark", "light")
MODE_NAMES = {"system": "Как в Windows", "dark": "Тёмная", "light": "Светлая"}

mode = "dark"        # то, что выбрал пользователь (может быть system)
applied = "dark"     # то, что реально нарисовано: dark или light


def _system_is_light():
    """Тема приложений Windows. Только чтение реестра."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 1
    except OSError:
        return False


def resolve(m):
    if m == "system":
        return "light" if _system_is_light() else "dark"
    return "light" if m == "light" else "dark"


def set_mode(m):
    """Переписать константы модуля под тему. Возвращает dark/light."""
    global mode, applied
    mode = m if m in MODES else "dark"
    applied = resolve(mode)
    globals().update(LIGHT if applied == "light" else DARK)
    return applied


set_mode("dark")

FONT = "Segoe UI"
ICON_FONT = "Segoe MDL2 Assets"   # значки: в Segoe UI части символов нет
F_BODY = (FONT, 10)
F_SMALL = (FONT, 9)
F_TINY = (FONT, 8)
F_BOLD = (FONT, 10, "bold")
F_TITLE = (FONT, 15, "bold")
F_MONO = ("Consolas", 10)

# CustomTkinter меряет шрифт в пикселях, Tk — в пунктах (9 pt ≈ 12 px).
# С одним и тем же кортежем CTk-кнопки выходили заметно мельче соседних
# меток — отдельные размеры для CTk-виджетов.
C_BODY = (FONT, 14)
C_SMALL = (FONT, 13)
C_TINY = (FONT, 11)
C_BOLD = (FONT, 14, "bold")
C_TITLE = (FONT, 17, "bold")
C_MONO = ("Consolas", 13)
C_ICON = (ICON_FONT, 13)

RADIUS = 12
RADIUS_SM = 8


def apply_ttk(root):
    """Дерево действий — ttk.Treeview: у CTk аналога нет, красим под тему."""
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("Treeview", background=SURFACE_2, fieldbackground=SURFACE_2,
                    foreground=TEXT, rowheight=26, borderwidth=0, relief="flat",
                    font=F_SMALL)
    style.map("Treeview",
              background=[("selected", ACCENT)],
              foreground=[("selected", ON_ACCENT)])
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])


def paint_titlebar(root):
    """Системный заголовок окна — под тему (Windows 10 1809+ / 11).

    Без этого в светлой теме над окном висит чёрная полоса, в тёмной —
    белая. Атрибут 20 = DWMWA_USE_IMMERSIVE_DARK_MODE. Сбой не критичен.
    """
    try:
        import ctypes
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
        val = ctypes.c_int(1 if applied == "dark" else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 20, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        pass
