"""Рисует mvave_icon.ico — сетку 4×4 с одним подсвеченным пэдом.

Многослойный .ico: 16/32/48/64/128/256. Один слой 256 Windows ужимает сам,
и в трее получается мыло — поэтому каждый размер рисуется отдельно, а мелкие
упрощаются: на 16 px скругления и зазоры между пэдами не читаются.

Запускается вручную, в рантайме не нужен:
    python tools/make_icon.py
"""
import os

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "mvave_icon.ico")

SIZES = [16, 32, 48, 64, 128, 256]
PAD_OFF = (0x3a, 0x3f, 0x46, 255)      # неактивный пэд
PAD_ON = (0x00, 0xc8, 0xff, 255)       # подсвеченный
GLOW = (0x00, 0xc8, 0xff, 70)
LIT = (1, 2)                            # ряд, столбец подсвеченного пэда


def draw(size):
    # Рисуем крупно и уменьшаем: края получаются мягкими без ручного сглаживания.
    scale = 8 if size <= 64 else 2
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    margin = s * 0.09
    gap = s * 0.045 if size >= 32 else s * 0.03
    cell = (s - 2 * margin - 3 * gap) / 4
    radius = cell * (0.26 if size >= 32 else 0.18)

    for row in range(4):
        for col in range(4):
            x0 = margin + col * (cell + gap)
            y0 = margin + row * (cell + gap)
            box = (x0, y0, x0 + cell, y0 + cell)
            on = (row, col) == LIT
            if on and size >= 32:
                # Ореол вокруг активного пэда: на тёмной и на светлой панели
                # задач он одинаково заметен.
                halo = s * 0.03
                d.rounded_rectangle(
                    (box[0] - halo, box[1] - halo, box[2] + halo, box[3] + halo),
                    radius=radius + halo, fill=GLOW)
            d.rounded_rectangle(box, radius=radius,
                                fill=PAD_ON if on else PAD_OFF)

    return img.resize((size, size), Image.LANCZOS)


def main():
    layers = [draw(n) for n in SIZES]
    layers[-1].save(OUT, format="ICO",
                    sizes=[(n, n) for n in SIZES], append_images=layers[:-1])
    print(f"иконка: {OUT}")
    print(f"слоёв: {', '.join(str(n) for n in SIZES)}")


if __name__ == "__main__":
    main()
