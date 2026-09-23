import tkinter as tk
from tkinter import ttk, messagebox, filedialog, colorchooser
import threading
import math
import queue
import time
import traceback
import os

from mvave import ble
from mvave import actions
from mvave import appconfig
from mvave import volume_osd
from mvave import tray
from mvave import theme

import customtkinter as ctk

# ── Bank map: bank number → MIDI note base for PAD 1 ─────────────────────────
# Bank 3 is the factory default (notes 36-51).
# Banks 4 and 8 send the same notes (52-67); by MIDI they are indistinguishable,
# we report the lower number.  Assignments are shared across all banks.
BANK_NOTE_BASE = {1: 4, 2: 20, 3: 36, 4: 52, 5: 68, 6: 84, 7: 100, 8: 52}
# Reverse: note → (pad_num, bank).  Built once at import time.
_NOTE_TO_PAD = {}
for _bank, _base in sorted(BANK_NOTE_BASE.items()):
    for _i in range(16):
        _note = _base + _i
        if _note not in _NOTE_TO_PAD:          # first bank wins (4 before 8)
            _NOTE_TO_PAD[_note] = (_i + 1, _bank)

# Knob CC assignments: device sends CC 30-37 (bank 1) and CC 38-45 (bank 2).
# Both banks map to the same 8 knobs.
KNOB_CC_MAP = {
    30: 1, 31: 2, 32: 3, 33: 4, 34: 5, 35: 6, 36: 7, 37: 8,
    38: 1, 39: 2, 40: 3, 41: 4, 42: 5, 43: 6, 44: 7, 45: 8,
}
CC_TO_KNOB = KNOB_CC_MAP

# ── Color presets for the pad palette ─────────────────────────────────────────
COLOR_PRESETS = [
    "#ff0000", "#ff8800", "#ffff00", "#00ff00", "#00ffff",
    "#0088ff", "#0000ff", "#8800ff", "#ff00ff", "#ffffff",
    "#000000",   # погасить пэд: цвет есть у всех, «нет цвета» больше не бывает
]

# Пэд без своего цвета всё равно должен светиться и слушаться общего фейдера:
# иначе «яркость всех пэдов» касалась бы только раскрашенных вручную.
# Определение одно, в транспорте: экран обязан показывать то же, что уходит
# на железо, а два независимых значения рано или поздно разъедутся.
DEFAULT_PAD_COLOR = ble.DEFAULT_PAD_COLOR

def _text_color_for_bg(hex_col):
    """Return '#000' or '#fff' based on perceived luminance of background."""
    if not hex_col or not hex_col.startswith("#") or len(hex_col) < 7:
        return "#ffffff"
    try:
        r = int(hex_col[1:3], 16)
        g = int(hex_col[3:5], 16)
        b = int(hex_col[5:7], 16)
    except ValueError:
        return "#ffffff"
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    return "#000000" if lum > 140 else "#ffffff"

def _apply_brightness(hex_col, pct):
    """Scale RGB components by pct (10-100).  Returns '#rrggbb'."""
    if not hex_col or not hex_col.startswith("#") or len(hex_col) < 7:
        return hex_col
    try:
        r = int(hex_col[1:3], 16)
        g = int(hex_col[3:5], 16)
        b = int(hex_col[5:7], 16)
    except ValueError:
        return hex_col
    # Ноль обязан давать ноль: общий фейдер должен уметь погасить пэды совсем.
    f = max(0.0, min(1.0, pct / 100.0))
    return f"#{int(r*f):02x}{int(g*f):02x}{int(b*f):02x}"

def _initial_param(act):
    """Начальное значение параметра, пригодное для записи в JSON.

    default_param у Action работает на две роли: у действий-пресетов это
    фиксированный хоткей (в том числе объект pynput Key), у пользовательских —
    стартовое значение редактируемого поля. В конфиг имеет право попасть только
    второе: Key не сериализуется, и запись его в binding роняет save_config
    целиком, то есть теряет ВСЕ настройки, а не только эту.
    """
    if not act or not act.param_kind:
        return None
    dp = act.default_param
    return dp if isinstance(dp, (str, int, float, bool)) else None


def _side(binding, slot):
    """Сторона крутилки в режиме «влево/вправо» — всегда СЛОВАРЬ.

    Хранить сторону простой строкой-id нельзя: тогда ей некуда положить
    параметр, и «Запустить программу» или «Своя комбинация» на сторону
    назначаются, но настроить их нечем. Общее поле param на всю крутилку
    тоже не годится — две стороны затирали бы параметр друг друга.

    Читает и старую строковую форму, чтобы уже сохранённый конфиг не падал.
    """
    v = (binding or {}).get(slot)
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v:
        return {"action": v, "param": None}
    return {}


_HOTKEY_NAMES = {
    "ctrl": "Ctrl", "shift": "Shift", "alt": "Alt", "win": "Win",
    "return": "Enter", "escape": "Esc", "esc": "Esc", "tab": "Tab", "end": "End",
    "print_screen": "PrtSc","space": "Space", "backspace": "Bksp",
    "delete": "Del", "page_up": "PgUp", "page_down": "PgDn", "prior": "PgUp",
    "next": "PgDn", "insert": "Ins", "print": "PrtSc", "up": "↑", "down": "↓",
    "left": "←", "right": "→", "minus": "-", "equal": "=", "comma": ",",
    "period": ".", "slash": "/", "backslash": "\\", "semicolon": ";",
    "apostrophe": "'", "grave": "`", "bracketleft": "[", "bracketright": "]",
}


def _format_hotkey(s):
    """'ctrl+shift+s' → 'Ctrl+Shift+S' — так, как комбинацию пишут люди."""
    parts = [p for p in str(s).split("+") if p]
    return "+".join(_HOTKEY_NAMES.get(p, p.upper() if len(p) <= 3 else p.capitalize())
                    for p in parts)


_MODIFIER_KEYSYMS = {"control_l", "control_r", "shift_l", "shift_r",
                     "alt_l", "alt_r", "win_l", "win_r", "super_l", "super_r",
                     "meta_l", "meta_r", "caps_lock", "num_lock"}

# Знаки на OEM-клавишах — по виртуальному коду: keysym в русской раскладке
# отдаёт кириллицу («Cyrillic_be» вместо запятой), а её pynput не нажмёт.
_OEM_VK = {0xBA: "semicolon", 0xBB: "equal", 0xBC: "comma", 0xBD: "minus",
           0xBE: "period", 0xBF: "slash", 0xC0: "grave", 0xDB: "bracketleft",
           0xDC: "backslash", 0xDD: "bracketright", 0xDE: "apostrophe"}


def _held_modifiers():
    """Зажатые модификаторы — у Windows напрямую, не из event.state.

    В state у Tk на Windows бит 0x2 — это Caps Lock, а не Alt (при
    включённом Caps к любой комбинации приписывался alt), а Win там нет вовсе.
    """
    try:
        import ctypes
        # GetKeyState, не GetAsyncKeyState: первое — состояние на момент
        # обрабатываемого сообщения, второе — «сейчас», и быстро отпущенный
        # Ctrl к обработке события уже терялся.
        gks = ctypes.windll.user32.GetKeyState
        down = lambda vk: bool(gks(vk) & 0x8000)
        mods = []
        if down(0x11): mods.append("ctrl")
        if down(0x10): mods.append("shift")
        if down(0x12): mods.append("alt")
        if down(0x5B) or down(0x5C): mods.append("win")
        return mods
    except Exception:
        return []


def _key_from_event(e):
    """Основная клавиша события в форме, которую понимает actions._hotkey.

    None — если нажат только модификатор. Буквы и цифры берутся по коду
    клавиши, чтобы запись не зависела от раскладки: Ctrl+C в русской
    раскладке иначе записывался как «ctrl+cyrillic_es».
    """
    keysym = (e.keysym or "").lower()
    if keysym in _MODIFIER_KEYSYMS:
        return None
    vk = getattr(e, "keycode", 0) or 0
    if 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39:
        return chr(vk).lower()
    if vk in _OEM_VK:
        return _OEM_VK[vk]
    return {"prior": "page_up", "next": "page_down", "print": "print_screen",
            "escape": "esc"}.get(keysym, keysym)


def _ellipsize(text, n):
    """Обрезать с «…»: подпись, срезанная краем рамки, читается как другая."""
    return text if len(text) <= n else text[:n - 1] + "…"


def _element_display_text(binding):
    """Build short label for a pad/knob/button from its binding dict."""
    if not binding:
        return ""
    action_id = binding.get("action", "none")
    if action_id == "none":
        legacy = binding.get("legacy_action")
        return "?" if legacy else ""
    act = actions.get(action_id)
    if not act:
        return "?"
    short = act.short
    param = binding.get("param")
    if param and act.param_kind == "hotkey":
        # Вместо слова «Хоткей» — сама комбинация: слово не говорит,
        # что нажмётся. Длинную ломаем перед последней клавишей, чтобы
        # влезла в пэд 75 px.
        combo = _format_hotkey(param)
        if len(combo) > 10 and "+" in combo:
            head, key = combo.rsplit("+", 1)
            combo = f"{head}+\n{key}"
        return combo
    if param and act.param_kind in ("exe", "folder"):
        basename = os.path.basename(str(param))
        if basename:
            short = f"{short}\n{basename[:12]}"
    elif param and act.param_kind in ("text",):
        short = f"{short}\n{str(param)[:10]}"
    elif param and act.param_kind == "url":
        short = f"{short}\n{str(param)[:10]}"
    return short

def on_closing(root):
    os._exit(0)


# ── Clipboard provider for actions.text.type ──────────────────────────────────
class _TkClipboard:
    """Clipboard provider backed by Tk, set once from App.__init__."""
    def __init__(self, root):
        self._root = root
    def get(self):
        try:
            return self._root.clipboard_get()
        except tk.TclError:
            return ""
    def set(self, text):
        self._root.clipboard_clear()
        self._root.clipboard_append(text)


class UIKnob(tk.Canvas):
    """Endless encoder visual — indicator rotates freely, no min/max."""
    def __init__(self, parent, size=50, bg_col="#1a1a1a"):
        super().__init__(parent, width=size, height=size, bg=bg_col, highlightthickness=0)
        self.size = size
        self.angle = 225.0  # Current angle in degrees (starts at 7 o'clock)
        self.center = size // 2
        self.radius = size // 2 - 4
        self.draw()

    def rotate(self, delta):
        """Rotate by delta encoder clicks. Positive=CW(right), negative=CCW(left)."""
        self.angle -= delta * 5  # 5 degrees per click — smooth visual rotation
        self.draw()

    def draw(self):
        self.delete("all")
        # Knob body
        self.create_oval(self.center - self.radius, self.center - self.radius,
                         self.center + self.radius, self.center + self.radius,
                         fill="#222", outline="#444", width=2)
        # Indicator line
        angle_rad = math.radians(self.angle)
        ix = self.center + (self.radius - 2) * math.cos(angle_rad)
        iy = self.center - (self.radius - 2) * math.sin(angle_rad)
        self.create_line(self.center, self.center, ix, iy, fill="#fff", width=2)
        # Dot at tip
        dot_r = 2
        self.create_oval(ix - dot_r, iy - dot_r, ix + dot_r, iy + dot_r,
                         fill="#00c8ff", outline="")


class ToolTip:
    """Lightweight tooltip for device elements — appears after 500 ms hover."""
    def __init__(self, widget, text_fn):
        self._widget = widget
        self._text_fn = text_fn
        self._tw = None
        self._after_id = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<Button-1>", self._hide, add="+")

    def _schedule(self, event=None):
        self._cancel()
        self._after_id = self._widget.after(500, self._show)

    def _cancel(self):
        if self._after_id:
            self._widget.after_cancel(self._after_id)
            self._after_id = None

    def _show(self):
        text = self._text_fn()
        if not text:
            return
        tw = tk.Toplevel(self._widget)
        tw.wm_overrideredirect(True)
        tw.wm_attributes("-topmost", True)
        x = self._widget.winfo_rootx() + 20
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        tw.wm_geometry(f"+{x}+{y}")
        lbl = tk.Label(tw, text=text, bg=theme.SURFACE_3, fg=theme.TEXT,
                       font=theme.F_SMALL, relief=tk.FLAT, bd=0, padx=10, pady=6,
                       justify=tk.LEFT, wraplength=320)
        lbl.pack()
        self._tw = tw

    def _hide(self, event=None):
        self._cancel()
        if self._tw:
            self._tw.destroy()
            self._tw = None


class App:
    def __init__(self, root):
        self.root = root
        self._last_note_time = {}  # uid -> float
        self.root.title("M-Vave SMC-PAD Controller")
        self.root.geometry("1320x780")
        self.root.minsize(1180, 700)
        self.root.configure(bg="#111")
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mvave_icon.ico")
        if os.path.exists(icon_path):
            try: self.root.iconbitmap(icon_path)
            except: pass

        # Крестик и сворачивание уводят окно в трей, выход — только из меню
        # значка. Если трей не поднялся (нет pystray), крестик закрывает
        # приложение, как раньше: иначе спрятанное окно было бы не вернуть.
        self._tray = tray.Tray("M-Vave SMC-PAD", icon_path)
        if self._tray.available:
            self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
            self.root.bind("<Unmap>", self._on_unmap, add="+")
            # Поток pystray не daemon: без stop() процесс переживает окно.
            self.root.bind("<Destroy>", lambda e: e.widget is self.root
                           and self._tray.stop(), add="+")
        else:
            self.root.protocol("WM_DELETE_WINDOW", lambda: on_closing(root))

        self.root.option_add('*TCombobox*Listbox.background', 'white')
        self.root.option_add('*TCombobox*Listbox.foreground', 'black')
        self.root.option_add('*TCombobox*Listbox.selectBackground', '#0078d7')
        self.root.option_add('*TCombobox*Listbox.selectForeground', 'white')

        self.ui_elements = {}
        self.current_sel = None
        self.raw_knob_values = {}
        self._prev_knob_cc = {}  # uid → previous absolute CC value for delta calc
        self._volume_osd = None  # индикатор громкости: None — ещё не создан, False — сбой
        self._current_bank = 3  # default bank
        self._identify_mode = False
        self._learn_uid = None   # элемент, ждущий привязки к CC/ноте
        self._color_ok = False   # вендорский канал цвета доступен
        self._color_base = ""    # цвет без яркости: ползунок не должен
                                 # умножать сам на себя при каждом движении
        self._pad_dim_job = None   # отложенная отправка общей яркости
        self._last_unbound = None  # ("cc"|"note", номер) — последнее сообщение,
                                   # которое не нашло хозяина

        # Clipboard provider for actions.text.type
        actions.set_clipboard_provider(_TkClipboard(root))
        actions.set_pad_brightness_provider(self._nudge_pad_brightness)

        theme.apply_ttk(self.root)
        ctk.set_appearance_mode("dark")

        self.build_ui()

        # Сбой в ЛЮБОМ обработчике Tk — в панель Ctrl+D, а не в stderr.
        # Под pythonw (а ярлык запускает именно его) sys.stderr равен None,
        # и штатный обработчик Tk падает сам, пытаясь напечатать трейсбек.
        # Снаружи это выглядит как зависание окна на ровном месте.
        self.root.report_callback_exception = self._on_tk_error

        ble.set_debug_callback(self._debug_log)
        self.update_ui_from_config()
        self.root.after(20, self.check_queue)

    # ══════════════════════════════════════════════════════════════════════════
    #  BUILD UI
    # ══════════════════════════════════════════════════════════════════════════
    # Правило вёрстки: CTk-виджеты — там, где их не перекрашивает код
    # (кнопки, поля, ползунки, переключатели). Метки, которым код на ходу
    # меняет fg/bg, остаются tk.Label: у CTk другие имена параметров, а его
    # .config() уходит во внутреннюю рамку, и вызов ломается молча.
    def _card(self, parent, **grid_or_pack):
        """Скруглённая карточка + внутренняя tk-рамка цвета карточки."""
        card = ctk.CTkFrame(parent, fg_color=theme.SURFACE,
                            corner_radius=theme.RADIUS, border_width=0)
        inner = tk.Frame(card, bg=theme.SURFACE)
        inner.pack(fill=tk.BOTH, expand=True, padx=14, pady=12)
        return card, inner

    def _lbl(self, parent, text="", fg=theme.TEXT, font=theme.F_SMALL, **kw):
        return tk.Label(parent, text=text, fg=fg, bg=parent.cget("bg"),
                        font=font, **kw)

    def _btn(self, parent, text, command, kind="secondary", **kw):
        colors = {
            "primary": (theme.ACCENT, theme.ACCENT_HOVER, "#ffffff"),
            "secondary": (theme.SURFACE_2, theme.SURFACE_3, theme.TEXT),
            "success": (theme.OK_DARK, "#24503a", "#b8f5cf"),
            "danger": (theme.DANGER_DARK, "#512730", "#ffb4b4"),
        }[kind]
        kw.setdefault("height", 30)
        kw.setdefault("corner_radius", theme.RADIUS_SM)
        return ctk.CTkButton(parent, text=text, command=command,
                             fg_color=colors[0], hover_color=colors[1],
                             text_color=colors[2], font=theme.C_SMALL, **kw)

    def build_ui(self):
        self.root.configure(bg=theme.BG)

        # ── Шапка: состояние устройства + общие настройки ────────────────────
        head_card, header = self._card(self.root)
        head_card.pack(fill=tk.X, padx=12, pady=(12, 6))

        row1 = tk.Frame(header, bg=theme.SURFACE)
        row1.pack(fill=tk.X)

        self._status_dot = tk.Canvas(row1, width=12, height=12, bg=theme.SURFACE,
                                     highlightthickness=0)
        self._status_dot.pack(side=tk.LEFT, padx=(0, 6))
        self.status_var = tk.StringVar(value="Подключение...")
        self._lbl(row1, fg=theme.TEXT, font=theme.F_BOLD,
                  textvariable=self.status_var).pack(side=tk.LEFT)
        self.status_var.trace_add("write", lambda *a: self._draw_status_dot())
        self._draw_status_dot()

        # Заряд. Значок рисуется на Canvas: эмодзи батареи Tk на Windows
        # показывает пустым квадратом (FIXES, «эмодзи в Tkinter не рисуются»).
        self._battery_pct = None
        self._battery_canvas = tk.Canvas(row1, width=28, height=14, bg=theme.SURFACE,
                                         highlightthickness=0)
        self._battery_canvas.pack(side=tk.LEFT, padx=(18, 5))
        self._battery_lbl = self._lbl(row1, "—", fg=theme.MUTED, font=theme.F_SMALL)
        self._battery_lbl.pack(side=tk.LEFT)
        self._draw_battery()

        self._bank_var = tk.StringVar(value="Банк 3")
        tk.Label(row1, textvariable=self._bank_var, fg=theme.WARN, bg=theme.SURFACE_2,
                 font=theme.F_SMALL, padx=10, pady=2).pack(side=tk.LEFT, padx=(18, 0))

        if appconfig.last_error:
            self._lbl(row1, f"⚠ {appconfig.last_error}", fg=theme.DANGER).pack(
                side=tk.LEFT, padx=10)

        self._identify_var = tk.BooleanVar(value=False)
        ctk.CTkSwitch(row1, text="Определить", variable=self._identify_var,
                      onvalue=True, offvalue=False, command=self._on_identify_toggle,
                      font=theme.C_SMALL, text_color=theme.MUTED,
                      progress_color=theme.ACCENT, button_color=theme.TEXT,
                      fg_color=theme.SURFACE_3, switch_width=34, switch_height=18,
                      width=110).pack(side=tk.LEFT, padx=(18, 0))

        # Пока ждём сигнал для привязки — это видно в шапке, а не только
        # мелкой строкой в инспекторе.
        self._learn_banner = tk.Label(row1, text="", fg="#1a1300", bg=theme.WARN,
                                      font=theme.F_SMALL + ("bold",), padx=10, pady=2)

        self._settings_btn = self._btn(row1, "Настройки  ▾", self._open_settings_menu,
                                       width=120)
        self._settings_btn.pack(side=tk.RIGHT)

        self.last_input_var = tk.StringVar(value="")
        self._lbl(row1, fg=theme.ACCENT, font=theme.F_SMALL,
                  textvariable=self.last_input_var).pack(side=tk.RIGHT, padx=(0, 16))

        # Цвет уходит на устройство по отдельному вендорскому каналу. Если его
        # нет — цвет останется только на экране, и об этом надо сказать вслух.
        self._color_status_var = tk.StringVar(value="цвет: ждём устройство")
        self._lbl(row1, fg=theme.DIM, font=theme.F_TINY,
                  textvariable=self._color_status_var).pack(side=tk.RIGHT, padx=(0, 16))

        # ── Общая яркость подсветки ──────────────────────────────────────────
        # Один фейдер на все 16 пэдов. Яркость конкретного пэда остаётся
        # частью его цвета, это живой множитель поверх неё.
        row2 = tk.Frame(header, bg=theme.SURFACE)
        row2.pack(fill=tk.X, pady=(10, 0))
        self._lbl(row2, "Яркость пэдов", fg=theme.MUTED).pack(side=tk.LEFT, padx=(0, 10))
        self._pad_dim_var = tk.IntVar(
            value=int(appconfig.config.get("pad_brightness", 100)))
        self._pad_dim_scale = ctk.CTkSlider(
            row2, from_=0, to=100, number_of_steps=100, variable=self._pad_dim_var,
            command=self._on_pad_dim_change, width=300, height=16,
            progress_color=theme.ACCENT, button_color=theme.TEXT,
            button_hover_color="#ffffff", fg_color=theme.SURFACE_3)
        self._pad_dim_scale.pack(side=tk.LEFT)
        self._pad_dim_lbl = self._lbl(row2, "", fg=theme.TEXT, font=theme.F_BOLD, width=5)
        self._pad_dim_lbl.pack(side=tk.LEFT, padx=(8, 0))
        self._pad_dim_lbl.config(text=f"{self._pad_dim_var.get()}%")
        self._lbl(row2, "можно и крутилкой: Подсветка → Яркость пэдов",
                  fg=theme.DIM, font=theme.F_TINY).pack(side=tk.LEFT, padx=16)

        # ── Основная область: схема слева, панель справа ─────────────────────
        main_area = tk.Frame(self.root, bg=theme.BG)
        main_area.pack(fill=tk.BOTH, expand=True, padx=12, pady=(6, 12))
        main_area.columnconfigure(0, weight=1)
        main_area.columnconfigure(1, weight=0)
        main_area.rowconfigure(0, weight=1)

        dev_card, device_frame = self._card(main_area)
        dev_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        S = theme.SURFACE

        # Схема — блок фиксированного размера. Без распорок по краям он липнет
        # в левый верхний угол, и справа от кнопок остаётся пустая половина
        # панели. Колонки 0 и 4 и строки 0 и 2 растягиваются, содержимое
        # оказывается по центру.
        device_frame.columnconfigure(0, weight=1)
        device_frame.columnconfigure(4, weight=1)
        device_frame.rowconfigure(0, weight=1)
        device_frame.rowconfigure(2, weight=1)

        # ── KNOBS: left side (4 rows × 2 cols, matches physical device) ──────
        knob_frame = tk.Frame(device_frame, bg=S)
        knob_frame.grid(row=1, column=1, padx=(0, 10), sticky="ns")

        for num in range(1, 9):
            uid = f"knob_{num}"
            grid_row = 3 - (num - 1) // 2   # 1,2→row3; 3,4→row2; 5,6→row1; 7,8→row0
            grid_col = (num - 1) % 2          # odd→col0, even→col1
            f = tk.Frame(knob_frame, bg=S)
            f.grid(row=grid_row, column=grid_col, padx=6, pady=6)

            lbl_num = tk.Label(f, text=str(num), fg=theme.DIM, bg=S,
                               font=theme.F_TINY)
            lbl_num.pack()

            knob_canvas = UIKnob(f, size=48, bg_col=S)
            knob_canvas.pack(pady=1)

            lbl_act = tk.Label(f, text="", fg=theme.MUTED, bg=S,
                               font=(theme.FONT, 7))
            lbl_act.pack()

            self.ui_elements[uid] = {"frame": f, "lbl": lbl_act, "num": lbl_num,
                                     "canvas": knob_canvas, "type": "knob", "midi_id": None}
            self.bind_click(f, uid, lbl_num, knob_canvas, lbl_act)
            self._attach_tooltip(f, uid)

        # ── PADS: standard MPC layout — PAD1 bottom-left, PAD16 top-right ────
        center_panel = tk.Frame(device_frame, bg=S)
        center_panel.grid(row=1, column=2, padx=10, pady=5)

        for num in range(1, 17):
            uid = f"pad_{num}"
            # MPC grid: row 3=bottom(PAD1-4), row 0=top(PAD13-16)
            grid_row = 3 - (num - 1) // 4
            grid_col = (num - 1) % 4
            outer = tk.Frame(center_panel, bg=S, padx=2, pady=2)
            outer.grid(row=grid_row, column=grid_col, padx=4, pady=4)
            f = tk.Frame(outer, width=94, height=94, bg="#222")
            f.pack()
            f.pack_propagate(False)

            lbl_num = tk.Label(f, text=f"PAD{num}", fg="#777", bg="#222",
                               font=(theme.FONT, 7, "bold"))
            lbl_num.pack(anchor="nw", padx=4, pady=3)
            lbl_act = tk.Label(f, text="", fg="white", bg="#222",
                               font=(theme.FONT, 9, "bold"), wraplength=86)
            lbl_act.pack(expand=True)

            self.ui_elements[uid] = {"outer": outer, "frame": f, "lbl": lbl_act,
                                     "num": lbl_num, "type": "pad", "midi_id": None}
            self.bind_click(outer, uid, f, lbl_num, lbl_act)
            self._attach_tooltip(outer, uid)

        # ── BUTTONS: vertical column on right (matches physical device) ───────
        btn_panel = tk.Frame(device_frame, bg=S)
        btn_panel.grid(row=1, column=3, padx=(10, 0), sticky="n", pady=(8, 0))

        btn_defs = [
            ("BT",       "#003355", "#8cf", 8),
            ("PAD BNK",  "#664400", "#fc0", 8),
            ("KNOB BNK", "#333333", "#ccc", 8),
            ("◀",        "#2a3a3a", "#8cc", 11),
            ("▶",        "#2a3a3a", "#8cc", 11),
            ("▶",        "#2a3a3a", "#8df", 11),
            # ⏸ и ⏺ Tkinter на Windows рисует пустым квадратом — проверено на
            # снимке окна. Берём глифы из базового набора Segoe UI.
            ("‖",        "#2a3a3a", "#8df", 11),
            ("●",        "#2a3a3a", "#8df", 11),
        ]
        for num, (icon, bg, fg, fsize) in enumerate(btn_defs, 1):
            uid = f"btn_{num}"
            outer = tk.Frame(btn_panel, bg=S, padx=1, pady=1)
            outer.pack(pady=2)
            # Ширину держит рамка, а подпись действия режется с «…» в
            # update_ui_from_config: раньше значок занимал 8 символов, и
            # подпись обрезалась краем панели («◀ Тре», «Pl»).
            f = tk.Frame(outer, width=172, height=34, bg=bg)
            f.pack()
            f.pack_propagate(False)

            # Номер и значок кнопки видны ВСЕГДА. Раньше подпись назначенного
            # действия затирала значок, и понять, какая это физическая кнопка,
            # было уже нельзя.
            lbl_num = tk.Label(f, text=str(num), fg="#888", bg=bg,
                               font=(theme.FONT, 7))
            lbl_num.pack(side=tk.LEFT, padx=(6, 3))
            lbl_icon = tk.Label(f, text=icon, fg=fg, bg=bg, anchor="w",
                                font=(theme.FONT, fsize, "bold"))
            lbl_icon.pack(side=tk.LEFT)
            lbl_act = tk.Label(f, text="", fg="#0f0", bg=bg, anchor="e",
                               font=theme.F_TINY)
            lbl_act.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(3, 8))

            self.ui_elements[uid] = {"outer": outer, "frame": f, "lbl": lbl_act,
                                     "num": lbl_num, "icon_lbl": lbl_icon,
                                     "type": "btn", "midi_id": None,
                                     "midi_kind": None, "icon": icon}
            self.bind_click(outer, uid, f, lbl_num, lbl_icon, lbl_act)
            self._attach_tooltip(outer, uid)

        # Shift и Note Repeat в эфир не шлют ничего — замер 2026-09-23,
        # docs/PROTOCOL.md §1. Показаны, чтобы схема совпадала с корпусом,
        # но выбрать их нельзя: назначить действие не на что.
        tk.Label(btn_panel, text="обрабатывает сам контроллер", fg=theme.DIM, bg=S,
                 font=(theme.FONT, 7)).pack(anchor="w", pady=(8, 1))
        fw_tip = ("Эту кнопку обрабатывает прошивка контроллера (Shift+пэд — "
                  "пресеты, чувствительность, октава). В компьютер она "
                  "ничего не передаёт, поэтому назначить на неё действие нельзя.")
        for icon in ("SHIFT", "NOTE RPT"):
            outer = tk.Frame(btn_panel, bg=S, padx=1, pady=1)
            outer.pack(pady=2)
            f = tk.Frame(outer, width=172, height=30, bg=theme.SURFACE_2)
            f.pack()
            f.pack_propagate(False)
            tk.Label(f, text=icon, fg=theme.DIM, bg=theme.SURFACE_2, anchor="w",
                     font=(theme.FONT, 8, "bold")).pack(side=tk.LEFT, padx=(18, 0))
            tk.Label(f, text="прошивка", fg=theme.DIM, bg=theme.SURFACE_2, anchor="e",
                     font=(theme.FONT, 7)).pack(side=tk.RIGHT, padx=8)
            ToolTip(outer, lambda: fw_tip)

        # ── RIGHT INSPECTOR PANEL ─────────────────────────────────────────────
        self._build_inspector(main_area)

        # Hidden debug log (Ctrl+D to toggle)
        self._debug_visible = False
        self._debug_lines = []
        self._debug_frame = tk.Frame(self.root, bg="#0a0a0a", bd=1, relief=tk.SUNKEN)
        self._debug_text = tk.Text(self._debug_frame, height=5, bg="#0a0a0a", fg="#00ff41",
                                   font=("Consolas", 8), state=tk.DISABLED, wrap=tk.NONE,
                                   insertbackground="#00ff41")
        self._debug_text.pack(fill=tk.X, padx=2, pady=2)
        self.root.bind("<Control-d>", self._toggle_debug)
        self.root.bind("<Control-D>", self._toggle_debug)

    # ── Индикаторы шапки ──────────────────────────────────────────────────────
    def _draw_status_dot(self):
        s = self.status_var.get().lower()
        if "подключено" in s:
            col = theme.OK
        elif "не найден" in s or "ошибка" in s:
            col = theme.DANGER
        else:
            col = theme.WARN
        c = self._status_dot
        c.delete("all")
        c.create_oval(2, 2, 11, 11, fill=col, outline="")

    def _draw_battery(self):
        pct = self._battery_pct
        c = self._battery_canvas
        c.delete("all")
        if pct is None:
            col = theme.DIM
        elif pct <= 20:
            col = theme.WARN
        else:
            col = theme.TEXT
        c.create_rectangle(1, 1, 24, 13, outline=col, width=1.5)
        c.create_rectangle(25, 5, 27, 9, fill=col, outline="")
        if pct is not None:
            w = max(1, round(20 * pct / 100))
            c.create_rectangle(3.5, 3.5, 3.5 + w, 10.5,
                               fill=theme.WARN if pct <= 20 else theme.OK, outline="")
        self._battery_lbl.config(text="—" if pct is None else f"{pct}%",
                                 fg=theme.MUTED if pct is None else col)

    def _set_battery(self, pct):
        self._battery_pct = pct if 0 <= pct <= 100 else None
        self._draw_battery()

    # ── Меню «Настройки»: перенос на другой компьютер ────────────────────────
    def _open_settings_menu(self):
        m = tk.Menu(self.root, tearoff=False, bg=theme.SURFACE_2, fg=theme.TEXT,
                    activebackground=theme.ACCENT, activeforeground="#ffffff",
                    bd=0, font=theme.F_SMALL)
        m.add_command(label="Сохранить настройки в файл…", command=self._on_export)
        m.add_command(label="Загрузить настройки из файла…", command=self._on_import)
        m.add_separator()
        m.add_command(label="Открыть папку с настройками", command=self._on_open_config_dir)
        b = self._settings_btn
        m.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height() + 4)

    def _on_export(self):
        name = time.strftime("smc-pad-настройки-%Y-%m-%d.json")
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Сохранить настройки", initialfile=name,
            defaultextension=".json", filetypes=[("Настройки SMC-PAD", "*.json")])
        if not path:
            return
        try:
            appconfig.export_config(path)
        except OSError as e:
            messagebox.showerror("Настройки не сохранены", str(e), parent=self.root)
            return
        messagebox.showinfo(
            "Настройки сохранены",
            f"{path}\n\nНа другом компьютере: Настройки → «Загрузить настройки "
            f"из файла…». Адрес контроллера в файл не попадает — там он "
            f"найдётся сам.", parent=self.root)

    def _on_import(self):
        path = filedialog.askopenfilename(
            parent=self.root, title="Загрузить настройки",
            filetypes=[("Настройки SMC-PAD", "*.json"), ("Все файлы", "*.*")])
        if not path:
            return
        if not messagebox.askyesno(
                "Заменить настройки?",
                "Все назначения, цвета и яркость заменятся настройками из файла.\n"
                "Текущие сохранятся рядом отдельным файлом — к ним можно вернуться "
                "тем же пунктом меню.", parent=self.root):
            return
        ok, report = appconfig.import_config(path)
        if not ok:
            messagebox.showerror("Настройки не загружены", report, parent=self.root)
            return
        self._pad_dim_var.set(int(appconfig.config.get("pad_brightness", 100)))
        self._pad_dim_lbl.config(text=f"{self._pad_dim_var.get()}%")
        self.load_hardware_mapping()
        self.update_ui_from_config()
        if self.current_sel:
            self.select_element(self.current_sel)
        self._on_resend_colors()
        show = messagebox.showwarning if "не найдены" in report else messagebox.showinfo
        show("Настройки загружены", report, parent=self.root)

    def _on_open_config_dir(self):
        try:
            os.startfile(os.path.dirname(os.path.abspath(appconfig.CONFIG_FILE)))
        except OSError as e:
            messagebox.showerror("Папка не открылась", str(e), parent=self.root)

    # ── Inspector panel ───────────────────────────────────────────────────────
    def _build_inspector(self, parent):
        S = theme.SURFACE
        card = ctk.CTkFrame(parent, fg_color=S, corner_radius=theme.RADIUS,
                            width=392, border_width=0)
        card.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        card.grid_propagate(False)
        card.pack_propagate(False)
        insp = tk.Frame(card, bg=S)
        insp.pack(fill=tk.BOTH, expand=True, padx=16, pady=14)
        self._inspector_frame = insp

        # === 1. Заголовок ===
        self._insp_header = tk.Label(insp, text="Ничего не выбрано", fg=theme.TEXT,
                                     bg=S, font=theme.F_TITLE, anchor="w")
        self._insp_header.pack(fill=tk.X)
        self._insp_hint = tk.Label(
            insp, text="Нажми пэд, крутилку или кнопку на схеме слева —\n"
                       "здесь появится, что она делает.",
            fg=theme.MUTED, bg=S, font=theme.F_SMALL, anchor="w", justify=tk.LEFT)
        self._insp_hint.pack(fill=tk.X, pady=(4, 0))

        # === Content area (hidden until element selected) ===
        self._insp_content = tk.Frame(insp, bg=S)

        # === 1b. Привязка MIDI ===
        # Пэды и крутилки опознаются по картам нот и CC. Кнопки справа шлют
        # неизвестные CC — карты для них нет, и без ручной привязки нажатие
        # физической кнопки не доходит ни до чего.
        learn = self._learn_frame = tk.Frame(self._insp_content, bg=S)
        learn.pack(fill=tk.X, pady=(8, 4))

        learn_row = tk.Frame(learn, bg=S)
        learn_row.pack(fill=tk.X)
        tk.Label(learn_row, text="MIDI", fg=theme.MUTED, bg=theme.SURFACE_2,
                 font=theme.F_TINY, padx=6).pack(side=tk.LEFT)
        self._learn_lbl = tk.Label(learn_row, text="—", fg=theme.MUTED, bg=S,
                                   font=theme.F_SMALL, anchor="w")
        self._learn_lbl.pack(side=tk.LEFT, padx=(8, 0))
        self._learn_btn = self._btn(learn_row, "Привязать", self._on_learn_toggle,
                                    width=86, height=26)
        self._learn_btn.pack(side=tk.RIGHT)
        self._btn(learn_row, "Забыть", self._on_learn_forget, width=66,
                  height=26).pack(side=tk.RIGHT, padx=(0, 6))

        # Привязка «задним числом». Ловить сигнал в момент нажатия — гонка:
        # надо успеть нажать «Привязать», не потерять выделение и попасть по
        # кнопке. Последнее непривязанное сообщение запоминается, и его можно
        # повесить на элемент одним кликом уже ПОСЛЕ того, как оно пришло.
        self._bind_last_btn = self._btn(learn, "", self._on_bind_last, kind="success",
                                        height=28, anchor="w")
        # not packed yet

        # === 2. Режим крутилки ===
        self._knob_mode_frame = tk.Frame(self._insp_content, bg=S)
        self._knob_mode_var = tk.StringVar(value="delta")
        self._knob_mode_names = {"delta": "Плавно", "pair": "Влево / вправо"}
        self._knob_mode_seg = ctk.CTkSegmentedButton(
            self._knob_mode_frame, values=list(self._knob_mode_names.values()),
            command=self._on_knob_mode_seg, font=theme.C_SMALL, height=30,
            fg_color=theme.SURFACE_2, unselected_color=theme.SURFACE_2,
            unselected_hover_color=theme.SURFACE_3, selected_color=theme.ACCENT,
            selected_hover_color=theme.ACCENT_HOVER, text_color=theme.TEXT)
        self._knob_mode_seg.pack(fill=tk.X)
        # Переключатель — только отображение. Источник правды — переменная:
        # её выставляют select_element и тесты, трасса держит кнопку в согласии.
        self._knob_mode_var.trace_add("write", lambda *a: self._knob_mode_seg.set(
            self._knob_mode_names.get(self._knob_mode_var.get(), "Плавно")))
        # Not packed yet — shown only for knobs

        # === 2b. Стороны (режим «влево / вправо») ===
        self._pair_frame = tk.Frame(self._insp_content, bg=S)
        self._pair_slot = tk.StringVar(value="ccw")  # which slot is being assigned
        for value, text in (("ccw", "◀ Влево"), ("cw", "Вправо ▶")):
            row = tk.Frame(self._pair_frame, bg=S)
            row.pack(fill=tk.X, pady=1)
            ctk.CTkRadioButton(row, text=text, variable=self._pair_slot, value=value,
                               command=self._on_pair_slot_change, font=theme.C_SMALL,
                               text_color=theme.TEXT, fg_color=theme.ACCENT,
                               border_color=theme.SURFACE_3, width=100,
                               radiobutton_width=16, radiobutton_height=16).pack(side=tk.LEFT)
            lbl = tk.Label(row, text="—", fg=theme.MUTED, bg=S, font=theme.F_SMALL,
                           anchor="w")
            lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
            if value == "ccw":
                self._pair_ccw_lbl = lbl
            else:
                self._pair_cw_lbl = lbl

        # === 2c. Вкладки пэда: «Действие» / «Цвет» ===
        self._tab_seg = ctk.CTkSegmentedButton(
            self._insp_content, values=["Действие", "Цвет"], command=self._show_tab,
            font=theme.C_SMALL, height=30, fg_color=theme.SURFACE_2,
            unselected_color=theme.SURFACE_2, unselected_hover_color=theme.SURFACE_3,
            selected_color=theme.SURFACE_3, selected_hover_color=theme.SURFACE_3,
            text_color=theme.TEXT)
        self._tab_seg.set("Действие")
        # Not packed yet — shown only for pads

        # ── Вкладка «Действие» ───────────────────────────────────────────────
        self._tab_action = tk.Frame(self._insp_content, bg=S)
        self._tab_action.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        self._visible_tab = self._tab_action

        # === 3. Поиск ===
        search_frame = self._search_frame = tk.Frame(self._tab_action, bg=S)
        search_frame.pack(fill=tk.X, pady=(0, 6))
        self._search_var = tk.StringVar()
        self._search_placeholder_active = False   # плейсхолдер рисует сам CTkEntry
        self._search_entry = ctk.CTkEntry(
            search_frame, placeholder_text="Поиск действия…", height=32,
            font=theme.C_SMALL, fg_color=theme.SURFACE_2, border_color=theme.SURFACE_3,
            border_width=1, text_color=theme.TEXT, placeholder_text_color=theme.DIM,
            corner_radius=theme.RADIUS_SM)
        self._search_entry.pack(fill=tk.X)
        # textvariable у CTkEntry отключает плейсхолдер — поэтому переменная
        # поиска кормится с клавиатуры, а не привязкой.
        self._search_entry.bind("<KeyRelease>", lambda e: self._search_var.set(
            self._search_entry.get()))
        self._search_var.trace_add("write", self._on_search_changed)

        # === 4. Список действий ===
        tree_frame = tk.Frame(self._tab_action, bg=theme.SURFACE_2)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        self._action_tree = ttk.Treeview(tree_frame, height=12, show="tree",
                                         selectmode="browse")
        tree_scroll = ctk.CTkScrollbar(tree_frame, command=self._action_tree.yview,
                                       fg_color=theme.SURFACE_2,
                                       button_color=theme.SURFACE_3,
                                       button_hover_color=theme.DIM, width=12)
        self._action_tree.configure(yscrollcommand=tree_scroll.set)
        self._action_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6, 0), pady=6)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y, padx=2, pady=4)
        self._action_tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        # === 5. Описание ===
        self._desc_lbl = tk.Label(self._tab_action, text="", fg=theme.MUTED, bg=S,
                                  font=theme.F_SMALL, anchor="w",
                                  wraplength=350, justify=tk.LEFT)
        self._desc_lbl.pack(fill=tk.X, pady=(6, 0))

        # === 6. Параметр ===
        self._param_frame = tk.Frame(self._tab_action, bg=S)
        self._param_label = tk.Label(self._param_frame, text="Параметр", fg=theme.MUTED,
                                     bg=S, font=theme.F_SMALL)
        self._param_label.pack(side=tk.LEFT, padx=(0, 8))
        self._param_var = tk.StringVar()
        self._param_entry = ctk.CTkEntry(
            self._param_frame, textvariable=self._param_var, height=30,
            font=theme.C_SMALL, fg_color=theme.SURFACE_2, border_color=theme.SURFACE_3,
            border_width=1, text_color=theme.TEXT, corner_radius=theme.RADIUS_SM)
        self._param_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._param_entry.bind("<FocusOut>", self._on_param_commit)
        self._param_entry.bind("<Return>", self._on_param_commit)
        self._param_btn = self._btn(self._param_frame, "Обзор…", self._on_param_browse,
                                    width=86)
        self._param_btn.pack(side=tk.LEFT, padx=(6, 0))
        # Not packed yet

        # ── Вкладка «Цвет» ───────────────────────────────────────────────────
        self._tab_color = tk.Frame(self._insp_content, bg=S)
        self._color_frame = self._tab_color   # прежнее имя: на него ссылается код

        self._lbl(self._tab_color, "Цвет пэда", fg=theme.MUTED).pack(anchor="w")
        pal = tk.Frame(self._tab_color, bg=S)
        pal.pack(fill=tk.X, pady=(6, 10))
        self._color_preset_btns = []
        for i, hex_c in enumerate(COLOR_PRESETS):
            btn = ctk.CTkButton(pal, text="", width=26, height=26, corner_radius=13,
                                fg_color=hex_c, hover_color=hex_c,
                                border_width=2 if hex_c == "#000000" else 0,
                                border_color=theme.SURFACE_3,
                                command=lambda c=hex_c: self._apply_color(c))
            btn.grid(row=i // 6, column=i % 6, padx=3, pady=3)
            self._color_preset_btns.append((hex_c, btn))
        # Свой цвет — такой же кружок: широкая кнопка раздвигала колонку сетки
        custom = self._btn(pal, "+", self._pick_custom_color, width=26, height=26,
                           corner_radius=13)
        custom.grid(row=1, column=5, padx=3, pady=3)
        ToolTip(custom, lambda: "Свой цвет…")

        hexrow = tk.Frame(self._tab_color, bg=S)
        hexrow.pack(fill=tk.X, pady=(0, 10))
        self._lbl(hexrow, "Код", fg=theme.MUTED).pack(side=tk.LEFT, padx=(0, 8))
        self._color_hex_var = tk.StringVar()
        color_hex_entry = ctk.CTkEntry(
            hexrow, textvariable=self._color_hex_var, width=100, height=30,
            font=theme.C_MONO, fg_color=theme.SURFACE_2, border_color=theme.SURFACE_3,
            border_width=1, text_color=theme.TEXT, corner_radius=theme.RADIUS_SM)
        color_hex_entry.pack(side=tk.LEFT)
        color_hex_entry.bind("<Return>", self._on_color_hex_commit)
        color_hex_entry.bind("<FocusOut>", self._on_color_hex_commit)

        brow = tk.Frame(self._tab_color, bg=S)
        brow.pack(fill=tk.X, pady=(0, 10))
        self._lbl(brow, "Яркость пэда", fg=theme.MUTED).pack(side=tk.LEFT, padx=(0, 8))
        self._brightness_var = tk.IntVar(value=100)
        self._brightness_scale = ctk.CTkSlider(
            brow, from_=10, to=100, number_of_steps=90, variable=self._brightness_var,
            command=self._on_brightness_change, height=16,
            progress_color=theme.ACCENT, button_color=theme.TEXT,
            button_hover_color="#ffffff", fg_color=theme.SURFACE_3)
        self._brightness_scale.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # «Как отправить цвет на пэд» не должно быть вопросом: цвет уходит
        # сразу при выборе. Кнопка нужна на случай, когда устройство
        # переподключилось — записи цвета волатильные.
        crow = tk.Frame(self._tab_color, bg=S)
        crow.pack(fill=tk.X)
        self._color_hint_lbl = tk.Label(crow, text="", fg=theme.MUTED, bg=S,
                                        font=theme.F_TINY, anchor="w", justify=tk.LEFT,
                                        wraplength=220)
        self._color_hint_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._btn(crow, "Отправить заново", self._on_resend_colors, width=130,
                  height=28).pack(side=tk.RIGHT)

        # === 8. Кнопки действий ===
        # Именованный: блоки вкладок пакуются динамически ПОЗЖЕ, а pack без
        # before кладёт их в самый низ — под кнопки. Вкладка встаёт before=сюда.
        btn_frame = self._btn_frame = tk.Frame(self._insp_content, bg=S)
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(10, 0))

        row = tk.Frame(btn_frame, bg=S)
        row.pack(fill=tk.X)
        self._btn(row, "Выполнить", self._on_execute, kind="primary",
                  width=120, height=34).pack(side=tk.LEFT)
        self._btn(row, "Очистить", self.clear_current, width=96,
                  height=34).pack(side=tk.RIGHT)
        self._exec_error_lbl = tk.Label(btn_frame, text="", fg=theme.DANGER, bg=S,
                                        font=theme.F_TINY, anchor="w")
        self._exec_error_lbl.pack(fill=tk.X, pady=(4, 0))
        ctk.CTkButton(btn_frame, text="Сбросить все назначения", command=self.clear_all,
                      fg_color="transparent", hover_color=theme.DANGER_DARK,
                      text_color=theme.DANGER, font=theme.C_TINY, height=24,
                      width=10).pack(anchor="e", pady=(6, 0))

    def _show_tab(self, name):
        """Вкладка пэда. Для крутилок и кнопок видна всегда «Действие»."""
        tab = self._tab_color if name == "Цвет" else self._tab_action
        if tab is not self._visible_tab:
            self._visible_tab.pack_forget()
            self._visible_tab = tab
        tab.pack(fill=tk.BOTH, expand=True, pady=(6, 0), before=self._btn_frame)
        self._tab_seg.set("Цвет" if tab is self._tab_color else "Действие")

    def _on_knob_mode_seg(self, label):
        mode = "pair" if label == self._knob_mode_names["pair"] else "delta"
        self._knob_mode_var.set(mode)
        self._on_knob_mode_change()

    # ── Tooltip helper ────────────────────────────────────────────────────────
    def _attach_tooltip(self, widget, uid):
        def _get_text():
            bindings = appconfig.config.get("bindings", {})
            b = bindings.get(uid, {})
            action_id = b.get("action", "none")
            if action_id == "none":
                return ""
            act = actions.get(action_id)
            if not act:
                return ""
            text = act.label
            param = b.get("param")
            if param:
                text += f"\n{param}"
            return text
        ToolTip(widget, _get_text)

    # ── Search helpers ────────────────────────────────────────────────────────
    def _search_focus_in(self, event=None):
        if self._search_placeholder_active:
            self._search_entry.delete(0, tk.END)
            self._search_entry.config(foreground="black")
            self._search_placeholder_active = False

    def _search_focus_out(self, event=None):
        if not self._search_var.get():
            self._search_placeholder_active = True
            self._search_entry.insert(0, "поиск действия…")
            self._search_entry.config(foreground="#888")

    def _on_search_changed(self, *args):
        if self._search_placeholder_active:
            return
        self._populate_tree()

    # ── Tree population ───────────────────────────────────────────────────────
    def _get_required_kind(self):
        """Return the action kind filter for the currently selected element."""
        if not self.current_sel:
            return "trigger"
        el = self.ui_elements[self.current_sel]
        if el["type"] == "knob":
            mode = self._knob_mode_var.get()
            return "delta" if mode == "delta" else "trigger"
        return "trigger"

    def _populate_tree(self, select_action_id=None):
        """Rebuild the action tree with filter and search."""
        tree = self._action_tree
        tree.delete(*tree.get_children())

        kind = self._get_required_kind()
        search_text = ""
        if not self._search_placeholder_active:
            search_text = self._search_var.get().strip().lower()

        filtered = actions.for_kind(kind)
        # Skip "none" action from tree — we have a Clear button for that
        filtered = [a for a in filtered if a.id != "none"]

        # Group by category
        from collections import OrderedDict
        cats = OrderedDict()
        for cat in actions.CATEGORY_ORDER:
            cat_actions = [a for a in filtered if a.category == cat]
            if search_text:
                cat_actions = [a for a in cat_actions
                               if search_text in a.label.lower()
                               or search_text in a.hint.lower()]
            if cat_actions:
                cats[cat] = cat_actions

        # Build tree nodes
        select_iid = None
        for cat, acts in cats.items():
            cat_iid = f"_cat_{cat}"
            tree.insert("", tk.END, iid=cat_iid, text=f"  {cat}", open=bool(search_text))
            for a in acts:
                tree.insert(cat_iid, tk.END, iid=a.id, text=f"    {a.label}")
                if a.id == select_action_id:
                    select_iid = a.id

        # Select and reveal current action
        if select_iid:
            parent = tree.parent(select_iid)
            if parent:
                tree.item(parent, open=True)
            tree.selection_set(select_iid)
            tree.see(select_iid)

    # ── Tree selection handler ────────────────────────────────────────────────
    def _on_tree_select(self, event=None):
        sel = self._action_tree.selection()
        if not sel:
            return
        iid = sel[0]
        # Ignore category nodes
        if iid.startswith("_cat_"):
            return

        act = actions.get(iid)
        if not act:
            return

        # Show description
        self._desc_lbl.config(text=act.hint)

        # If an element is selected, assign action
        if self.current_sel:
            self._assign_action(act)

    def _assign_action(self, act):
        """Write the selected action to config for the current element."""
        uid = self.current_sel
        if not uid:
            return

        bindings = appconfig.config["bindings"]
        if uid not in bindings:
            bindings[uid] = {}

        el = self.ui_elements[uid]
        if el["type"] == "knob" and self._knob_mode_var.get() == "pair":
            key = "ccw" if self._pair_slot.get() == "ccw" else "cw"
            prev = _side(bindings[uid], key)
            # Параметр сохраняем, только если действие то же самое.
            keep = prev.get("param") if prev.get("action") == act.id else None
            bindings[uid][key] = {
                "action": act.id,
                "param": keep if keep is not None else _initial_param(act),
            }
            bindings[uid]["mode"] = "pair"
            self._update_pair_labels()
            target = bindings[uid][key]
        else:
            prev_action = bindings[uid].get("action")
            bindings[uid]["action"] = act.id
            if el["type"] == "knob":
                bindings[uid]["mode"] = "delta"
            if act.param_kind:
                if prev_action != act.id:
                    bindings[uid]["param"] = _initial_param(act)
            else:
                bindings[uid].pop("param", None)
            target = bindings[uid]

        appconfig.save_config()
        self._show_param_for_action(act, target)
        self.update_ui_from_config()

    # ── Knob mode ─────────────────────────────────────────────────────────────
    def _on_knob_mode_change(self):
        mode = self._knob_mode_var.get()
        if mode == "pair":
            # before= обязан указывать на СОСЕДА по тому же менеджеру.
            # Здесь стояло `_action_tree.master.master` — это _insp_content,
            # то есть родитель, а не сосед: Tk отвечал TclError, галка не
            # включалась. Под pythonw трейсбек уходил в несуществующий
            # stderr, и обработчик ошибок Tk валился следом — снаружи это
            # выглядело как зависание.
            self._pair_frame.pack(fill=tk.X, pady=(6, 0),
                                  before=self._visible_tab)
        else:
            self._pair_frame.pack_forget()

        # Update config mode without clearing action
        if self.current_sel:
            bindings = appconfig.config["bindings"]
            if self.current_sel not in bindings:
                bindings[self.current_sel] = {}
            bindings[self.current_sel]["mode"] = mode
            appconfig.save_config()

        # Repopulate tree with new kind filter
        current_action = None
        if self.current_sel:
            b = appconfig.config["bindings"].get(self.current_sel, {})
            current_action = b.get("action")
        self._populate_tree(select_action_id=current_action)
        self._update_pair_labels()

    def _on_pair_slot_change(self):
        """When user switches pair slot, highlight current action in tree."""
        if not self.current_sel:
            return
        b = appconfig.config["bindings"].get(self.current_sel, {})
        side = _side(b, "ccw" if self._pair_slot.get() == "ccw" else "cw")
        action_id = side.get("action")
        act = actions.get(action_id) if action_id else None
        if action_id:
            try:
                self._action_tree.selection_set(action_id)
                self._action_tree.see(action_id)
            except tk.TclError:
                pass
        # Поле параметра принадлежит выбранной стороне, а не крутилке целиком.
        self._show_param_for_action(act, side)

    def _update_pair_labels(self):
        if not self.current_sel:
            return
        b = appconfig.config["bindings"].get(self.current_sel, {})
        ccw_act = actions.get(_side(b, "ccw").get("action") or "")
        cw_act = actions.get(_side(b, "cw").get("action") or "")
        self._pair_ccw_lbl.config(text=ccw_act.label if ccw_act else "—")
        self._pair_cw_lbl.config(text=cw_act.label if cw_act else "—")

    # ── Parameter area ────────────────────────────────────────────────────────
    def _show_param_for_action(self, act, binding):
        """Show/hide parameter widget based on action's param_kind."""
        if not act or not act.param_kind:
            self._param_frame.pack_forget()
            return

        param = binding.get("param", act.default_param or "")
        self._param_var.set(str(param) if param else "")
        self._current_param_kind = act.param_kind

        if act.param_kind == "hotkey":
            self._param_entry.configure(state="readonly")
            self._param_btn.configure(text="Записать", command=self._on_record_hotkey)
            self._param_btn.pack(side=tk.LEFT, padx=(5, 0))
        elif act.param_kind == "exe":
            self._param_entry.configure(state="normal")
            self._param_btn.configure(text="Обзор…", command=self._on_param_browse)
            self._param_btn.pack(side=tk.LEFT, padx=(5, 0))
        elif act.param_kind == "folder":
            self._param_entry.configure(state="normal")
            self._param_btn.configure(text="Обзор…", command=self._on_param_browse)
            self._param_btn.pack(side=tk.LEFT, padx=(5, 0))
        elif act.param_kind in ("url", "text"):
            self._param_entry.configure(state="normal")
            self._param_btn.pack_forget()
        else:
            self._param_frame.pack_forget()
            return

        # Параметр — главное поле после выбора действия, он идёт НАД цветом.
        # Оба блока пакуются динамически, поэтому порядок задаётся явно через
        # before, иначе кто сработал последним, тот и оказался ниже.
        # Параметр живёт внизу вкладки «Действие», под описанием: он
        # последний в своей рамке, и pack без before ставит его ровно туда.
        self._param_frame.pack(fill=tk.X, pady=(8, 0))

    def _on_param_commit(self, event=None):
        if not self.current_sel:
            return
        bindings = appconfig.config["bindings"]
        if self.current_sel not in bindings:
            bindings[self.current_sel] = {}
        b = bindings[self.current_sel]
        val = self._param_var.get().strip()
        el = self.ui_elements.get(self.current_sel, {})
        if el.get("type") == "knob" and b.get("mode") == "pair":
            # Пишем в сторону, а не в крутилку: иначе левая и правая
            # затирают параметр друг друга.
            key = "ccw" if self._pair_slot.get() == "ccw" else "cw"
            side = _side(b, key)
            side["param"] = val if val else None
            b[key] = side
        else:
            b["param"] = val if val else None
        appconfig.save_config()
        self.update_ui_from_config()

    def _on_param_browse(self):
        pk = getattr(self, "_current_param_kind", None)
        if pk == "exe":
            path = filedialog.askopenfilename(
                title="Выбрать программу",
                filetypes=[("Исполняемые", "*.exe;*.lnk;*.bat;*.cmd"),
                           ("Все файлы", "*.*")])
        elif pk == "folder":
            path = filedialog.askdirectory(title="Выбрать папку")
        else:
            return
        if path:
            self._param_var.set(path)
            self._on_param_commit()

    def _on_record_hotkey(self):
        result = self.record_hotkey()
        if result:
            self._param_var.set(result)
            self._on_param_commit()

    # ── Color area ────────────────────────────────────────────────────────────
    def _update_color_hint(self):
        self._color_hint_lbl.config(
            text="цвет уходит на пэд сразу" if self._color_ok
                 else "устройство цвет не принимает — только экран",
            fg="#8c8" if self._color_ok else "#c88")

    def _apply_color(self, hex_col, keep_base=False):
        if not self.current_sel:
            return
        el = self.ui_elements[self.current_sel]
        if el["type"] != "pad":
            return

        # Яркость масштабирует БАЗОВЫЙ цвет. Раньше она масштабировала то,
        # что уже лежало в поле hex — то есть саму себя, и каждое движение
        # ползунка гасило пэд ещё раз.
        if not keep_base:
            self._color_base = hex_col
        pct = self._brightness_var.get()
        final = _apply_brightness(self._color_base, pct) if pct < 100 else self._color_base

        bindings = appconfig.config["bindings"]
        if self.current_sel not in bindings:
            bindings[self.current_sel] = {}
        bindings[self.current_sel]["color"] = final
        self._color_hex_var.set(final)
        self._save_config()
        self.update_ui_from_config()

        # Собственно отправка на железо. До этого цвет жил только на экране.
        ble.set_pad_color(int(self.current_sel.split("_")[1]), final)

    def _pick_custom_color(self):
        result = colorchooser.askcolor(title="Выберите цвет")
        if result and result[1]:
            self._apply_color(result[1])

    def _on_color_hex_commit(self, event=None):
        val = self._color_hex_var.get().strip()
        if val and not val.startswith("#"):
            val = "#" + val
        if val and len(val) == 7:
            self._apply_color(val)

    def _on_brightness_change(self, val):
        if getattr(self, "_suspend_brightness", False):
            return
        if self._color_base:
            self._apply_color(self._color_base, keep_base=True)

    # ── Execute button ────────────────────────────────────────────────────────
    def _on_execute(self):
        self._exec_error_lbl.config(text="")
        if not self.current_sel:
            return
        bindings = appconfig.config["bindings"]
        b = bindings.get(self.current_sel, {})
        action_id = b.get("action", "none")
        if action_id == "none":
            return
        param = b.get("param")
        el = self.ui_elements[self.current_sel]
        delta = 1 if el["type"] == "knob" and b.get("mode") == "delta" else 0
        err = actions.execute(action_id, param, delta=delta)
        if err:
            self._exec_error_lbl.config(text=err)

    def _on_resend_colors(self):
        ble.resend_all_colors()
        self._debug_log("цвета отправлены заново")

    # ── Общая яркость подсветки ───────────────────────────────────────────────
    def _on_pad_dim_change(self, val=None):
        pct = int(self._pad_dim_var.get())
        self._pad_dim_lbl.config(text=f"{pct}%")
        appconfig.config["pad_brightness"] = pct
        self.update_ui_from_config()
        # Ползунок шлёт команду на каждое движение, а в эфир уходит 16 кадров
        # по 25 мс. Отправляем через паузу после последнего движения.
        if self._pad_dim_job is not None:
            self.root.after_cancel(self._pad_dim_job)
        self._pad_dim_job = self.root.after(200, self._flush_pad_dim)

    def _flush_pad_dim(self):
        self._pad_dim_job = None
        self._save_config()
        ble.resend_all_colors()

    def _nudge_pad_brightness(self, delta):
        """Крутилка или кнопка двигает общую яркость."""
        pct = max(0, min(100, int(self._pad_dim_var.get()) + int(delta)))
        if pct == int(self._pad_dim_var.get()):
            return
        self._pad_dim_var.set(pct)
        self._on_pad_dim_change()

    # ── Привязка MIDI (MIDI learn) ────────────────────────────────────────────
    def _on_learn_toggle(self):
        if not self.current_sel:
            self._debug_log("«Привязать»: не выделен ни один элемент")
            return
        self._learn_uid = None if self._learn_uid else self.current_sel
        if self._learn_uid:
            self._debug_log(f"«Привязать»: жду сигнал для {self._learn_uid}")
        else:
            self._debug_log("«Привязать»: отменено")
        self._update_learn_row()

    def _on_bind_last(self):
        """Повесить последнее непривязанное сообщение на текущий элемент."""
        if not self.current_sel or not self._last_unbound:
            return
        kind, midi_id = self._last_unbound
        self._learn_uid = self.current_sel
        self._capture_learn(kind, midi_id)
        self._last_unbound = None
        self._update_learn_row()

    def _on_learn_forget(self):
        uid = self.current_sel
        if not uid:
            return
        self.ui_elements[uid]["midi_id"] = None
        self.ui_elements[uid]["midi_kind"] = None
        b = appconfig.config["bindings"].get(uid)
        if b:
            b.pop("midi_id", None)
            b.pop("midi_kind", None)
            self._save_config()
        if self._learn_uid == uid:
            self._learn_uid = None
        self._update_learn_row()

    def _update_learn_row(self):
        uid = self.current_sel
        if not uid:
            return

        # Кнопка «привязать задним числом» — только когда есть что вешать.
        if self._last_unbound and self._learn_uid != uid:
            kind, mid = self._last_unbound
            self._bind_last_btn.configure(
                text=f"⇦ повесить сюда {'CC' if kind == 'cc' else 'ноту'} {mid}")
            self._bind_last_btn.pack(fill=tk.X, pady=(4, 0))
        else:
            self._bind_last_btn.pack_forget()

        if self._learn_uid == uid:
            self._learn_lbl.config(text="нажмите контрол на устройстве…",
                                   fg="#ffaa00")
            self._learn_btn.configure(text="Отмена", fg_color=theme.WARN,
                                      hover_color=theme.WARN, text_color="#1a1300")
            self._learn_banner.config(
                text=f"ПРИВЯЗКА {uid.upper()} — нажмите контрол на устройстве")
            self._learn_banner.pack(side=tk.LEFT, padx=10)
            return
        self._learn_banner.pack_forget()
        self._learn_btn.configure(text="Привязать", fg_color=theme.SURFACE_2,
                                  hover_color=theme.SURFACE_3, text_color=theme.TEXT)
        el = self.ui_elements[uid]
        mid = el.get("midi_id")
        if mid is None:
            # BT / PAD BNK / KNOB BNK — служебные кнопки самого устройства.
            # Они переключают банк и блютус на железе и по MIDI обычно молчат,
            # так что «Привязать» на них может не поймать ничего.
            text = ("не привязан — служебная кнопка устройства"
                    if uid in ("btn_1", "btn_2", "btn_3") else "не привязан")
            self._learn_lbl.config(text=text, fg="#cc8888")
            return
        kind = el.get("midi_kind")
        if kind is None:
            kind = "note" if el["type"] == "pad" else "cc"
        self._learn_lbl.config(text=f"{'CC' if kind == 'cc' else 'нота'} {mid}",
                               fg="#88cc88")

    # ── Identify mode ─────────────────────────────────────────────────────────
    def _on_identify_toggle(self):
        self._identify_mode = self._identify_var.get()

    # ══════════════════════════════════════════════════════════════════════════
    #  ELEMENT SELECTION
    # ══════════════════════════════════════════════════════════════════════════
    def bind_click(self, main_widget, uid, *children):
        main_widget.bind("<Button-1>", lambda e, x=uid: self.select_element(x))
        for c in children:
            c.bind("<Button-1>", lambda e, x=uid: self.select_element(x))

    def select_element(self, uid):
        # Режим привязки принадлежит конкретному элементу. Если выделили
        # другой — прежнее ожидание снимается, иначе сигнал ушёл бы не туда,
        # причём молча: в инспекторе был бы виден уже новый элемент.
        if self._learn_uid and self._learn_uid != uid:
            self._learn_uid = None
        self.current_sel = uid
        el = self.ui_elements[uid]
        num = uid.split('_')[1]

        # Header text
        if el["type"] == "pad":
            name = f"ПЭД {num}"
        elif el["type"] == "knob":
            name = f"КРУТИЛКА {num}"
        else:
            name = f"КНОПКА {num}"
        self._insp_header.config(text=name)

        # Show content area
        self._insp_hint.pack_forget()
        self._insp_content.pack(fill=tk.BOTH, expand=True)

        # Highlight selection on device
        for k, v in self.ui_elements.items():
            if v["type"] in ["pad", "btn"]:
                v["outer"].config(bg=theme.ACCENT if k == uid else theme.SURFACE)
            elif v["type"] == "knob":
                v["num"].config(fg=theme.ACCENT if k == uid else theme.DIM)

        # Get binding
        bindings = appconfig.config["bindings"]
        b = bindings.get(uid, {})

        # Вкладки есть только у пэда. Крутилка и кнопка всегда на «Действии»:
        # вкладку ставим ДО блоков режима — они пакуются before=неё.
        if el["type"] == "pad":
            self._tab_seg.pack(fill=tk.X, pady=(6, 0), before=self._visible_tab)
        else:
            self._tab_seg.pack_forget()
            self._show_tab("Действие")

        # Show/hide knob mode switcher
        if el["type"] == "knob":
            mode = b.get("mode", "delta")
            self._knob_mode_var.set(mode)
            self._knob_mode_frame.pack(fill=tk.X, pady=(6, 0),
                                       before=self._visible_tab)
            if mode == "pair":
                self._pair_frame.pack(fill=tk.X, pady=(6, 0),
                                      before=self._visible_tab)
                self._update_pair_labels()
            else:
                self._pair_frame.pack_forget()
        else:
            self._knob_mode_frame.pack_forget()
            self._pair_frame.pack_forget()

        # Цвет — только у пэда; сама вкладка уже собрана, заполняем значения
        if el["type"] == "pad":
            col = b.get("color")
            if not (isinstance(col, str) and col.startswith("#")):
                col = DEFAULT_PAD_COLOR
            self._color_base = col
            self._color_hex_var.set(col)
            # Флаг — на случай, если ползунок дёрнет command на программную
            # установку. Без него простой клик по пэду переписывал бы его цвет
            # и слал кадр в эфир (так вёл себя прежний tk.Scale).
            self._suspend_brightness = True
            self._brightness_var.set(100)
            self._suspend_brightness = False
            self._update_color_hint()

        self._update_learn_row()

        # Populate tree
        current_action = b.get("action")
        self._populate_tree(select_action_id=current_action)

        # Show param if action has param_kind
        act = actions.get(current_action) if current_action else None
        self._show_param_for_action(act, b)

        # Clear execute error
        self._exec_error_lbl.config(text="")

    # ══════════════════════════════════════════════════════════════════════════
    #  HOTKEY RECORDING
    # ══════════════════════════════════════════════════════════════════════════
    def record_hotkey(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Назначение Хоткея")
        dlg.geometry("350x150")
        dlg.configure(bg="#2d2d30")
        dlg.transient(self.root)
        dlg.grab_set()
        tk.Label(dlg, text="Нажмите нужную комбинацию на клавиатуре",
                 bg="#2d2d30", fg="white", pady=15, font=("Segoe UI", 10)).pack()
        lbl = tk.Label(dlg, text="Ожидание...", font=("Segoe UI", 16, "bold"),
                       fg="#ffaa00", bg="#2d2d30")
        lbl.pack(pady=10)

        tk.Button(dlg, text="Отмена", command=dlg.destroy, bg="#444", fg="#fff",
                  bd=0, padx=10, font=("Segoe UI", 9)).pack()

        # Окну нужен фокус клавиатуры явно. grab_set() перехватывает только
        # мышь; нажатия шли в главное окно, пока пользователь не переключался
        # на другое окно и обратно. after — потому что на Windows фокус,
        # выданный до первой отрисовки, окно теряет.
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dlg.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dlg.winfo_height()) // 3
        dlg.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        dlg.lift()
        dlg.focus_force()
        dlg.after(50, dlg.focus_force)

        recorded = []
        def on_key(e):
            mods = _held_modifiers()
            key = _key_from_event(e)
            if key is None:   # нажат только модификатор — ждём основную клавишу
                lbl.config(text=_format_hotkey("+".join(mods)) + " + ...")
                return

            hotkey_str = "+".join(mods + [key])
            lbl.config(text=_format_hotkey(hotkey_str), fg="#00ff00")
            recorded.append(hotkey_str)
            dlg.unbind("<KeyPress>")
            dlg.after(500, dlg.destroy)

        dlg.bind("<KeyPress>", on_key)
        self.root.wait_window(dlg)
        return recorded[0] if recorded else None

    # ══════════════════════════════════════════════════════════════════════════
    #  CLEAR / RESET
    # ══════════════════════════════════════════════════════════════════════════
    # Привязка к железу — это не «настройка», а то, каким контролом является
    # элемент. Очистка действия не должна её сносить: иначе кнопку, которую
    # только что привязали вручную, придётся привязывать заново.
    _HW_KEYS = ("midi_id", "midi_kind")

    def _keep_hardware(self, binding):
        return {k: binding[k] for k in self._HW_KEYS if k in binding}

    def clear_current(self):
        if not self.current_sel:
            return
        bindings = appconfig.config["bindings"]
        if self.current_sel in bindings:
            bindings[self.current_sel] = self._keep_hardware(bindings[self.current_sel])
            self._save_config()
        self.select_element(self.current_sel)
        self.update_ui_from_config()

    def clear_all(self):
        if messagebox.askyesno(
                "Сброс",
                "Удалить назначения ВСЕХ элементов?\n\n"
                "Привязки к MIDI (какая кнопка какой контрол) сохранятся."):
            bindings = appconfig.config["bindings"]
            for uid in list(bindings):
                bindings[uid] = self._keep_hardware(bindings[uid])
            self._save_config()
            self.update_ui_from_config()
            if self.current_sel:
                self.select_element(self.current_sel)

    # ══════════════════════════════════════════════════════════════════════════
    #  UPDATE UI FROM CONFIG
    # ══════════════════════════════════════════════════════════════════════════
    def update_ui_from_config(self):
        bindings = appconfig.config["bindings"]
        for k, el in self.ui_elements.items():
            data = bindings.get(k, {})
            display = _element_display_text(data)

            if el["type"] == "pad":
                col = data.get("color")
                if not (isinstance(col, str) and col.startswith("#")):
                    col = DEFAULT_PAD_COLOR
                # Экран показывает то же, что уйдёт на пэд, вместе с общей
                # яркостью — иначе GUI был бы догадкой о железе.
                hex_col = _apply_brightness(
                    col, appconfig.config.get("pad_brightness", 100))
                fg = _text_color_for_bg(hex_col)
                el["frame"].config(bg=hex_col)
                el["lbl"].config(bg=hex_col, text=display, fg=fg)
                el["num"].config(bg=hex_col, fg=fg)
            elif el["type"] == "btn":
                # Значок кнопки живёт в отдельной метке и не затирается:
                # иначе после назначения не понять, какая это кнопка.
                el["lbl"].config(text=_ellipsize(
                                     display.replace("+\n", "+").replace("\n", " "), 17),
                                 fg="#0f0" if display else "#666")
            elif el["type"] == "knob":
                mode = data.get("mode", "delta")
                if mode == "pair":
                    ccw = actions.get(_side(data, "ccw").get("action") or "")
                    cw = actions.get(_side(data, "cw").get("action") or "")
                    parts = []
                    if ccw and ccw.id != "none":
                        parts.append(f"◀{ccw.short}")
                    if cw and cw.id != "none":
                        parts.append(f"▶{cw.short}")
                    el["lbl"].config(text=" ".join(parts)[:12] if parts else "",
                                     fg="#0f0" if parts else "#aaa")
                else:
                    el["lbl"].config(text=display[:10],
                                     fg="#0f0" if display else "#aaa")

    # ══════════════════════════════════════════════════════════════════════════
    #  HARDWARE MAPPING
    # ══════════════════════════════════════════════════════════════════════════
    def auto_assign_midi(self, midi_type, midi_id):
        for k, v in self.ui_elements.items():
            if v["midi_id"] == midi_id and v["type"] == midi_type:
                return k
        for k, v in self.ui_elements.items():
            if v["type"] == midi_type and v["midi_id"] is None:
                v["midi_id"] = midi_id
                bindings = appconfig.config["bindings"]
                if k not in bindings:
                    bindings[k] = {}
                bindings[k]["midi_id"] = midi_id
                appconfig.save_config()
                return k
        return None

    def load_hardware_mapping(self):
        bindings = appconfig.config["bindings"]
        for k, data in bindings.items():
            if k not in self.ui_elements:
                continue
            if "midi_id" in data:
                self.ui_elements[k]["midi_id"] = data["midi_id"]
            if "midi_kind" in data:
                self.ui_elements[k]["midi_kind"] = data["midi_kind"]
        if self.current_sel:
            self._update_learn_row()

    def flash_element(self, uid):
        if uid not in self.ui_elements:
            return
        el = self.ui_elements[uid]
        if el["type"] == "pad":
            orig_bg = el["frame"].cget("bg")
            el["frame"].config(bg="#ffffff")
            self.root.after(100, lambda: el["frame"].config(bg=orig_bg))
        elif el["type"] == "btn":
            # У кнопки внутри свои метки со своим фоном — заливка рамки
            # выглядела бы половинчато. Мигаем обводкой.
            orig_bg = el["outer"].cget("bg")
            el["outer"].config(bg="#ffffff")
            self.root.after(100, lambda: el["outer"].config(bg=orig_bg))

    def update_analog_ui(self, uid, val):
        pass  # Handled deeply in CC processing now

    # ══════════════════════════════════════════════════════════════════════════
    #  DEBUG
    # ══════════════════════════════════════════════════════════════════════════
    def _toggle_debug(self, event=None):
        self._debug_visible = not self._debug_visible
        if self._debug_visible:
            self._debug_frame.pack(fill=tk.X, side=tk.BOTTOM)
        else:
            self._debug_frame.pack_forget()

    def _on_tk_error(self, exc, val, tb):
        """Обработчик исключений Tk. Сам падать не имеет права."""
        try:
            lines = traceback.format_exception(exc, val, tb)
            self._debug_log("СБОЙ Tk: " + " | ".join(
                l.strip() for l in lines[-3:]))
            self.last_input_var.set(f"сбой: {val}"[:60])
        except Exception:
            pass

    def _debug_log(self, msg):
        self._debug_lines.append(msg)
        if len(self._debug_lines) > 50:
            self._debug_lines.pop(0)
        if self._debug_visible:
            self._debug_text.config(state=tk.NORMAL)
            self._debug_text.delete(1.0, tk.END)
            self._debug_text.insert(tk.END, "\n".join(self._debug_lines[-20:]))
            self._debug_text.see(tk.END)
            self._debug_text.config(state=tk.DISABLED)

    # ══════════════════════════════════════════════════════════════════════════
    #  CHECK QUEUE — main event loop
    # ══════════════════════════════════════════════════════════════════════════
    def _save_config(self):
        """Сохранить конфиг, не роняя вызывающего.

        save_config() зовётся прямо из обработчика MIDI. Исключение оттуда
        убивало цикл after() НАСОВСЕМ: устройство продолжало слать ноты,
        а приложение молча переставало их обрабатывать — «нажимаю пэд,
        ничего не происходит».
        """
        try:
            appconfig.save_config()
            return True
        except OSError as e:
            self._debug_log(f"конфиг не сохранён: {e}")
            self.last_input_var.set("конфиг не сохранён")
            return False

    def _run_action(self, uid, action_id, param=None, delta=0):
        """Выполнить действие и показать ошибку, а не проглотить её."""
        err = actions.execute(action_id, param, delta=delta)
        if not err:
            if action_id == "audio.volume":
                self._show_volume_osd()
            return
        self._debug_log(f"ОШИБКА {action_id}: {err}")
        self.last_input_var.set(f"{uid.upper()}: {err}"[:60])
        if uid == self.current_sel:
            self._exec_error_lbl.config(text=err[:40])

    def _show_volume_osd(self):
        """Индикатор громкости взамен системного.

        Уровень пишется в микшер напрямую, а флайаут Windows рисует только в
        ответ на медиа-клавишу — своего вызова у него нет. Сбой индикатора не
        должен ронять саму регулировку, поэтому всё под перехватом.

        После первого же сбоя индикатор отключается до перезапуска: крутилка
        шлёт десятки событий в секунду, и молчаливые повторные попытки залили
        бы лог и грузили систему на каждом щелчке.
        """
        if self._volume_osd is False:
            return
        level, _ = actions.volume_state()
        if level is None:
            return
        try:
            if self._volume_osd is None:
                self._volume_osd = volume_osd.VolumeOSD(self.root)
            self._volume_osd.show(level)
        except Exception:
            self._volume_osd = False
            self._debug_log("СБОЙ индикатора, отключён до перезапуска: " +
                            traceback.format_exc().strip().splitlines()[-1])

    def _set_bank(self, bank):
        if bank == self._current_bank:
            return
        self._current_bank = bank
        self._bank_var.set(f"Банк {bank}")
        # Адрес записи цвета зависит от банка: запись в чужой банк ACK-ается
        # и молча ничего не делает.
        ble.set_bank(bank)

    def _remember_midi_id(self, uid, midi_id):
        """Запомнить, какой CC/ноту реально прислал элемент."""
        if self.ui_elements[uid].get("midi_id") == midi_id:
            return
        self.ui_elements[uid]["midi_id"] = midi_id
        bindings = appconfig.config["bindings"]
        if uid not in bindings:
            bindings[uid] = {}
        bindings[uid]["midi_id"] = midi_id
        self._save_config()
        if uid == self.current_sel:
            self._update_learn_row()

    def _learned_uid(self, kind, midi_id):
        """Элемент с ручной привязкой к этому CC/ноте.

        Явная привязка пользователя главнее статических карт нот и CC:
        у кнопок справа карты нет вообще, а угадывать — это как раз то,
        что уже испортило midi_id в боевом конфиге.
        """
        for uid, el in self.ui_elements.items():
            if el.get("midi_kind") == kind and el.get("midi_id") == midi_id:
                return uid
        return None

    def _capture_learn(self, kind, midi_id):
        uid = self._learn_uid
        self._learn_uid = None
        if uid not in self.ui_elements:
            return
        # Один и тот же CC не может висеть на двух элементах.
        for other, el in self.ui_elements.items():
            if other != uid and el.get("midi_kind") == kind and el.get("midi_id") == midi_id:
                el["midi_id"] = None
                el["midi_kind"] = None
                ob = appconfig.config["bindings"].get(other)
                if ob:
                    ob.pop("midi_id", None)
                    ob.pop("midi_kind", None)
        self.ui_elements[uid]["midi_id"] = midi_id
        self.ui_elements[uid]["midi_kind"] = kind
        bindings = appconfig.config["bindings"]
        if uid not in bindings:
            bindings[uid] = {}
        bindings[uid]["midi_id"] = midi_id
        bindings[uid]["midi_kind"] = kind
        self._save_config()
        label = "CC" if kind == "cc" else "нота"
        self._last_unbound = None
        self._learn_banner.pack_forget()
        self.last_input_var.set(f"{uid.upper()} ← {label} {midi_id}")
        self._debug_log(f"привязано: {uid} ← {label} {midi_id}")
        if uid == self.current_sel:
            self._update_learn_row()

    def _pad_for_note(self, note):
        info = _NOTE_TO_PAD.get(note)
        if info is None:
            return None
        pad_num, bank = info
        uid = f"pad_{pad_num}"
        if uid not in self.ui_elements:
            return None
        self._set_bank(bank)
        self._remember_midi_id(uid, note)
        return uid

    def _knob_for_cc(self, cc):
        knob_num = CC_TO_KNOB.get(cc)
        if knob_num is None:
            return None
        uid = f"knob_{knob_num}"
        if uid not in self.ui_elements:
            return None
        self._remember_midi_id(uid, cc)
        return uid

    def _handle_trigger(self, uid, kind, midi_id, val):
        """Пэд или кнопка: одно нажатие — одно действие."""
        # Кнопка на CC: нажатие — большое значение, отпускание — малое.
        pressed = val >= 64 if kind == "cc" else val > 0
        self._debug_log(f"{kind.upper()} {midi_id}={val} uid={uid} "
                        f"{'нажатие' if pressed else 'отпускание'}")
        if not pressed:
            return
        label = f"CC{midi_id}" if kind == "cc" else f"[{midi_id}]"
        self.last_input_var.set(f"{uid.upper()} {label}")
        self.flash_element(uid)

        if self._identify_mode:
            self.select_element(uid)
            return

        now = time.time()
        if now - self._last_note_time.get(uid, 0) < 0.08:   # дребезг 80 мс
            return
        self._last_note_time[uid] = now

        b = appconfig.config["bindings"].get(uid, {})
        action_id = b.get("action", "none")
        if action_id == "none":
            self.last_input_var.set(f"{uid.upper()} {label} — действие не назначено")
            return
        self._run_action(uid, action_id, b.get("param"))

    def _handle_knob(self, uid, midi_id, val):
        """Крутилка.

        M-Vave SMC-PAD шлёт АБСОЛЮТНЫЕ значения CC (0→1→2→3 по часовой,
        3→2→1→0 против), а не относительные дельты. Дельта считается как
        разница с предыдущим значением, заворот 0↔127 — через порог ±64.
        Не «улучшать»: три предыдущих декодера были неверны, см. FIXES.md.
        """
        self.last_input_var.set(f"{uid.upper()} [CC{midi_id}]: {val}")

        prev = self._prev_knob_cc.get(uid)
        if prev is not None:
            delta = val - prev
            if delta > 64:
                delta -= 128
            elif delta < -64:
                delta += 128
        else:
            delta = 0    # первое сообщение — только базовая точка, без действия
        self._prev_knob_cc[uid] = val
        self.raw_knob_values[uid] = val

        self._debug_log(f"CC {midi_id}={val} delta={delta} uid={uid}")
        if delta == 0:
            return

        self.ui_elements[uid]["canvas"].rotate(delta)

        if self._identify_mode:
            self.select_element(uid)
            return

        b = appconfig.config["bindings"].get(uid, {})
        if b.get("mode", "delta") == "pair":
            side = _side(b, "cw" if delta > 0 else "ccw")
            act_id = side.get("action") or "none"
            if act_id != "none":
                self._run_action(uid, act_id, side.get("param"))
        else:
            action_id = b.get("action", "none")
            if action_id != "none":
                self._run_action(uid, action_id, b.get("param"), delta=delta)

    def _on_midi(self, kind, midi_id, val):
        if self._learn_uid:
            # Учим только на нажатии: отпускание шлёт 0 и привязало бы
            # элемент к событию отпускания.
            if val > 0:
                self._capture_learn(kind, midi_id)
            return

        uid = self._learned_uid(kind, midi_id)
        if uid is None:
            uid = self._pad_for_note(midi_id) if kind == "note" \
                else self._knob_for_cc(midi_id)

        if uid is None:
            # Молчание здесь и было жалобой «кнопки не откликаются»:
            # неизвестный CC просто исчезал без следа. Теперь он ещё и
            # запоминается — чтобы повесить его на элемент можно было уже
            # после нажатия, не ловя момент.
            label = "CC" if kind == "cc" else "нота"
            self._last_unbound = (kind, midi_id)
            self.last_input_var.set(f"{label} {midi_id} — не привязан")
            self._debug_log(f"{label} {midi_id}={val}: не привязан. "
                            f"Выделите элемент → «повесить сюда {label} {midi_id}».")
            if self.current_sel:
                self._update_learn_row()
            return

        if self.ui_elements[uid]["type"] == "knob":
            self._handle_knob(uid, midi_id, val)
        else:
            self._handle_trigger(uid, kind, midi_id, val)

    def _pump_queue(self):
        while True:
            msg = ble.msg_queue.get_nowait()

            if msg.startswith("status:"):
                self.status_var.set(msg.split(":", 1)[1])

            elif msg.startswith("battery:"):
                try:
                    self._set_battery(int(msg.split(":", 1)[1]))
                except ValueError:
                    pass

            elif msg.startswith("color:"):
                self._color_ok = msg.split(":", 1)[1] == "есть"
                self._color_status_var.set(
                    "цвет: идёт на устройство" if self._color_ok
                    else "цвет: только на экране")
                self._update_color_hint()

            elif msg.startswith("state:"):
                parts = msg.split(":")
                if len(parts) == 3 and parts[2].isdigit():
                    self._set_bank(int(parts[2]))

            elif msg.startswith("note:"):
                parts = msg.split(":")
                self._on_midi("note", int(parts[1]), int(parts[2]))

            elif msg.startswith("cc:"):
                parts = msg.split(":")
                self._on_midi("cc", int(parts[1]), int(parts[2]))

    # ── Tray ──────────────────────────────────────────────────────────────────
    def hide_to_tray(self):
        self.root.withdraw()

    def show_from_tray(self):
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.focus_force()

    def _on_unmap(self, event):
        # <Unmap> приходит и от дочерних виджетов — нужен только сам root,
        # и только сворачивание (withdraw тоже шлёт Unmap, state тогда withdrawn).
        if event.widget is self.root and self.root.state() == "iconic":
            self.root.after_idle(self.root.withdraw)

    def _pump_tray(self):
        ev = getattr(self, "_show_event", None)
        if ev:
            import ctypes
            # 0 = WAIT_OBJECT_0: повторный запуск позвонил — показать окно
            if ctypes.windll.kernel32.WaitForSingleObject(ctypes.c_void_p(ev), 0) == 0:
                self.show_from_tray()
        while True:
            try:
                cmd = self._tray.commands.get_nowait()
            except queue.Empty:
                return
            if cmd == tray.SHOW:
                self.show_from_tray()
            elif cmd == tray.QUIT:
                self._tray.stop()
                on_closing(self.root)

    def check_queue(self):
        try:
            self._pump_tray()
            self._pump_queue()
        except queue.Empty:
            pass
        except Exception:
            # Любое другое исключение раньше убивало цикл after() навсегда —
            # приложение выглядело живым, но MIDI больше не обрабатывался.
            # Теперь сбой виден в Ctrl+D, а цикл продолжает работать.
            self._debug_log("СБОЙ: " + " | ".join(
                traceback.format_exc().strip().splitlines()[-3:]))
            self.last_input_var.set("сбой обработки — Ctrl+D")
        finally:
            self.root.after(20, self.check_queue)


def _hide_console():
    """Спрятать окно консоли.

    Приложение запускают ярлыком, чёрное окно позади интерфейса только мешает.
    Трейсбеки при этом не теряются: check_queue пишет их в панель Ctrl+D.
    Прятать надо всегда, а не полагаться на pythonw в ярлыке — ярлык уже
    один раз протух (см. create_shortcut.vbs).
    """
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)   # SW_HIDE
    except Exception:
        pass


def _silence_missing_streams():
    """Под pythonw sys.stdout и sys.stderr равны None.

    Любой оставшийся print или чужая библиотека, пишущая в stderr, роняет
    вызывающего на AttributeError. Подставляем заглушку, чтобы это было
    молчанием, а не падением посреди обработчика.
    """
    import io
    import sys as _sys
    for name in ("stdout", "stderr"):
        if getattr(_sys, name, None) is None:
            setattr(_sys, name, io.StringIO())


_SHOW_EVENT_NAME = "Local\\MvaveSmcPad.Show"


def _claim_single_instance():
    """Именованное событие Windows — одновременно флаг «уже запущено» и звонок.

    Второй экземпляр не только выходит, но и будит первый: окно того лежит
    в трее, и без этого повторный клик по ярлыку ничего видимого не делал бы.
    Возвращает хэндл события для опроса или None, если запуск лишний.
    Событие ядро снимает само, когда процесс умирает, в том числе через
    os._exit — зависшего «уже запущено» после падения не бывает.
    """
    try:
        import ctypes
        # use_last_error: у общего windll код ошибки затирают вызовы самого ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateEventW.restype = ctypes.c_void_p
        h = k32.CreateEventW(None, False, False, _SHOW_EVENT_NAME)  # auto-reset
        if h and ctypes.get_last_error() == 183:    # ERROR_ALREADY_EXISTS
            # Разрешить первому экземпляру вытащить окно на передний план:
            # иначе Windows только мигнёт кнопкой, фокус останется у нас.
            ctypes.windll.user32.AllowSetForegroundWindow(-1)   # ASFW_ANY
            k32.SetEvent(ctypes.c_void_p(h))
            return None
        return h or False
    except Exception:
        return False   # проверка недоступна — запускаемся как раньше


def main():
    _silence_missing_streams()
    _hide_console()
    show_event = _claim_single_instance()
    if show_event is None:
        return
    root = tk.Tk()
    app = App(root)
    app._show_event = show_event or None
    app.load_hardware_mapping()
    t = threading.Thread(target=ble.start_ble_thread, daemon=True)
    t.start()
    root.mainloop()

if __name__ == "__main__":
    main()
