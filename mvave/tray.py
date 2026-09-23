"""Значок в трее: приложение живёт там после закрытия и сворачивания окна.

pystray крутит свой цикл сообщений в отдельном потоке. Tk из чужого потока
трогать нельзя, поэтому пункты меню не зовут GUI напрямую, а кладут команду
в очередь — её разбирает цикл Tk (App.check_queue).
"""
import os
import queue

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:   # без pystray приложение работает как раньше, без трея
    pystray = None

SHOW = "show"
QUIT = "quit"


def _load_image(icon_path):
    try:
        if icon_path and os.path.exists(icon_path):
            return Image.open(icon_path)
    except Exception:
        pass
    # Запасная картинка: сетка 4x4, как пэды на контроллере.
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for r in range(4):
        for c in range(4):
            x, y = 4 + c * 15, 4 + r * 15
            d.rectangle([x, y, x + 12, y + 12], fill=(0, 170, 255, 255))
    return img


class Tray:
    def __init__(self, title, icon_path):
        self.commands = queue.SimpleQueue()
        self._icon = None
        if pystray is None:
            return
        menu = pystray.Menu(
            # default=True — пункт срабатывает по клику на значок.
            pystray.MenuItem("Показать окно", self._on_show, default=True),
            pystray.MenuItem("Выход", self._on_quit),
        )
        try:
            self._icon = pystray.Icon("mvave_smc_pad", _load_image(icon_path),
                                      title, menu)
            self._icon.run_detached()
        except Exception:
            self._icon = None

    @property
    def available(self):
        return self._icon is not None

    def _on_show(self, icon=None, item=None):
        self.commands.put(SHOW)

    def _on_quit(self, icon=None, item=None):
        self.commands.put(QUIT)

    def stop(self):
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
