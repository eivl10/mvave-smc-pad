"""Кадры крутилки: PIL с запасом ×4, уменьшение LANCZOS.

tk.Canvas рисует овалы и линии без сглаживания — кольцо крутилки выходило
ступеньками. Здесь каждый кадр рисуется крупно и уменьшается, край
получается мягким на любом фоне.

Кольцо — сплошной декор ровной толщины, НЕ дуга-прогресс: крутилка
бесконечная, упоров нет (FIXES «UIKnob — бесконечное вращение»).
Угол квантуется шагом 5° — ровно столько даёт один щелчок энкодера.
"""
import math

from PIL import Image, ImageDraw, ImageTk

SS = 4          # запас по размеру
STEP = 5        # градусов на кадр
_cache = {}     # ключ цветов и размера → {угол: PhotoImage}


def _rgb(hx):
    hx = hx.lstrip("#")
    return tuple(int(hx[i:i + 2], 16) for i in (0, 2, 4))


def _mix(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _render(size, angle, bg, body, ring, tick, accent):
    S = size * SS
    c = S / 2
    r = (size / 2 - 4) * SS            # как у прежнего UIKnob: отступ 4 px
    ring_w = 2 * SS

    im = Image.new("RGB", (S, S), _rgb(bg))

    # Тело: вертикальный градиент — чуть светлее сверху, чуть темнее снизу.
    b = _rgb(body)
    top, bot = _mix(b, (255, 255, 255), 0.10), _mix(b, (0, 0, 0), 0.14)
    grad = Image.new("RGB", (1, S))
    for y in range(S):
        grad.putpixel((0, y), _mix(top, bot, y / (S - 1)))
    grad = grad.resize((S, S))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).ellipse((c - r, c - r, c + r, c + r), fill=255)
    im.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(im)
    # Кольцо: ровная окружность по краю тела.
    d.ellipse((c - r, c - r, c + r, c + r), outline=_rgb(ring), width=ring_w)

    # Указатель от центра к кольцу, со скруглёнными концами.
    a = math.radians(angle)
    L = r - ring_w - 2 * SS
    ix, iy = c + L * math.cos(a), c - L * math.sin(a)
    lw = 2 * SS
    d.line((c, c, ix, iy), fill=_rgb(tick), width=lw)
    for x, y in ((c, c), (ix, iy)):
        d.ellipse((x - lw / 2, y - lw / 2, x + lw / 2, y + lw / 2), fill=_rgb(tick))
    # Точка-акцент на конце указателя.
    dr = 2.5 * SS
    d.ellipse((ix - dr, iy - dr, ix + dr, iy + dr), fill=_rgb(accent))

    return im.resize((size, size), Image.LANCZOS)


def frame(master, size, angle, bg, body, ring, tick, accent):
    """PhotoImage крутилки под угол (градусы, против часовой от 3 часов)."""
    key = (id(master.tk), size, bg, body, ring, tick, accent)
    frames = _cache.setdefault(key, {})
    q = int(round(angle / STEP) * STEP) % 360
    img = frames.get(q)
    if img is None:
        img = ImageTk.PhotoImage(_render(size, q, bg, body, ring, tick, accent), master=master)
        frames[q] = img
    return img
