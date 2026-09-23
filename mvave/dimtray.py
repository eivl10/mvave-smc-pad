"""Клиент DimTray — управление яркостью мониторов с пэда или крутилки.

DimTray — отдельная программа автора, в этот репозиторий не входит: по иконке в трее на каждый монитор, колесо мыши меняет
яркость. Встроенный экран — настоящая подсветка через WMI, внешний — гамма-рампа.

Канал — именованный канал Windows, а не горячие клавиши. Клавиши здесь не
годятся: крутилка шлёт десятки событий в секунду, Windows часть их теряет
(на этом уже сломалась регулировка громкости), плюс любая глобальная
комбинация рано или поздно сталкивается с чужой.

Команды — строками:
    all <delta>\\n   сдвинуть все экраны на delta процентов
    reset\\n         вернуть все на 100%

Если DimTray не запущен, канала нет — действие возвращает текст ошибки,
а не падает.
"""
import os

PIPE = r"\\.\pipe\DimTrayCtl"

_handle = None


def _connect():
    global _handle
    if _handle is not None:
        return _handle
    # Открываем в двоичном режиме без буферизации: иначе команда зависнет
    # в буфере Python до закрытия, а крутилке нужен эффект сразу.
    _handle = open(PIPE, "wb", buffering=0)
    return _handle


def _close():
    global _handle
    try:
        if _handle is not None:
            _handle.close()
    except OSError:
        pass
    _handle = None


def available() -> bool:
    """Есть ли сервер на том конце.

    `os.path.exists` на пути именованного канала врёт — каталог каналов
    так не читается. Надёжно только перечисление `\\\\.\\pipe`.
    """
    try:
        return "DimTrayCtl" in os.listdir(r"\\.\pipe")
    except OSError:
        return False


def send(command: str) -> str | None:
    """Отправить команду. Возвращает текст ошибки либо None."""
    line = (command.strip() + "\n").encode("utf-8")
    for attempt in (1, 2):
        try:
            _connect().write(line)
            return None
        except OSError as e:
            _close()
            if attempt == 2:
                return ("DimTray не отвечает — запущен ли он? "
                        f"({e.__class__.__name__})")
    return None


def adjust_all(delta: int) -> str | None:
    return send(f"all {int(delta)}")


def reset_all() -> str | None:
    return send("reset")
