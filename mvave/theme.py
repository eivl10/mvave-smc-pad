"""Палитра и шрифты интерфейса — в одном месте.

Цвета заданы явно, а не через тему CustomTkinter: часть виджетов остаётся
обычным Tk (метки, которым код меняет цвет на ходу, схема устройства),
и им нужен тот же набор значений, что и CTk-виджетам рядом.
"""
from tkinter import ttk

BG = "#0f1115"          # окно
SURFACE = "#171a21"     # карточки: схема, шапка, панель справа
SURFACE_2 = "#1f232c"   # поля, список, неактивные кнопки
SURFACE_3 = "#2a2f3a"   # наведение, рамки
BORDER = "#2a2f3a"

TEXT = "#e6e8ee"
MUTED = "#8b93a7"
DIM = "#5c6475"

ACCENT = "#4f8cff"
ACCENT_HOVER = "#3d78ea"
OK = "#22c55e"
OK_DARK = "#1c3b2a"
WARN = "#f59e0b"
DANGER = "#ef4444"
DANGER_DARK = "#3a1d22"

FONT = "Segoe UI"
F_BODY = (FONT, 10)
F_SMALL = (FONT, 9)
F_TINY = (FONT, 8)
F_BOLD = (FONT, 10, "bold")
F_TITLE = (FONT, 15, "bold")
F_MONO = ("Consolas", 10)

# CustomTkinter меряет шрифт в пикселях, Tk — в пунктах (9 pt ≈ 12 px).
# С одним и тем же кортежем CTk-кнопки выходили заметно мельче соседних
# меток — отдельные размеры для CTk-виджетов.
C_SMALL = (FONT, 13)
C_TINY = (FONT, 11)
C_MONO = ("Consolas", 13)

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
              foreground=[("selected", "#ffffff")])
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    style.configure("TCombobox", fieldbackground="white", background="#eee",
                    foreground="black", bordercolor="#aaa")
