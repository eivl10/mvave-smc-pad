"""Свои окна вместо системных: тосты, сообщения, вопросы, меню, выбор цвета.

Все берут цвета из mvave.theme в момент создания — отдельной копии палитры
здесь нет (в AGY-sub диалоги со своей копией выходили белыми в тёмной теме).

Скругление без белых углов: окно без рамки красится «цветом-ключом»,
который Windows делает прозрачным (-transparentcolor), а карточка
CTkFrame рисует скруглённый фон поверх. Ключ близок к фону окна —
сглаженный край карточки смешивается с ним и не даёт тёмной каймы.
Системные окна остаются только для выбора файла и папки: это проводник.
"""
import colorsys
import tkinter as tk

import customtkinter as ctk

from mvave import aa_shapes, theme

# Углы CTk без лесенки: до создания первого виджета. Почему — в aa_shapes.
aa_shapes.install()

PAD = 20          # внутренний отступ окна; шкала 4/8/12/16/24 — плюс 20 из AGY-sub
GAP = 8


def _key():
    # Чуть отличается от любого цвета темы: ключ обязан быть уникальным.
    return "#e3e3f0" if theme.applied == "light" else "#1b1b2c"


def _frameless(parent, topmost=False):
    w = tk.Toplevel(parent)
    w.withdraw()
    w.overrideredirect(True)
    key = _key()
    w.configure(bg=key)
    w.attributes("-transparentcolor", key)
    if topmost:
        w.attributes("-topmost", True)
    return w, key


# ── Кнопки и поля — общие для диалогов и главного окна ──────────────────────
def button(parent, text, command, kind="secondary", **kw):
    """primary — главное действие, secondary — заливка + рамка (в светлой теме
    без рамки кнопка читается как текст), danger — опасное, ghost — ссылка."""
    t = theme
    styles = {
        "primary": dict(fg_color=t.ACCENT, hover_color=t.ACCENT_HOVER,
                        text_color=t.ON_ACCENT, border_width=0),
        "secondary": dict(fg_color=t.SURFACE_2, hover_color=t.SURFACE_3,
                          text_color=t.TEXT, border_width=1, border_color=t.BORDER),
        "danger": dict(fg_color=t.DANGER, hover_color=t.DANGER_HOVER,
                       text_color="#ffffff", border_width=0),
        "success": dict(fg_color=t.OK_DARK, hover_color=t.SURFACE_3,
                        text_color=t.OK, border_width=0),
        "ghost": dict(fg_color="transparent", hover_color=t.SURFACE_3,
                      text_color=t.MUTED, border_width=0),
    }
    style = dict(styles[kind])
    style.update(kw)
    style.setdefault("height", 32)
    style.setdefault("corner_radius", t.RADIUS_SM)
    style.setdefault("font", t.C_SMALL)
    b = ctk.CTkButton(parent, text=text, command=command, **style)
    return b


def icon_button(parent, glyph, command, size=32, **kw):
    kw.setdefault("fg_color", theme.SURFACE_2)
    kw.setdefault("hover_color", theme.SURFACE_3)
    kw.setdefault("text_color", theme.MUTED)
    kw.setdefault("border_width", 1)
    kw.setdefault("border_color", theme.BORDER)
    return ctk.CTkButton(parent, text=glyph, command=command, width=size, height=size,
                         corner_radius=theme.RADIUS_SM, font=theme.C_ICON, **kw)


def entry(parent, **kw):
    """Поле без обводки: читается заливкой, рамка акцентом — только в фокусе."""
    kw.setdefault("height", 32)
    kw.setdefault("font", theme.C_SMALL)
    kw.setdefault("corner_radius", theme.RADIUS_SM)
    e = ctk.CTkEntry(parent, fg_color=theme.SURFACE_2, border_color=theme.SURFACE_2,
                     border_width=1, text_color=theme.TEXT,
                     placeholder_text_color=theme.DIM, **kw)
    e.bind("<FocusIn>", lambda ev: e.configure(border_color=theme.ACCENT), add="+")
    e.bind("<FocusOut>", lambda ev: e.configure(border_color=theme.SURFACE_2), add="+")
    return e


def label(parent, text="", fg=None, font=None, **kw):
    return tk.Label(parent, text=text, fg=fg or theme.TEXT, bg=parent.cget("bg"),
                    font=font or theme.F_BODY, **kw)


def _center_on(win, root, dy=0):
    win.update_idletasks()
    w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    if root.winfo_viewable():
        x = root.winfo_rootx() + (root.winfo_width() - w) // 2
        y = root.winfo_rooty() + (root.winfo_height() - h) // 3 + dy
    else:
        x = (win.winfo_screenwidth() - w) // 2
        y = (win.winfo_screenheight() - h) // 3
    win.geometry(f"+{max(x, 0)}+{max(y, 0)}")


# ── Модальное окно ──────────────────────────────────────────────────────────
class Modal:
    """Карточка поверх затемнённого главного окна.

    Затемнение заменяет рамку и тень: карточка отделена от окна контрастом,
    а не контуром. grab_set держит мышь, фокус выдаётся явно и повторно
    через after — иначе на Windows клавиатура остаётся у главного окна
    (FIXES, «запись клавиши срабатывала со второго раза»).
    """

    def __init__(self, root, title=None, width=420):
        self.root = root
        self.result = None
        self._overlay = None
        if root.winfo_viewable():
            ov = tk.Toplevel(root)
            ov.overrideredirect(True)
            ov.configure(bg="#000000")
            ov.attributes("-alpha", 0.28 if theme.applied == "dark" else 0.18)
            ov.geometry(f"{root.winfo_width()}x{root.winfo_height()}"
                        f"+{root.winfo_rootx()}+{root.winfo_rooty()}")
            ov.bind("<Button-1>", lambda e: self.close(None))
            self._overlay = ov
        self.win, key = _frameless(root)
        self.win.transient(root)
        self.card = ctk.CTkFrame(self.win, fg_color=theme.ELEVATED, bg_color=key,
                                 corner_radius=14, border_width=0)
        self.card.pack()
        self.body = tk.Frame(self.card, bg=theme.ELEVATED)
        self.body.pack(padx=PAD, pady=PAD, fill=tk.BOTH, expand=True)
        tk.Frame(self.body, bg=theme.ELEVATED, width=width - 2 * PAD, height=0).pack()
        self.width = width
        if title:
            label(self.body, title, font=theme.F_TITLE[:1] + (13, "bold"),
                  anchor="w").pack(fill=tk.X)
        self.win.bind("<Escape>", lambda e: self.close(None))

    def buttons(self, specs):
        """specs: [(текст, результат или функция, вид)] слева направо.

        Последняя — главная. Функция вызывается вместо закрытия с результатом.
        """
        row = tk.Frame(self.body, bg=theme.ELEVATED)
        row.pack(fill=tk.X, pady=(16, 0))
        for text, result, kind in reversed(specs):
            cmd = result if callable(result) else (lambda r=result: self.close(r))
            button(row, text, cmd, kind=kind,
                   width=130 if kind in ("primary", "danger") else 96).pack(
                side=tk.RIGHT, padx=(GAP, 0))
        return row

    def show(self, focus=None):
        _center_on(self.win, self.root)
        self.win.deiconify()
        self.win.lift()
        self.win.grab_set()
        target = focus or self.win
        target.focus_force()
        self.win.after(50, target.focus_force)
        self.root.wait_window(self.win)
        return self.result

    def close(self, result):
        self.result = result
        try:
            self.win.grab_release()
        except tk.TclError:
            pass
        for w in (self.win, self._overlay):
            if w is not None:
                try:
                    w.destroy()
                except tk.TclError:
                    pass


_KIND_GLYPH = {"info": ("\ue946", "ACCENT"), "ok": ("\ue73e", "OK"),
               "warn": ("\ue7ba", "WARN"), "error": ("\uea39", "DANGER")}


def _message(m, text, kind):
    row = tk.Frame(m.body, bg=theme.ELEVATED)
    row.pack(fill=tk.X, pady=(10, 0))
    if kind in _KIND_GLYPH:
        g, col = _KIND_GLYPH[kind]
        tk.Label(row, text=g, fg=getattr(theme, col), bg=theme.ELEVATED,
                 font=(theme.ICON_FONT, 14)).pack(side=tk.LEFT, anchor="n", padx=(0, 12), pady=2)
    tk.Label(row, text=text, fg=theme.TEXT, bg=theme.ELEVATED, font=theme.F_BODY,
             justify=tk.LEFT, anchor="w", wraplength=m.width - 2 * PAD - 40).pack(
        side=tk.LEFT, fill=tk.X, expand=True)


def alert(root, title, text, kind="info"):
    m = Modal(root, title)
    _message(m, text, kind)
    m.buttons([("Понятно", True, "primary")])
    m.win.bind("<Return>", lambda e: m.close(True))
    m.show()


def confirm(root, title, text, ok_text="OK", cancel_text="Отмена", danger=False, kind=None):
    m = Modal(root, title)
    _message(m, text, kind)
    m.buttons([(cancel_text, False, "secondary"),
               (ok_text, True, "danger" if danger else "primary")])
    m.win.bind("<Return>", lambda e: m.close(True))
    return bool(m.show())


def ask_text(root, title, prompt, initial="", ok_text="Сохранить", hint=None):
    m = Modal(root, title)
    if prompt:
        label(m.body, prompt, fg=theme.MUTED, font=theme.F_SMALL, anchor="w").pack(
            fill=tk.X, pady=(12, 6))
    e = entry(m.body)
    e.pack(fill=tk.X)
    if initial:
        e.insert(0, initial)
        e.select_range(0, tk.END)
    if hint:
        label(m.body, hint, fg=theme.MUTED, font=theme.F_SMALL, anchor="w",
              justify=tk.LEFT, wraplength=m.width - 2 * PAD).pack(fill=tk.X, pady=(6, 0))

    def ok(ev=None):
        v = e.get().strip()
        if v:
            m.close(v)
    m.buttons([("Отмена", None, "secondary"), (ok_text, ok, "primary")])
    m.win.bind("<Return>", ok)
    return m.show(focus=e)


# ── Тост ────────────────────────────────────────────────────────────────────
_toast = {"win": None, "job": None}


def toast(root, text, kind="ok"):
    """Неблокирующая плашка в правом нижнем углу окна.

    Один тост за раз: новый заменяет старый. Фокус не забирает. Появляется
    за 200 мс, держится 3.2 с, гаснет за 250 мс. Клик закрывает. Состояние
    анимации — в модуле: у колбэков after нет доступа к локальным
    переменным вызвавшего (ловушка из AGY-sub).
    """
    _toast_close()
    w, key = _frameless(root, topmost=True)
    card = ctk.CTkFrame(w, fg_color=theme.ELEVATED, bg_color=key, corner_radius=12,
                        border_width=1, border_color=theme.BORDER)
    card.pack()
    row = tk.Frame(card, bg=theme.ELEVATED)
    row.pack(padx=16, pady=12)
    g, col = _KIND_GLYPH.get(kind, _KIND_GLYPH["info"])
    tk.Label(row, text=g, fg=getattr(theme, col), bg=theme.ELEVATED,
             font=(theme.ICON_FONT, 12)).pack(side=tk.LEFT, padx=(0, 10))
    tk.Label(row, text=text, fg=theme.TEXT, bg=theme.ELEVATED, font=theme.F_BODY,
             justify=tk.LEFT, wraplength=340).pack(side=tk.LEFT)
    for wd in (w, card, row) + tuple(row.winfo_children()):
        wd.bind("<Button-1>", lambda e: _toast_close())
    w.update_idletasks()
    ww, hh = w.winfo_reqwidth(), w.winfo_reqheight()
    if root.winfo_viewable():
        x = root.winfo_rootx() + root.winfo_width() - ww - 16
        y = root.winfo_rooty() + root.winfo_height() - hh - 16
    else:
        x = w.winfo_screenwidth() - ww - 16
        y = w.winfo_screenheight() - hh - 64
    w.geometry(f"{ww}x{hh}+{x}+{y}")
    w.attributes("-alpha", 0.0)
    w.deiconify()
    _toast["win"] = w
    _fade(w, 0.0, 1.0, 200, lambda: _toast_hold(w))


def _fade(w, a, b, ms, done=None, step=0, steps=10):
    if _toast["win"] is not w:
        return
    try:
        w.attributes("-alpha", a + (b - a) * step / steps)
    except tk.TclError:
        return
    if step < steps:
        _toast["job"] = w.after(ms // steps, lambda: _fade(w, a, b, ms, done, step + 1, steps))
    elif done:
        done()


def _toast_hold(w):
    _toast["job"] = w.after(3200, lambda: _fade(w, 1.0, 0.0, 250, _toast_close))


def _toast_close():
    w = _toast["win"]
    _toast["win"] = None
    if w is not None:
        try:
            if _toast["job"]:
                w.after_cancel(_toast["job"])
            w.destroy()
        except tk.TclError:
            pass
    _toast["job"] = None


# ── Всплывающее меню ────────────────────────────────────────────────────────
class PopupMenu:
    """Меню-карточка без рамки. Закрывается по клику мимо, Esc, потере фокуса
    и перемещению окна: меню, которое не закрылось, в AGY-sub висело на
    экране минутами."""

    _current = None

    def __init__(self, root, width=260):
        PopupMenu.close_current()
        self.root = root
        self.win, key = _frameless(root)
        self.win.transient(root)
        self.card = ctk.CTkFrame(self.win, fg_color=theme.ELEVATED, bg_color=key,
                                 corner_radius=12, border_width=1,
                                 border_color=theme.ELEVATED)
        self.card.pack()
        self.body = tk.Frame(self.card, bg=theme.ELEVATED)
        self.body.pack(padx=6, pady=6)
        tk.Frame(self.body, bg=theme.ELEVATED, width=width - 12, height=0).pack()
        self.win.bind("<Escape>", lambda e: self.close())
        self.win.bind("<FocusOut>", self._on_focus_out)
        if not getattr(root, "_popup_guard", False):
            root.bind("<Button-1>", lambda e: PopupMenu.close_current(), add="+")
            root.bind("<Configure>", lambda e: e.widget is root and PopupMenu.close_current(),
                      add="+")
            root._popup_guard = True
        PopupMenu._current = self

    @classmethod
    def close_current(cls):
        if cls._current is not None:
            cls._current.close()

    def item(self, text, command, glyph=None):
        row = tk.Frame(self.body, bg=theme.ELEVATED, cursor="hand2")
        row.pack(fill=tk.X)
        ic = tk.Label(row, text=glyph or "", fg=theme.MUTED, bg=theme.ELEVATED,
                      font=(theme.ICON_FONT, 11), width=2)
        ic.pack(side=tk.LEFT, padx=(8, 6), pady=8)
        tx = tk.Label(row, text=text, fg=theme.TEXT, bg=theme.ELEVATED,
                      font=theme.F_BODY, anchor="w")
        tx.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 12))
        parts = (row, ic, tx)

        def paint(bg):
            for p in parts:
                p.configure(bg=bg)

        def run(e=None):
            self.close()
            self.root.after_idle(command)
        for p in parts:
            p.bind("<Enter>", lambda e: paint(theme.HOVER))
            p.bind("<Leave>", lambda e: paint(theme.ELEVATED))
            p.bind("<Button-1>", run)
        return row

    def separator(self):
        tk.Frame(self.body, bg=theme.SURFACE_3 if theme.applied == "dark" else theme.SURFACE_2,
                 height=1).pack(fill=tk.X, padx=8, pady=4)

    def caption(self, text):
        tk.Label(self.body, text=text, fg=theme.MUTED, bg=theme.ELEVATED,
                 font=theme.F_SMALL, anchor="w").pack(fill=tk.X, padx=12, pady=(6, 4))

    def show_below(self, widget, align_right=True):
        self.win.update_idletasks()
        w = self.win.winfo_reqwidth()
        x = widget.winfo_rootx() + (widget.winfo_width() - w if align_right else 0)
        y = widget.winfo_rooty() + widget.winfo_height() + 6
        self.win.geometry(f"+{x}+{y}")
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()
        # Клик, открывший меню, ещё обрабатывается — страж корня закрыл бы
        # меню сразу. Помечаем, что первый клик уже был.
        self._born = True
        self.root.after(80, lambda: setattr(self, "_born", False))

    def _on_focus_out(self, e):
        def check():
            f = None
            try:
                f = self.win.focus_get()
            except (tk.TclError, KeyError):
                pass
            if f is None or not str(f).startswith(str(self.win)):
                self.close()
        self.win.after(30, check)

    def close(self):
        if getattr(self, "_born", False):
            return
        if PopupMenu._current is self:
            PopupMenu._current = None
        try:
            self.win.destroy()
        except tk.TclError:
            pass


# ── Выбор цвета ─────────────────────────────────────────────────────────────
def _hex(r, g, b):
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def _parse(hx):
    hx = (hx or "").strip().lstrip("#")
    if len(hx) != 6:
        return None
    try:
        return tuple(int(hx[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return None


class _Bar:
    """Полоса-градиент с бегунком: тон, насыщенность или яркость."""
    W, H = 320, 18

    def __init__(self, parent, on_change):
        self.c = tk.Canvas(parent, width=self.W, height=self.H + 6, bg=theme.ELEVATED,
                           highlightthickness=0, cursor="hand2")
        self.value = 0.0
        self.on_change = on_change
        self.c.bind("<Button-1>", self._drag)
        self.c.bind("<B1-Motion>", self._drag)

    def _drag(self, e):
        self.value = max(0.0, min(1.0, (e.x - 3) / (self.W - 6)))
        self.on_change()

    def draw(self, color_at):
        c = self.c
        c.delete("all")
        for x in range(3, self.W - 3, 2):
            col = color_at((x - 3) / (self.W - 6))
            c.create_line(x, 4, x, self.H + 2, fill=col, width=2)
        mx = 3 + self.value * (self.W - 6)
        c.create_oval(mx - 7, 1, mx + 7, self.H + 5, outline=theme.TEXT, width=2,
                      fill=color_at(self.value))


def pick_color(root, initial="#ff0000"):
    """Своё окно выбора цвета. Возвращает '#rrggbb' или None."""
    m = Modal(root, "Свой цвет", width=380)
    rgb = _parse(initial) or (1, 0, 0)
    h, s, v = colorsys.rgb_to_hsv(*rgb)

    top = tk.Frame(m.body, bg=theme.ELEVATED)
    top.pack(fill=tk.X, pady=(12, 12))
    swatch = ctk.CTkFrame(top, width=56, height=56, corner_radius=28,
                          fg_color=initial, bg_color=theme.ELEVATED)
    swatch.pack(side=tk.LEFT)
    right = tk.Frame(top, bg=theme.ELEVATED)
    right.pack(side=tk.LEFT, padx=(16, 0), fill=tk.X, expand=True)
    label(right, "Код цвета", fg=theme.MUTED, font=theme.F_SMALL, anchor="w").pack(fill=tk.X)
    hexe = entry(right, width=120, font=theme.C_MONO)
    hexe.pack(anchor="w", pady=(4, 0))

    bars = {}

    def current():
        return _hex(*colorsys.hsv_to_rgb(bars["h"].value, bars["s"].value, bars["v"].value))

    def refresh(from_entry=False):
        hh, ss, vv = bars["h"].value, bars["s"].value, bars["v"].value
        bars["h"].draw(lambda t: _hex(*colorsys.hsv_to_rgb(t, 1, 1)))
        bars["s"].draw(lambda t: _hex(*colorsys.hsv_to_rgb(hh, t, max(vv, 0.35))))
        bars["v"].draw(lambda t: _hex(*colorsys.hsv_to_rgb(hh, ss, t)))
        col = current()
        swatch.configure(fg_color=col)
        if not from_entry:
            hexe.delete(0, tk.END)
            hexe.insert(0, col)

    for key, name in (("h", "Тон"), ("s", "Насыщенность"), ("v", "Яркость")):
        label(m.body, name, fg=theme.MUTED, font=theme.F_SMALL, anchor="w").pack(fill=tk.X)
        bars[key] = _Bar(m.body, refresh)
        bars[key].c.pack(anchor="w", pady=(2, 8))
    bars["h"].value, bars["s"].value, bars["v"].value = h, s, v

    def on_hex(ev=None):
        rgb2 = _parse(hexe.get())
        if rgb2:
            bars["h"].value, bars["s"].value, bars["v"].value = colorsys.rgb_to_hsv(*rgb2)
            refresh(from_entry=True)
    hexe.bind("<KeyRelease>", on_hex)

    label(m.body, "Цвет останется в палитре.", fg=theme.MUTED, font=theme.F_SMALL,
          anchor="w").pack(fill=tk.X, pady=(4, 0))
    m.buttons([("Отмена", None, "secondary"), ("Выбрать", lambda: m.close(current()), "primary")])
    m.win.bind("<Return>", lambda e: m.close(current()))
    refresh()
    return m.show()
