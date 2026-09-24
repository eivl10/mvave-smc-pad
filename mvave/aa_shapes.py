"""Сглаженные скругления CustomTkinter на Windows.

CTk рисует углы карточек, кнопок и полей кругами из своего шрифта-фигуры
(`CustomTkinter_shapes_font`), считая, что текст на холсте сглаживается.
На Windows Tk выводит текст через GDI с ClearType, а ClearType сглаживает
только по горизонтали: почти горизонтальные участки дуги (верх и низ угла)
остаются ступеньками. На снимке владельца углы вышли «лесенкой».

Здесь круги-символы подменяются картинками с альфа-каналом: PIL рисует круг
с запасом ×4 и уменьшает. Tk смешивает альфу картинки с тем, что под ней на
холсте, поэтому край мягкий на любом фоне.

CTk кладёт на каждый угол два круга (второй повёрнут на 180°, чтобы символ
шрифта выглядел симметричнее). Картинке это не нужно, а два полупрозрачных
края друг на друге дали бы тяжёлую кайму — второй круг остаётся пустым.
"""
import tkinter

from PIL import Image, ImageDraw, ImageTk

from customtkinter.windows.widgets.core_rendering.ctk_canvas import CTkCanvas

_SS = 4                 # запас по размеру при отрисовке
_cache = {}             # (interp, радиус, rgb) → PhotoImage
_installed = False


def _rgb(canvas, fill):
    r, g, b = canvas.winfo_rgb(fill)
    return r >> 8, g >> 8, b >> 8


def _circle(canvas, radius, fill):
    """PhotoImage круга диаметром 2*radius, края сглажены альфой."""
    if not fill or radius <= 0:
        return ""
    rgb = _rgb(canvas, fill)
    key = (id(canvas.tk), int(radius), rgb)
    img = _cache.get(key)
    if img is None:
        d = int(radius) * 2
        big = Image.new("L", (d * _SS, d * _SS), 0)
        ImageDraw.Draw(big).ellipse((0, 0, d * _SS - 1, d * _SS - 1), fill=255)
        alpha = big.resize((d, d), Image.LANCZOS)
        im = Image.new("RGBA", (d, d), rgb + (0,))
        im.putalpha(alpha)
        img = ImageTk.PhotoImage(im, master=canvas)
        _cache[key] = img
    return img


def _state(canvas):
    st = canvas.__dict__.get("_aa_img_state")
    if st is None:
        st = canvas.__dict__["_aa_img_state"] = {}
    return st


def _refresh(canvas, item):
    info = _state(canvas).get(item)
    if info is None or not info["primary"]:
        return
    tkinter.Canvas.itemconfigure(canvas, item, image=_circle(canvas, info["r"], info["fill"]))


def _create_aa_circle(self, x_pos, y_pos, radius, angle=0, fill="white",
                      tags="", anchor=tkinter.CENTER):
    item = tkinter.Canvas.create_image(self, x_pos, y_pos, anchor=anchor, tags=tags)
    self.addtag_withtag("ctk_aa_circle_font_element", item)
    self._aa_circle_canvas_ids.add(item)
    _state(self)[item] = {"r": int(radius), "fill": fill, "primary": angle == 0}
    _refresh(self, item)
    return item


def _coords(self, tag_or_id, *args):
    if isinstance(tag_or_id, str) and "ctk_aa_circle_font_element" in self.gettags(tag_or_id):
        item = self.find_withtag(tag_or_id)[0]
    elif isinstance(tag_or_id, int) and tag_or_id in self._aa_circle_canvas_ids:
        item = tag_or_id
    else:
        return tkinter.Canvas.coords(self, tag_or_id, *args)
    coords = tkinter.Canvas.coords(self, item, *args[:2])
    if len(args) == 3:
        info = _state(self).get(item)
        if info is not None and info["r"] != int(args[2]):
            info["r"] = int(args[2])
            _refresh(self, item)
    return coords


def _itemconfig(self, tag_or_id, *args, **kwargs):
    ids = [tag_or_id] if isinstance(tag_or_id, int) else self.find_withtag(tag_or_id)
    for item in ids:
        if item in self._aa_circle_canvas_ids:
            kw = {k: v for k, v in kwargs.items() if k not in ("outline", "fill", "width")}
            if "fill" in kwargs:
                info = _state(self).get(item)
                if info is not None:
                    info["fill"] = kwargs["fill"]
                    _refresh(self, item)
            if kw or args:
                tkinter.Canvas.itemconfigure(self, item, *args, **kw)
        else:
            tkinter.Canvas.itemconfigure(self, item, *args, **kwargs)


def install():
    """Подменить отрисовку кругов CTk. Повторный вызов ничего не делает."""
    global _installed
    if _installed:
        return
    CTkCanvas.create_aa_circle = _create_aa_circle
    CTkCanvas.coords = _coords
    CTkCanvas.itemconfig = _itemconfig
    _installed = True
