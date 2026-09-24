import tkinter as tk
from tkinter import ttk, filedialog
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
from mvave import dialogs
from mvave import presets
from mvave import knob_art
from mvave import winplace
from mvave import autostart

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
# Крайние значения CC крутилки → шаг, если контроллер повторяет их на упоре
RAIL = {0: -1, 127: +1}

# Транспортные кнопки корпуса (btn_4..btn_8): назад, вперёд, play, пауза,
# запись. Надписей на корпусе нет — в программе значок и латинское слово.
BTN_GLYPHS = {4: "\ue892", 5: "\ue893", 6: "\ue768", 7: "\ue769", 8: "\ue7c8"}
BTN_WORDS = {4: "PREV", 5: "NEXT", 6: "PLAY", 7: "PAUSE", 8: "REC"}

BANK_HINT = (
    "Банки переключаются кнопками на корпусе.\n"
    "PAD BANK меняет диапазон нот пэдов: банков 8 (4-й и 8-й шлют одни и те же "
    "ноты). KNOB BANK переключает крутилки между двумя наборами CC.\n"
    "Программа узнаёт банк по пришедшей ноте или CC. Номер банка пэдов нужен ей "
    "самой: от него зависит адрес, по которому цвет записывается на пэд.\n"
    "Назначения одинаковы во всех банках."
)

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


def _pair_locked(binding):
    """Замок сторон закрыт? Явное значение pair_lock главнее.

    Старый конфиг без ключа: замок закрыт, только если стороны уже
    противоположны (или обе пусты). Иначе открыт — чужую настройку, где
    стороны заданы независимо, замок не переписывает.
    """
    b = binding or {}
    if "pair_lock" in b:
        return bool(b["pair_lock"])
    ccw = _side(b, "ccw").get("action")
    cw = _side(b, "cw").get("action")
    if not ccw and not cw:
        return True
    return bool(ccw) and actions.opposite(ccw) == cw


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
    """Endless encoder visual — indicator rotates freely, no min/max.

    Кадр — сглаженная картинка из mvave.knob_art: tk-овалы рисовали кольцо
    ступеньками. Угол не ограничен, кадр берётся по angle % 360.
    """
    def __init__(self, parent, size=50, bg_col=None):
        self._bg = bg_col or theme.SURFACE
        super().__init__(parent, width=size, height=size, bg=self._bg,
                         highlightthickness=0)
        self.size = size
        self.angle = 225.0  # Current angle in degrees (starts at 7 o'clock)
        # Цвета читаются при создании: крутилки пересоздаются при смене темы
        self._colors = (self._bg, theme.KNOB_BODY, theme.KNOB_RING,
                        theme.KNOB_TICK, theme.ACCENT)
        self._img_id = self.create_image(0, 0, anchor="nw")
        self.draw()

    def rotate(self, delta):
        """Rotate by delta encoder clicks. Positive=CW(right), negative=CCW(left)."""
        self.angle -= delta * 5  # 5 degrees per click — smooth visual rotation
        self.draw()

    def draw(self):
        self.itemconfigure(self._img_id, image=knob_art.frame(
            self, self.size, self.angle, *self._colors))


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
        # Размер и место ставит _place_window после сборки: минимум считается
        # от содержимого, а не числом (при 1320x780 низ инспектора срезался).
        self._geom_job = None
        self._placed = False
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

        self.ui_elements = {}
        self.current_sel = None
        self.raw_knob_values = {}
        self._prev_knob_cc = {}  # uid → previous absolute CC value for delta calc
        self._volume_osd = None  # индикатор громкости: None — ещё не создан, False — сбой
        self._current_bank = 3  # default bank
        self._knob_bank = 1     # по CC крутилок: 30-37 — 1, 38-45 — 2
        self._identify_mode = False
        self._learn_uid = None   # элемент, ждущий привязки к CC/ноте
        self._color_ok = False   # вендорский канал цвета доступен
        self._color_known = False  # устройство уже ответило, есть ли канал
        self._color_base = ""    # цвет без яркости: ползунок не должен
                                 # умножать сам на себя при каждом движении
        self._pad_dim_job = None   # отложенная отправка общей яркости
        self._last_unbound = None  # ("cc"|"note", номер) — последнее сообщение,
                                   # которое не нашло хозяина

        # Clipboard provider for actions.text.type
        actions.set_clipboard_provider(_TkClipboard(root))
        actions.set_pad_brightness_provider(self._nudge_pad_brightness)

        self._apply_theme(appconfig.config.get("theme", "dark"))
        self.build_ui()
        self._place_window()
        self.root.bind("<Configure>", self._on_root_configure, add="+")

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

    def _lbl(self, parent, text="", fg=None, font=None, **kw):
        # Цвет по умолчанию читается при вызове: значение по умолчанию в
        # сигнатуре застыло бы на теме, активной при импорте модуля.
        return tk.Label(parent, text=text, fg=fg or theme.TEXT, bg=parent.cget("bg"),
                        font=font or theme.F_SMALL, **kw)

    def _btn(self, parent, text, command, kind="secondary", **kw):
        kw.setdefault("height", 30)
        return dialogs.button(parent, text, command, kind=kind, **kw)

    # ── Тема ──────────────────────────────────────────────────────────────────
    def _apply_theme(self, mode):
        theme.set_mode(mode)
        ctk.set_appearance_mode(theme.applied)
        theme.apply_ttk(self.root)
        self.root.configure(bg=theme.BG)
        theme.paint_titlebar(self.root)

    def set_theme(self, mode):
        """Сменить тему на ходу: пересобрать окно, сохранив состояние."""
        if mode not in theme.MODES:
            return
        appconfig.config["theme"] = mode
        self._save_config()
        if theme.resolve(mode) == theme.applied and mode == theme.mode:
            return
        self._apply_theme(mode)
        self.rebuild_ui()

    def rebuild_ui(self):
        """Уничтожить и заново собрать интерфейс.

        Виджеты читают цвета темы при создании, перекрашивать каждый на ходу
        — значит держать реестр «виджет → свойство → токен» и не забыть ни
        одного. Пересборка проще и полнее; выделение, вкладка пэда и режим
        Ctrl+D возвращаются явно. Трей, BLE-поток и очереди не трогаются:
        они не виджеты.
        """
        sel = self.current_sel
        tab = self._tab_seg.get() if hasattr(self, "_tab_seg") else "Действие"
        debug = getattr(self, "_debug_visible", False)
        lines = list(getattr(self, "_debug_lines", []))
        status, battery = self.status_var.get(), self._battery_pct
        last_input = self.last_input_var.get()
        if self._pad_dim_job is not None:
            self.root.after_cancel(self._pad_dim_job)
            self._pad_dim_job = None
            self._save_config()
        dialogs.PopupMenu.close_current()
        for w in self.root.winfo_children():
            w.destroy()
        # CTk-виджет подменяет config/configure у tk-родителя, чтобы тот
        # пересылал ему смену фона. Виджет уничтожен, подмена осталась — и
        # следующий root.configure(bg=…) падал на мёртвом «.!ctkframe».
        for attr in ("config", "configure"):
            self.root.__dict__.pop(attr, None)
        # Индикатор громкости — тоже дочернее окно root, он пересоздастся сам
        if self._volume_osd is not False:
            self._volume_osd = None
        self.ui_elements = {}
        self.current_sel = None
        self._learn_uid = None
        self.build_ui()
        self._debug_lines = lines
        if debug:
            self._toggle_debug()
        self.status_var.set(status)
        self.last_input_var.set(last_input)
        self._set_battery(battery if battery is not None else -1)
        self._bank_var.set(self._bank_text())
        self.load_hardware_mapping()
        self.update_ui_from_config()
        if sel in self.ui_elements:
            self.select_element(sel)
            if self.ui_elements[sel]["type"] == "pad":
                self._show_tab(tab)

    def build_ui(self):
        self.root.configure(bg=theme.BG)
        self._firmware_btns = []

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

        # Банки — справка, а не тревога: нейтральная подпись без плашки.
        self._bank_var = tk.StringVar(value=self._bank_text())
        bank_lbl = tk.Label(row1, textvariable=self._bank_var, fg=theme.MUTED,
                            bg=theme.SURFACE, font=theme.F_SMALL)
        bank_lbl.pack(side=tk.LEFT, padx=(18, 0))
        ToolTip(bank_lbl, lambda: BANK_HINT)

        if appconfig.last_error:
            self._lbl(row1, appconfig.last_error, fg=theme.WARN).pack(
                side=tk.LEFT, padx=10)

        self._identify_var = tk.BooleanVar(value=False)
        # Пока включён, нажатие контрола выделяет его на схеме и НЕ выполняет
        # действие — так ищут, где на схеме физическая кнопка.
        pick_sw = ctk.CTkSwitch(row1, text="Выбор нажатием", variable=self._identify_var,
                                onvalue=True, offvalue=False,
                                command=self._on_identify_toggle,
                                font=theme.C_SMALL, text_color=theme.MUTED,
                                progress_color=theme.ACCENT, button_color="#ffffff",
                                button_hover_color="#ffffff",
                                fg_color=theme.SURFACE_3, switch_width=34, switch_height=18)
        pick_sw.pack(side=tk.LEFT, padx=(18, 0))
        ToolTip(pick_sw, lambda: "Нажмите контрол на устройстве — он выделится "
                                 "справа, действие не выполнится.")

        # Пока ждём сигнал для привязки — это видно в шапке, а не только
        # мелкой строкой в инспекторе.
        self._learn_banner = tk.Label(row1, text="", fg="#1a1300", bg=theme.WARN,
                                      font=theme.F_SMALL + ("bold",), padx=10, pady=2)

        self._settings_btn = self._btn(row1, "Настройки  ▾", self._open_settings_menu,
                                       width=124, height=32)
        self._settings_btn.pack(side=tk.RIGHT)
        self._help_btn = dialogs.icon_button(row1, "\ue946", self._open_help, size=32)
        self._help_btn.pack(side=tk.RIGHT, padx=(0, 8))
        ToolTip(self._help_btn, lambda: "Справка")

        self.last_input_var = tk.StringVar(value="")
        self._lbl(row1, fg=theme.MUTED, font=theme.F_SMALL,
                  textvariable=self.last_input_var).pack(side=tk.RIGHT, padx=(0, 16))

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
            progress_color=theme.ACCENT, button_color=theme.ACCENT,
            button_hover_color=theme.ACCENT_HOVER, fg_color=theme.SURFACE_3)
        self._pad_dim_scale.pack(side=tk.LEFT)
        self._pad_dim_lbl = self._lbl(row2, "", fg=theme.TEXT, font=theme.F_BOLD, width=5)
        self._pad_dim_lbl.pack(side=tk.LEFT, padx=(8, 0))
        self._pad_dim_lbl.config(text=f"{self._pad_dim_var.get()}%")

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

            lbl_num = tk.Label(f, text=f"PAD {num}", fg="#777", bg="#222",
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

        # Колонка — в порядке корпуса. Служебные кнопки (BT, банки, Shift,
        # Note Repeat) обрабатывает прошивка: в компьютер они не передают
        # ничего, на что можно повесить действие (замер 2026-09-23,
        # docs/PROTOCOL.md §1). Они серые и не выбираются, объяснение — в
        # подсказке. uid рабочих кнопок остались btn_4..btn_8: на них
        # держатся назначения в конфигах и пресетах.
        fw = "Её обрабатывает сам контроллер, в компьютер она ничего не передаёт — " \
             "назначить на неё действие нельзя."
        btn_defs = [
            ("fw", "BT", "Bluetooth. " + fw),
            ("fw", "PAD BANK", "Банк пэдов. " + fw + " Номер банка программа узнаёт "
                               "по нотам и показывает в шапке."),
            ("fw", "KNOB BANK", "Банк крутилок. " + fw),
            # Значки — Segoe MDL2 Assets. Назад / вперёд — «предыдущий /
            # следующий» (|◀ ▶|): одиночные ◀ ▶ путались с play.
            (4, BTN_GLYPHS[4], None),
            (5, BTN_GLYPHS[5], None),
            (6, BTN_GLYPHS[6], None),
            (7, BTN_GLYPHS[7], None),
            (8, BTN_GLYPHS[8], None),
            ("fw", "SHIFT", "Shift. " + fw + " Shift+пэд — пресеты, чувствительность, октава."),
            ("fw", "NOTE REPEAT", "Note Repeat. " + fw),
        ]
        for num, icon, tip in btn_defs:
            if num == "fw":
                self._firmware_button(btn_panel, icon, tip)
                continue
            uid = f"btn_{num}"
            bg = theme.BTN_BG
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
            # Номер не пишется: служебные кнопки выпали из нумерации, и «4»
            # у первой рабочей кнопки только сбивал бы. Кнопку опознаёт значок.
            lbl_num = tk.Label(f, text="", fg=theme.DIM, bg=bg, font=(theme.FONT, 7))
            lbl_num.pack(side=tk.LEFT, padx=(8, 0))
            lbl_icon = tk.Label(f, text=icon, fg=theme.BTN_ICON, bg=bg, anchor="w",
                                font=(theme.ICON_FONT, 12))
            lbl_icon.pack(side=tk.LEFT)
            lbl_act = tk.Label(f, text="", fg=theme.OK, bg=bg, anchor="e",
                               font=theme.F_TINY)
            lbl_act.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(3, 8))

            self.ui_elements[uid] = {"outer": outer, "frame": f, "lbl": lbl_act,
                                     "num": lbl_num, "icon_lbl": lbl_icon,
                                     "type": "btn", "midi_id": None,
                                     "midi_kind": None, "icon": icon}
            self.bind_click(outer, uid, f, lbl_num, lbl_icon, lbl_act)
            self._attach_tooltip(outer, uid)

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
        # Ctrl+D по коду клавиши, а не по символу: в русской раскладке Tk
        # присылает «в» (Cyrillic_ve), и привязка <Control-d> молчала.
        self.root.bind("<Control-KeyPress>", self._on_ctrl_key)

    def _firmware_button(self, parent, text, tip):
        """Серая кнопка схемы, которую обрабатывает прошивка: не выбирается."""
        outer = tk.Frame(parent, bg=theme.SURFACE, padx=1, pady=1)
        outer.pack(pady=2)
        f = tk.Frame(outer, width=172, height=30, bg=theme.FW_BG)
        f.pack()
        f.pack_propagate(False)
        lbl = tk.Label(f, text=text, fg=theme.FW_TEXT, bg=theme.FW_BG, anchor="w",
                       font=(theme.FONT, 8, "bold"))
        lbl.pack(side=tk.LEFT, padx=(12, 0))
        ToolTip(outer, lambda: tip)
        self._firmware_btns.append(text)

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
        if dialogs.PopupMenu._current is not None:
            dialogs.PopupMenu.close_current()
            return
        m = dialogs.PopupMenu(self.root, width=272)
        m.item("Пресеты", self._open_presets, "\ue8f1")
        m.item("Сохранить как пресет", self._on_save_preset, "\ue74e")
        m.separator()
        m.item("Экспорт в файл", self._on_export, "\ue898")
        m.item("Импорт из файла", self._on_import, "\ue896")
        m.item("Открыть папку с настройками", self._on_open_config_dir, "\ue838")
        m.item("Ярлык на рабочем столе", self._on_desktop_shortcut, "\ue8a7")
        m.separator()
        self._autostart_row(m.body)
        m.separator()
        m.caption("Тема")
        names = {v: k for k, v in theme.MODE_NAMES.items()}

        def pick(label):
            m.close()
            self.root.after(30, lambda: self.set_theme(names[label]))
        seg = self._segmented(m.body, [theme.MODE_NAMES[k] for k in theme.MODES], pick)
        seg.configure(font=theme.C_TINY, height=28)
        seg.set(theme.MODE_NAMES[theme.mode])
        seg.pack(fill=tk.X, padx=8, pady=(0, 8))
        m.show_below(self._settings_btn)

    # ── Автозапуск и ярлык ────────────────────────────────────────────────────
    def _autostart_row(self, parent):
        """Переключатель автозапуска. Состояние читается из реестра при каждом
        открытии меню: значение могли удалить снаружи, а exe — перенести."""
        E = theme.ELEVATED
        row = tk.Frame(parent, bg=E)
        row.pack(fill=tk.X, padx=12, pady=(4, 2))
        st = autostart.state()
        self._autostart_var = tk.BooleanVar(value=st == "on")
        ctk.CTkSwitch(row, text="Запускать вместе с Windows", variable=self._autostart_var,
                      command=self._on_autostart_toggle, font=theme.C_SMALL,
                      text_color=theme.TEXT, progress_color=theme.ACCENT,
                      button_color="#ffffff", button_hover_color="#ffffff",
                      fg_color=theme.SURFACE_3, switch_width=36,
                      switch_height=18).pack(side=tk.LEFT)
        note = ("Записан другой путь — включи, чтобы запускать эту копию"
                if st == "other" else "Окно откроется спрятанным в трей")
        tk.Label(parent, text=note, fg=theme.WARN if st == "other" else theme.MUTED,
                 bg=E, font=theme.F_TINY, anchor="w", justify=tk.LEFT,
                 wraplength=240).pack(fill=tk.X, padx=12, pady=(0, 4))

    def _on_autostart_toggle(self):
        want = bool(self._autostart_var.get())
        try:
            st = autostart.set_autostart(want)
        except OSError as e:
            self._autostart_var.set(not want)
            dialogs.toast(self.root, f"Автозапуск не изменён: {e}", kind="error")
            return
        if (st == "on") != want:
            self._autostart_var.set(st == "on")
            dialogs.toast(self.root, "Автозапуск не записался в реестр", kind="error")
            return
        dialogs.toast(self.root, "Автозапуск включён" if want else "Автозапуск выключен")
        legacy = autostart.legacy_startup_shortcut()
        if want and legacy:
            # Старый ярлык из «Автозагрузки» вместе с реестром запустил бы
            # программу дважды. Удалять — только с согласия.
            dialogs.PopupMenu.close_current()
            if dialogs.confirm(self.root, "Удалить старый ярлык автозагрузки?",
                               f"Раньше автозапуск шёл через ярлык:\n{legacy}\n\n"
                               "Теперь он записан в реестр, а ярлык запустил бы "
                               "программу второй раз.", ok_text="Удалить"):
                try:
                    os.remove(legacy)
                except OSError as e:
                    dialogs.alert(self.root, "Ярлык не удалён", str(e), kind="error")

    def _on_desktop_shortcut(self):
        try:
            path = autostart.create_desktop_shortcut()
        except Exception as e:
            dialogs.alert(self.root, "Ярлык не создан", str(e), kind="error")
            return
        dialogs.toast(self.root, "Ярлык создан: " + os.path.basename(path))

    def _after_config_replaced(self):
        """Настройки заменены целиком — привести окно и железо к ним."""
        self._pad_dim_var.set(int(appconfig.config.get("pad_brightness", 100)))
        self._pad_dim_lbl.config(text=f"{self._pad_dim_var.get()}%")
        self._build_palette()
        self.load_hardware_mapping()
        self.update_ui_from_config()
        if self.current_sel:
            self.select_element(self.current_sel)
        self._on_resend_colors()

    def _load_config_file(self, path, name, remember=False):
        """Общий путь «Открыть пресет» и «Импорт»: вопрос → загрузка → отчёт.

        remember — положить копию файла в пресеты, но только после успешной
        загрузки: битый файл в списке пресетов не нужен.
        """
        if not dialogs.confirm(
                self.root, f"Открыть «{name}»?",
                "Назначения, цвета и яркость заменятся настройками пресета. "
                "Текущие сохранятся в резервную копию.", ok_text="Открыть"):
            return False
        ok, report = presets.load_preset(path)
        if not ok:
            dialogs.alert(self.root, "Настройки не загружены", report, kind="error")
            return False
        if remember:
            # Второй раз файл откроется из списка пресетов, без поиска
            try:
                presets.remember_file(path)
            except OSError as e:
                self._debug_log(f"копия в пресеты не сделана: {e}")
        self._after_config_replaced()
        missing = report.split("\n\n", 1)
        if len(missing) > 1:
            # Пути программ и папок, которых нет на этом компьютере
            dialogs.alert(self.root, f"«{name}» открыт",
                          missing[1], kind="warn")
        else:
            dialogs.toast(self.root, f"Открыт пресет «{name}»")
        return True

    def _on_save_preset(self, initial=None):
        name = dialogs.ask_text(
            self.root, "Сохранить как пресет", "Название",
            initial=initial or time.strftime("Пресет %d.%m.%Y"), ok_text="Сохранить")
        if not name:
            return None
        name = presets.clean_name(name)
        if not name:
            dialogs.alert(self.root, "Пресет не сохранён",
                          "В названии нет ни одного допустимого символа.", kind="warn")
            return None
        if presets.exists(name) and not dialogs.confirm(
                self.root, "Заменить пресет?",
                f"Пресет «{name}» уже есть. Заменить его текущими настройками?",
                ok_text="Заменить"):
            return None
        try:
            presets.save_preset(name)
        except (OSError, ValueError) as e:
            dialogs.alert(self.root, "Пресет не сохранён", str(e), kind="error")
            return None
        dialogs.toast(self.root, f"Пресет «{name}» сохранён")
        return name

    def _open_help(self):
        """Справка: разделы из mvave.help_text в прокручиваемом окне."""
        from mvave import help_text
        E = theme.ELEVATED
        m = dialogs.Modal(self.root, "Справка", width=560)
        area = ctk.CTkScrollableFrame(
            m.body, fg_color=E, height=min(460, int(self.root.winfo_height() * 0.6)),
            scrollbar_button_color=theme.SURFACE_3,
            scrollbar_button_hover_color=theme.DIM, corner_radius=0)
        area.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        wrap = 560 - 2 * dialogs.PAD - 40
        for i, (title, paras) in enumerate(help_text.SECTIONS, 1):
            tk.Label(area, text=f"{i}. {title}", fg=theme.TEXT, bg=E,
                     font=theme.F_BOLD, anchor="w").pack(fill=tk.X, pady=(12 if i > 1 else 0, 4))
            for p in paras:
                tk.Label(area, text=p, fg=theme.MUTED, bg=E, font=theme.F_SMALL,
                         anchor="w", justify=tk.LEFT, wraplength=wrap).pack(
                    fill=tk.X, pady=(0, 6))
        self._help_modal = m
        m.buttons([("Закрыть", None, "primary")])
        m.show()
        self._help_modal = None

    def _open_presets(self):
        """Окно со списком пресетов: открыть или удалить без выбора файла."""
        m = dialogs.Modal(self.root, "Пресеты", width=460)
        top = tk.Frame(m.body, bg=theme.ELEVATED)
        top.pack(fill=tk.X, pady=(12, 12))
        dialogs.label(top, "Сохранённые и загруженные раньше настройки.",
                      fg=theme.MUTED, font=theme.F_SMALL, anchor="w").pack(side=tk.LEFT)

        holder = tk.Frame(m.body, bg=theme.ELEVATED)
        holder.pack(fill=tk.BOTH, expand=True)

        def fill():
            for w in holder.winfo_children():
                w.destroy()
            items = presets.list_presets()
            if not items:
                dialogs.label(holder, "Пока пусто. Сохраните текущие настройки "
                                      "кнопкой ниже.", fg=theme.MUTED,
                              font=theme.F_BODY, anchor="w", justify=tk.LEFT,
                              wraplength=400).pack(fill=tk.X, pady=(4, 8))
                return
            area = holder
            if len(items) > 6:
                area = ctk.CTkScrollableFrame(
                    holder, height=6 * 60, fg_color=theme.ELEVATED,
                    scrollbar_button_color=theme.SURFACE_3,
                    scrollbar_button_hover_color=theme.DIM)
                area.pack(fill=tk.BOTH, expand=True)
            for it in items:
                row = ctk.CTkFrame(area, fg_color=theme.SURFACE_2,
                                   corner_radius=theme.RADIUS_SM, border_width=0)
                row.pack(fill=tk.X, pady=(0, 8))
                inner = tk.Frame(row, bg=theme.SURFACE_2)
                inner.pack(fill=tk.X, padx=12, pady=8)
                txt = tk.Frame(inner, bg=theme.SURFACE_2)
                txt.pack(side=tk.LEFT, fill=tk.X, expand=True)
                tk.Label(txt, text=it["name"], fg=theme.TEXT, bg=theme.SURFACE_2,
                         font=theme.F_BOLD, anchor="w").pack(fill=tk.X)
                when = time.strftime("%d.%m.%Y %H:%M", time.localtime(it["mtime"]))
                n = it["count"]
                sub = when if n is None else f"{when} · назначений: {n}"
                tk.Label(txt, text=sub, fg=theme.MUTED, bg=theme.SURFACE_2,
                         font=theme.F_SMALL, anchor="w").pack(fill=tk.X)
                dialogs.icon_button(
                    inner, "\ue74d", lambda it=it: delete(it),
                    fg_color=theme.SURFACE_2, border_color=theme.SURFACE_2,
                    hover_color=theme.DANGER_DARK, text_color=theme.MUTED
                ).pack(side=tk.RIGHT, padx=(8, 0))
                dialogs.button(inner, "Открыть", lambda it=it: open_(it),
                               kind="primary", width=92).pack(side=tk.RIGHT)

        def open_(it):
            m.close(None)
            self._load_config_file(it["path"], it["name"])

        def delete(it):
            if not dialogs.confirm(self.root, "Удалить пресет?",
                                   f"«{it['name']}» уйдёт в Корзину, оттуда его "
                                   f"можно вернуть.", ok_text="Удалить", danger=True):
                m.win.grab_set()
                return
            if presets.delete_preset(it["path"]):
                dialogs.toast(self.root, f"Пресет «{it['name']}» в Корзине")
            else:
                dialogs.alert(self.root, "Пресет не удалён",
                              "Корзина недоступна. Файл остался на месте — его "
                              "можно удалить из папки с настройками.", kind="warn")
            fill()
            m.win.grab_set()
            m.win.focus_force()

        def save_new():
            m.close(None)
            if self._on_save_preset():
                self._open_presets()

        fill()
        bottom = tk.Frame(m.body, bg=theme.ELEVATED)
        bottom.pack(fill=tk.X, pady=(8, 0))
        dialogs.button(bottom, "Сохранить текущие", save_new, kind="secondary",
                       width=170).pack(side=tk.LEFT)
        dialogs.button(bottom, "Закрыть", lambda: m.close(None), kind="secondary",
                       width=96).pack(side=tk.RIGHT)
        m.show()

    def _on_export(self):
        name = time.strftime("smc-pad-настройки-%Y-%m-%d.json")
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Экспорт настроек", initialfile=name,
            defaultextension=".json", filetypes=[("Настройки SMC-PAD", "*.json")])
        if not path:
            return
        try:
            appconfig.export_config(path)
        except OSError as e:
            dialogs.alert(self.root, "Настройки не сохранены", str(e), kind="error")
            return
        dialogs.toast(self.root, f"Сохранено: {os.path.basename(path)}. На другом "
                                 f"компьютере — «Импорт из файла».")

    def _on_import(self):
        path = filedialog.askopenfilename(
            parent=self.root, title="Импорт настроек",
            filetypes=[("Настройки SMC-PAD", "*.json"), ("Все файлы", "*.*")])
        if not path:
            return
        name = os.path.splitext(os.path.basename(path))[0]
        self._load_config_file(path, name, remember=True)

    def _on_open_config_dir(self):
        try:
            os.startfile(os.path.dirname(os.path.abspath(appconfig.CONFIG_FILE)))
        except OSError as e:
            dialogs.alert(self.root, "Папка не открылась", str(e), kind="error")

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
        head = tk.Frame(insp, bg=S)
        head.pack(fill=tk.X)
        self._insp_header = tk.Label(head, text="Ничего не выбрано", fg=theme.TEXT,
                                     bg=S, font=theme.F_TITLE, anchor="w")
        self._insp_header.pack(side=tk.LEFT)
        # Значок кнопки — Segoe MDL2, в шрифте заголовка его нет
        self._insp_header_icon = tk.Label(head, text="", fg=theme.TEXT, bg=S,
                                          font=(theme.ICON_FONT, 15))
        self._insp_header_icon.pack(side=tk.LEFT, padx=(10, 0))
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
        self._knob_mode_seg = self._segmented(
            self._knob_mode_frame, list(self._knob_mode_names.values()),
            self._on_knob_mode_seg)
        self._knob_mode_seg.pack(fill=tk.X)
        # Подсказка режима «Плавно»; последняя в рамке — pack без before
        self._delta_hint = tk.Label(
            self._knob_mode_frame,
            text="Действия этого режима уже работают в обе стороны. "
                 "«Влево / вправо» — для действий-нажатий.",
            fg=theme.MUTED, bg=S, font=theme.F_TINY, anchor="w", justify=tk.LEFT,
            wraplength=340)
        # Переключатель — только отображение. Источник правды — переменная:
        # её выставляют select_element и тесты, трасса держит кнопку в согласии.
        self._knob_mode_var.trace_add("write", lambda *a: self._knob_mode_seg.set(
            self._knob_mode_names.get(self._knob_mode_var.get(), "Плавно")))
        # Not packed yet — shown only for knobs

        # === 2b. Стороны (режим «влево / вправо») ===
        self._pair_frame = tk.Frame(self._insp_content, bg=S)
        self._pair_slot = tk.StringVar(value="ccw")  # which slot is being assigned
        for value, text in (("ccw", "◀ Влево"), ("cw", "Вправо ▶")):
            if value == "cw":
                # Замок между сторонами: закрыт — стороны зеркалят друг друга
                lock_row = tk.Frame(self._pair_frame, bg=S)
                lock_row.pack(fill=tk.X, pady=1)
                self._lock_btn = dialogs.icon_button(lock_row, "\ue72e",
                                                     self._on_lock_toggle, size=26)
                self._lock_btn.pack(side=tk.LEFT, padx=(24, 8))
                self._lock_lbl = tk.Label(lock_row, text="", fg=theme.MUTED, bg=S,
                                          font=theme.F_TINY, anchor="w")
                self._lock_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
                ToolTip(self._lock_btn, lambda: (
                    "Замок закрыт: выбери действие на одной стороне — на другую "
                    "встанет противоположное.\nЗамок открыт: стороны настраиваются "
                    "отдельно."))
            row = tk.Frame(self._pair_frame, bg=S)
            row.pack(fill=tk.X, pady=1)
            ctk.CTkRadioButton(row, text=text, variable=self._pair_slot, value=value,
                               command=self._on_pair_slot_change, font=theme.C_SMALL,
                               text_color=theme.TEXT, fg_color=theme.ACCENT,
                               hover_color=theme.ACCENT_HOVER,
                               border_color=theme.BORDER, width=100,
                               radiobutton_width=16, radiobutton_height=16).pack(side=tk.LEFT)
            lbl = tk.Label(row, text="—", fg=theme.MUTED, bg=S, font=theme.F_SMALL,
                           anchor="w")
            lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
            if value == "ccw":
                self._pair_ccw_lbl = lbl
            else:
                self._pair_cw_lbl = lbl
        # Последний в рамке сторон — pack без before ставит его в конец
        self._pair_hint = tk.Label(
            self._pair_frame, text="У этого действия нет пары — второе направление "
                                   "настраивается отдельно.",
            fg=theme.MUTED, bg=S, font=theme.F_TINY, anchor="w", justify=tk.LEFT,
            wraplength=340)

        # === 2c. Вкладки пэда: «Действие» / «Цвет» ===
        self._tab_seg = self._segmented(self._insp_content, ["Действие", "Цвет"],
                                        self._show_tab)
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
        self._search_entry = dialogs.entry(search_frame, placeholder_text="Поиск действия")
        self._search_entry.pack(fill=tk.X)
        # textvariable у CTkEntry отключает плейсхолдер — поэтому переменная
        # поиска кормится с клавиатуры, а не привязкой.
        self._search_entry.bind("<KeyRelease>", lambda e: self._search_var.set(
            self._search_entry.get()))
        self._search_var.trace_add("write", self._on_search_changed)

        # === 4. Список действий ===
        tree_frame = tk.Frame(self._tab_action, bg=theme.SURFACE_2)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        # height — минимум, а не размер: список растягивается на всё
        # свободное место. При 12 строках список не сжимался, и в режиме
        # «Влево / вправо» кнопки внизу инспектора срезались краем окна.
        self._action_tree = ttk.Treeview(tree_frame, height=5, show="tree",
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
        self._param_entry = dialogs.entry(self._param_frame, textvariable=self._param_var,
                                          height=30)
        self._param_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._param_entry.bind("<FocusOut>", self._on_param_commit, add="+")
        self._param_entry.bind("<Return>", self._on_param_commit)
        self._param_btn = self._btn(self._param_frame, "Обзор", self._on_param_browse,
                                    width=86)
        self._param_btn.pack(side=tk.LEFT, padx=(6, 0))
        # Not packed yet

        # ── Вкладка «Цвет» ───────────────────────────────────────────────────
        self._tab_color = tk.Frame(self._insp_content, bg=S)
        self._color_frame = self._tab_color   # прежнее имя: на него ссылается код

        self._lbl(self._tab_color, "Цвет пэда", fg=theme.MUTED).pack(anchor="w")
        self._palette = tk.Frame(self._tab_color, bg=S)
        self._palette.pack(fill=tk.X, pady=(6, 10))
        self._build_palette()

        hexrow = tk.Frame(self._tab_color, bg=S)
        hexrow.pack(fill=tk.X, pady=(0, 10))
        self._lbl(hexrow, "Код", fg=theme.MUTED).pack(side=tk.LEFT, padx=(0, 8))
        self._color_hex_var = tk.StringVar()
        color_hex_entry = dialogs.entry(hexrow, textvariable=self._color_hex_var,
                                        width=100, height=30, font=theme.C_MONO)
        color_hex_entry.pack(side=tk.LEFT)
        color_hex_entry.bind("<Return>", self._on_color_hex_commit)
        color_hex_entry.bind("<FocusOut>", self._on_color_hex_commit, add="+")

        brow = tk.Frame(self._tab_color, bg=S)
        brow.pack(fill=tk.X, pady=(0, 10))
        self._lbl(brow, "Яркость пэда", fg=theme.MUTED).pack(side=tk.LEFT, padx=(0, 8))
        self._brightness_var = tk.IntVar(value=100)
        self._brightness_scale = ctk.CTkSlider(
            brow, from_=10, to=100, number_of_steps=90, variable=self._brightness_var,
            command=self._on_brightness_change, height=16,
            progress_color=theme.ACCENT, button_color=theme.ACCENT,
            button_hover_color=theme.ACCENT_HOVER, fg_color=theme.SURFACE_3)
        self._brightness_scale.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Цвет уходит на пэд сразу при выборе — говорить об этом незачем.
        # Строка появляется только при проблеме: канал цвета недоступен.
        # Кнопка нужна на случай, когда устройство переподключилось —
        # записи цвета волатильные.
        crow = tk.Frame(self._tab_color, bg=S)
        crow.pack(fill=tk.X)
        self._color_hint_lbl = tk.Label(crow, text="", fg=theme.WARN, bg=S,
                                        font=theme.F_SMALL, anchor="w", justify=tk.LEFT,
                                        wraplength=200)
        self._color_hint_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._btn(crow, "Отправить заново", self._on_resend_colors, width=140,
                  height=30).pack(side=tk.RIGHT)

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

    def _segmented(self, parent, values, command):
        return ctk.CTkSegmentedButton(
            parent, values=values, command=command, font=theme.C_SMALL, height=32,
            fg_color=theme.SURFACE_2, unselected_color=theme.SURFACE_2,
            unselected_hover_color=theme.SURFACE_3, selected_color=theme.SEG_ON,
            selected_hover_color=theme.SEG_ON_HOVER, text_color=theme.SEG_TEXT,
            corner_radius=theme.RADIUS_SM)

    # ── Палитра: заводские цвета + свои ───────────────────────────────────────
    def _custom_colors(self):
        return [c for c in appconfig.config.get("custom_colors", [])
                if isinstance(c, str) and c.startswith("#") and len(c) == 7]

    def _build_palette(self):
        pal = self._palette
        for w in pal.winfo_children():
            w.destroy()
        self._color_preset_btns = []
        cols = 8
        for c in range(cols):
            # ровная сетка: кружок с обводкой шире и раздвигал свою колонку
            pal.grid_columnconfigure(c, minsize=34, uniform="pal")
        items =[(c, False) for c in COLOR_PRESETS] + [(c, True) for c in self._custom_colors()]
        for i, (hex_c, custom) in enumerate(items):
            # Кружок, чей цвет сливается с карточкой (чёрный в тёмной теме,
            # белый в светлой), получает обводку — иначе его не видно.
            r, g, b = (int(hex_c[i:i + 2], 16) for i in (1, 3, 5))
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            edge = lum < 20 if theme.applied == "dark" else lum > 200
            btn = ctk.CTkButton(pal, text="", width=28, height=28, corner_radius=14,
                                fg_color=hex_c, hover_color=hex_c,
                                border_width=2 if edge else 0,
                                border_color=theme.BORDER,
                                command=lambda c=hex_c: self._apply_color(c))
            btn.grid(row=i // cols, column=i % cols, padx=3, pady=3)
            self._color_preset_btns.append((hex_c, btn))
            if custom:
                btn.bind("<Button-3>", lambda e, c=hex_c: self._palette_menu(e, c))
                ToolTip(btn, lambda c=hex_c: f"{c} — свой цвет. Правый клик — убрать.")
        # Свой цвет — такой же кружок: широкая кнопка раздвигала колонку сетки
        i = len(items)
        # Canvas, \u0430 \u043d\u0435 CTkButton: \u0443 \u043a\u043d\u043e\u043f\u043a\u0438 \u0441 \u0442\u0435\u043a\u0441\u0442\u043e\u043c CTk \u0434\u0435\u0440\u0436\u0438\u0442 \u043c\u0438\u043d\u0438\u043c\u0430\u043b\u044c\u043d\u0443\u044e
        # \u0448\u0438\u0440\u0438\u043d\u0443 \u043f\u043e\u0434 \u043d\u0430\u0434\u043f\u0438\u0441\u044c, \u0438 \u00ab+\u00bb \u0432\u044b\u0445\u043e\u0434\u0438\u043b \u043e\u0432\u0430\u043b\u043e\u043c, \u0440\u0430\u0437\u0434\u0432\u0438\u0433\u0430\u044f \u0441\u0435\u0442\u043a\u0443.
        custom = tk.Canvas(pal, width=28, height=28, bg=theme.SURFACE,
                           highlightthickness=0, cursor="hand2")

        def draw_plus(fill):
            custom.delete("all")
            custom.create_oval(1, 1, 27, 27, fill=fill, outline=theme.BORDER)
            custom.create_text(14, 14, text="\ue710", fill=theme.MUTED,
                               font=(theme.ICON_FONT, 9))
        draw_plus(theme.SURFACE_2)
        custom.bind("<Enter>", lambda e: draw_plus(theme.SURFACE_3))
        custom.bind("<Leave>", lambda e: draw_plus(theme.SURFACE_2))
        custom.bind("<Button-1>", lambda e: self._pick_custom_color())
        custom.grid(row=i // cols, column=i % cols, padx=3, pady=3)
        ToolTip(custom, lambda: "Добавить свой цвет")

    def _palette_menu(self, event, hex_c):
        m = dialogs.PopupMenu(self.root, width=220)
        m.item("Убрать из палитры", lambda: self._remove_custom_color(hex_c), "\ue74d")
        m.win.update_idletasks()
        m.win.geometry(f"+{event.x_root}+{event.y_root}")
        m.win.deiconify()
        m.win.lift()
        m.win.focus_force()
        m._born = True
        self.root.after(80, lambda: setattr(m, "_born", False))

    def _add_custom_color(self, hex_c):
        hex_c = hex_c.lower()
        if hex_c in COLOR_PRESETS:
            return
        cur = [c for c in self._custom_colors() if c.lower() != hex_c]
        cur.append(hex_c)
        appconfig.config["custom_colors"] = cur[-appconfig.MAX_CUSTOM_COLORS:]
        self._save_config()
        self._build_palette()

    def _remove_custom_color(self, hex_c):
        appconfig.config["custom_colors"] = [
            c for c in self._custom_colors() if c.lower() != hex_c.lower()]
        self._save_config()
        self._build_palette()

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
            locked = _pair_locked(bindings[uid])   # до записи: она меняет вывод
            prev = _side(bindings[uid], key)
            # Параметр сохраняем, только если действие то же самое.
            keep = prev.get("param") if prev.get("action") == act.id else None
            bindings[uid][key] = {
                "action": act.id,
                "param": keep if keep is not None else _initial_param(act),
            }
            if locked:
                # Замок: на другую сторону — противоположное. Параметр у
                # стороны свой (FIXES «_side»), у пар из OPPOSITE его нет.
                other = "cw" if key == "ccw" else "ccw"
                opp = actions.opposite(act.id)
                if opp:
                    bindings[uid][other] = {"action": opp, "param": None}
                elif act.id == "none":
                    bindings[uid].pop(other, None)
                else:
                    locked = False           # пары нет — замок открывается сам
            bindings[uid]["pair_lock"] = locked
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
        self._update_mode_hint()
        self.root.after_idle(self._fit_inspector)

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
        self._update_pair_labels()

    def _on_lock_toggle(self):
        """Открыть или закрыть замок сторон.

        Закрытие зеркалит выбранную сторону на другую. Если у действия нет
        пары, замок не закрывается — подсказка под строками объясняет почему.
        """
        uid = self.current_sel
        if not uid:
            return
        b = appconfig.config["bindings"].setdefault(uid, {})
        if _pair_locked(b):
            b["pair_lock"] = False
        else:
            key = "ccw" if self._pair_slot.get() == "ccw" else "cw"
            other = "cw" if key == "ccw" else "ccw"
            a = _side(b, key).get("action") or _side(b, other).get("action")
            if a and a != "none":
                src = key if _side(b, key).get("action") else other
                opp = actions.opposite(a)
                if not opp:
                    self._update_pair_labels()
                    return
                b["cw" if src == "ccw" else "ccw"] = {"action": opp, "param": None}
            b["pair_lock"] = True
        appconfig.save_config()
        self._update_pair_labels()
        self.update_ui_from_config()

    def _update_pair_labels(self):
        if not self.current_sel:
            return
        b = appconfig.config["bindings"].get(self.current_sel, {})
        ccw_act = actions.get(_side(b, "ccw").get("action") or "")
        cw_act = actions.get(_side(b, "cw").get("action") or "")
        self._pair_ccw_lbl.config(text=ccw_act.label if ccw_act else "—")
        self._pair_cw_lbl.config(text=cw_act.label if cw_act else "—")

        locked = _pair_locked(b)
        self._lock_btn.configure(text="\ue72e" if locked else "\ue785",
                                 text_color=theme.ACCENT if locked else theme.MUTED)
        self._lock_lbl.config(text="Связаны: вторая сторона — противоположное"
                              if locked else "Стороны настраиваются отдельно")
        key = "ccw" if self._pair_slot.get() == "ccw" else "cw"
        a = _side(b, key).get("action")
        if not locked and a and a != "none" and not actions.opposite(a):
            self._pair_hint.pack(fill=tk.X, pady=(2, 0))
        else:
            self._pair_hint.pack_forget()

    def _update_mode_hint(self):
        if self._knob_mode_var.get() == "delta":
            self._delta_hint.pack(fill=tk.X, pady=(4, 0))
        else:
            self._delta_hint.pack_forget()

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
        # Молчит, пока всё в порядке. Пока устройство не подключено, канал
        # цвета ещё неизвестен — это тоже не проблема, о которой стоит писать.
        bad = self._color_known and not self._color_ok
        self._color_hint_lbl.config(
            text="Устройство не принимает цвет — он виден только на экране." if bad else "")

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
        start = self._color_base or DEFAULT_PAD_COLOR
        result = dialogs.pick_color(self.root, start)
        if result:
            self._add_custom_color(result)
            self._apply_color(result)

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
                                   fg=theme.WARN)
            self._learn_btn.configure(text="Отмена", fg_color=theme.WARN,
                                      hover_color=theme.WARN, text_color="#1a1300")
            self._learn_banner.config(
                text=f"ПРИВЯЗКА {self._name(uid)} — нажмите контрол на устройстве")
            self._learn_banner.pack(side=tk.LEFT, padx=10)
            return
        self._learn_banner.pack_forget()
        self._learn_btn.configure(text="Привязать", fg_color=theme.SURFACE_2,
                                  hover_color=theme.SURFACE_3, text_color=theme.TEXT)
        el = self.ui_elements[uid]
        mid = el.get("midi_id")
        if mid is None:
            text = "не привязан"
            self._learn_lbl.config(text=text, fg=theme.MUTED)
            return
        kind = el.get("midi_kind")
        if kind is None:
            kind = "note" if el["type"] == "pad" else "cc"
        self._learn_lbl.config(text=f"{'CC' if kind == 'cc' else 'нота'} {mid}",
                               fg=theme.OK)

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
        self.root.after_idle(self._fit_inspector)
        el = self.ui_elements[uid]
        num = uid.split('_')[1]

        # Header text
        if el["type"] == "btn":
            self._insp_header.config(text="BUTTON")
            self._insp_header_icon.config(text=el.get("icon", ""))
        else:
            self._insp_header.config(text=self._name(uid))
            self._insp_header_icon.config(text="")

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
            self._update_mode_hint()
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
        m = dialogs.Modal(self.root, "Своя комбинация", width=380)
        dialogs.label(m.body, "Нажмите нужную комбинацию на клавиатуре.",
                      fg=theme.MUTED, font=theme.F_BODY, anchor="w").pack(
            fill=tk.X, pady=(8, 0))
        lbl = tk.Label(m.body, text="Ожидание", font=(theme.FONT, 16, "bold"),
                       fg=theme.WARN, bg=theme.ELEVATED)
        lbl.pack(pady=(16, 4))
        m.buttons([("Отмена", None, "secondary")])
        dlg = m.win
        # Окну нужен фокус клавиатуры явно — Modal.show выдаёт его дважды:
        # grab_set() перехватывает только мышь, а фокус, выданный до первой
        # отрисовки, окно на Windows теряет (FIXES, запись со второго раза).
        # Esc закрывает окно (привязка Modal точнее, чем <KeyPress>), поэтому
        # одиночный Esc хоткеем не записать; Ctrl+Esc и прочие — можно.

        recorded = []
        def on_key(e):
            mods = _held_modifiers()
            key = _key_from_event(e)
            if key is None:   # нажат только модификатор — ждём основную клавишу
                lbl.config(text=_format_hotkey("+".join(mods)) + " + ...")
                return

            hotkey_str = "+".join(mods + [key])
            lbl.config(text=_format_hotkey(hotkey_str), fg=theme.OK)
            recorded.append(hotkey_str)
            dlg.unbind("<KeyPress>")
            dlg.after(500, lambda: m.close(hotkey_str))

        dlg.bind("<KeyPress>", on_key)
        m.show()
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
        if dialogs.confirm(
                self.root, "Сбросить все назначения?",
                "Действия и цвета всех элементов будут удалены. Привязки к MIDI "
                "(какая кнопка какой контрол) сохранятся. Можно заранее сохранить "
                "пресет.", ok_text="Сбросить", danger=True):
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
                                 fg=theme.OK if display else theme.DIM)
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
                                     fg=theme.OK if parts else theme.MUTED)
                else:
                    el["lbl"].config(text=display[:10],
                                     fg=theme.OK if display else theme.MUTED)

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
    VK_D = 0x44

    def _on_ctrl_key(self, event):
        if getattr(event, "keycode", 0) == self.VK_D:
            self._toggle_debug()
            return "break"

    DEBUG_LOG_MAX = 2_000_000   # байт; больше — старое уходит в .old

    def _debug_log_path(self):
        return os.path.join(os.path.dirname(os.path.abspath(appconfig.CONFIG_FILE)),
                            "debug.log")

    def _toggle_debug(self, event=None):
        """Ctrl+D: панель журнала + запись журнала в debug.log рядом с настройками.

        Из панели текст не скопировать и её не растянуть — поэтому, пока
        журнал включён, каждая строка дописывается в файл. Включение сначала
        сбрасывает в файл то, что уже накопилось в памяти.
        """
        self._debug_visible = not self._debug_visible
        if self._debug_visible:
            self._debug_frame.pack(fill=tk.X, side=tk.BOTTOM)
            self._debug_write(["", f"=== журнал включён {time.strftime('%Y-%m-%d %H:%M:%S')} ==="]
                              + list(self._debug_lines))
            dialogs.toast(self.root, "Журнал пишется в файл debug.log")
        else:
            self._debug_write([f"=== журнал выключен {time.strftime('%H:%M:%S')} ==="])
            self._debug_frame.pack_forget()

    def _debug_write(self, lines):
        """Дописать строки в debug.log. Сбой записи не роняет обработку MIDI."""
        path = self._debug_log_path()
        try:
            if os.path.exists(path) and os.path.getsize(path) > self.DEBUG_LOG_MAX:
                os.replace(path, path + ".old")
            with open(path, "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except OSError:
            pass

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
            self._debug_write([f"{time.strftime('%H:%M:%S')} {msg}"])
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
        self.last_input_var.set(f"{self._name(uid)}: {err}"[:60])
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
        self._bank_var.set(self._bank_text())
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
        self.last_input_var.set(f"{self._name(uid)} ← {label} {midi_id}")
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
        # Банк крутилок виден по CC: 30-37 — первый, 38-45 — второй. Сюда
        # попадают только CC из карты: у крутилки, привязанной вручную к
        # другому CC, банк не определить — подпись остаётся прежней.
        knob_bank = 1 if cc <= 37 else 2
        if knob_bank != self._knob_bank:
            self._knob_bank = knob_bank
            self._bank_var.set(self._bank_text())
        return uid

    @staticmethod
    def _name(uid):
        """Имя элемента для людей: PAD 6, KNOB 6, BUTTON PLAY.
        uid в конфиге (pad_6, knob_6, btn_4) при этом не меняются."""
        kind, _, num = uid.partition("_")
        if kind == "btn":
            return "BUTTON " + BTN_WORDS.get(int(num), num) if num.isdigit() else uid
        return {"pad": "PAD", "knob": "KNOB"}.get(kind, kind.upper()) + " " + num

    def _bank_text(self):
        return f"Pad bank {self._current_bank} · Knob bank {self._knob_bank}"

    def _handle_trigger(self, uid, kind, midi_id, val):
        """Пэд или кнопка: одно нажатие — одно действие."""
        # Кнопка на CC: нажатие — большое значение, отпускание — малое.
        pressed = val >= 64 if kind == "cc" else val > 0
        self._debug_log(f"{kind.upper()} {midi_id}={val} uid={uid} "
                        f"{'нажатие' if pressed else 'отпускание'}")
        if not pressed:
            return
        label = f"CC {midi_id}" if kind == "cc" else f"нота {midi_id}"
        self.last_input_var.set(f"{self._name(uid)} · {label}")
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
            self.last_input_var.set(f"{self._name(uid)} · {label} — действие не назначено")
            return
        self._run_action(uid, action_id, b.get("param"))

    def _handle_knob(self, uid, midi_id, val):
        """Крутилка.

        M-Vave SMC-PAD шлёт АБСОЛЮТНЫЕ значения CC (0→1→2→3 по часовой,
        3→2→1→0 против), а не относительные дельты. Дельта считается как
        разница с предыдущим значением, заворот 0↔127 — через порог ±64.
        Не «улучшать»: три предыдущих декодера были неверны, см. FIXES.md.
        """
        self.last_input_var.set(f"{self._name(uid)} · CC {midi_id} · {val}")

        prev = self._prev_knob_cc.get(uid)
        if val in RAIL and (prev is None or prev == val):
            # Упор. Счётчик в контроллере не заворачивается, а упирается в
            # 0 / 127: если после запуска крутилка стояла, скажем, на 106,
            # вправо оставался 21 щелчок — громкость «не доходила до 100».
            # Повтор крайнего значения считаем шагом в ту же сторону.
            # ЗАМЕР 2026-09-24: железо на упоре НЕ шлёт повторов (сырые BLE-пакеты
            # обрываются на 7F / 00) — эта ветка сейчас не срабатывает. Оставлена
            # на случай другой прошивки. Лечение — режим энкодера, docs/NEXT.md.
            delta = RAIL[val]
        elif prev is not None:
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
                self._color_known = True
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
    # ── Размер и место окна ───────────────────────────────────────────────────
    START_W, START_H, MIN_W = 1320, 780, 1180

    def _min_height(self):
        """Минимум по высоте: схема целиком плюс то, чего не хватает инспектору.

        Карточка инспектора не растягивает окно (pack_propagate выключен ради
        ширины), поэтому её запрос добавляется отдельно.
        """
        self.root.update_idletasks()
        need = self.root.winfo_reqheight()
        insp = self._inspector_frame
        short = insp.winfo_reqheight() - insp.winfo_height()
        if insp.winfo_height() > 1 and short > 0:
            need = max(need, self.root.winfo_height() + short)
        return need

    def _place_window(self):
        """Сохранённое место, если оно на существующем мониторе; иначе по центру
        рабочей области, не выше 90% её высоты."""
        min_h = self._min_height()
        self.root.minsize(self.MIN_W, min_h)
        saved = winplace.parse(appconfig.config.get("window"))
        if saved and winplace.on_screen(saved[2], saved[3], saved[0], saved[1]):
            w, h, x, y = saved
            self.root.geometry(f"{max(w, self.MIN_W)}x{max(h, min_h)}+{x}+{y}")
        else:
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            ax, ay, aw, ah = winplace.work_area(0, 0, sw, sh) or (0, 0, sw, sh)
            w = min(self.START_W, aw)
            h = max(min(self.START_H, int(ah * 0.9)), min(min_h, ah))
            x, y = ax + max((aw - w) // 2, 0), ay + max((ah - h) // 2, 0)
            self.root.geometry(f"{w}x{h}+{x}+{y}")
        self._placed = True

    def _fit_inspector(self):
        """Если содержимому инспектора не хватает высоты — поднять минимум окна
        (а с ним и само окно), но не выше рабочей области."""
        if not self._placed or self.root.state() != "normal":
            return
        min_h = self._min_height()
        cur_h = self.root.winfo_height()
        area = winplace.work_area(self.root.winfo_rootx(), self.root.winfo_rooty(),
                                  self.root.winfo_width(), cur_h)
        if area:
            min_h = min(min_h, area[3])
        self.root.minsize(self.MIN_W, min_h)
        if cur_h < min_h:
            # Растём вниз, а если низ уходит за рабочую область — сдвигаемся вверх
            y = self.root.winfo_y()
            if area:
                y = max(area[1], min(y, area[1] + area[3] - min_h))
            self.root.geometry(f"{self.root.winfo_width()}x{min_h}+{self.root.winfo_x()}+{y}")

    def _on_root_configure(self, event):
        if event.widget is not self.root or not self._placed:
            return
        if self._geom_job is not None:
            self.root.after_cancel(self._geom_job)
        self._geom_job = self.root.after(600, self._remember_geometry)

    def _remember_geometry(self):
        """Запомнить размер и место. Только в обычном состоянии: у свёрнутого
        окна координаты -32000, у спрятанного в трей — мусор."""
        self._geom_job = None
        try:
            if self.root.state() != "normal" or not self.root.winfo_viewable():
                return
            g = self.root.geometry()
        except tk.TclError:
            return
        if appconfig.config.get("window") != g:
            appconfig.config["window"] = g
            self._save_config()

    def start_in_tray(self):
        """Запуск с --tray (автозапуск): окно сразу в трее.

        Если значок трея не поднялся, окно показывается: иначе программа
        висела бы невидимой, и закрыть её было бы нечем (FIXES «tray.py»).
        Значок pystray появляется асинхронно — через секунду проверяем
        ещё раз, что он действительно виден.
        """
        if not self._tray.available:
            return
        self.hide_to_tray()

        def verify():
            icon = self._tray._icon
            if icon is None or not getattr(icon, "visible", True):
                self._debug_log("--tray: значок трея не появился — показываю окно")
                self.show_from_tray()
        self.root.after(1500, verify)

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
    import sys as _sys
    if "--tray" in _sys.argv[1:]:       # автозапуск с Windows
        app.start_in_tray()
    app.load_hardware_mapping()
    t = threading.Thread(target=ble.start_ble_thread, daemon=True)
    t.start()
    root.mainloop()

if __name__ == "__main__":
    main()
