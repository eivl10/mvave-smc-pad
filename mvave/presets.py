"""Пресеты — сохранённые настройки, доступные прямо из программы.

Пресет — тот же JSON, что даёт экспорт (appconfig.export_config), лежит в
папке presets/ рядом с конфигом. Загружается через appconfig.import_config:
с бэкапом текущих настроек и отчётом о путях, которых нет на этой машине.
Файл, однажды загруженный через «Импорт из файла», копируется сюда же —
второй раз его открывают из списка, без окна выбора файла.
"""
import json
import os
import re

from mvave import appconfig

_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def clean_name(name):
    """Имя пресета → допустимое имя файла без расширения."""
    name = _BAD.sub(" ", str(name or "")).strip().strip(".")
    return re.sub(r"\s+", " ", name)[:60]


def path_for(name):
    return os.path.join(appconfig.presets_dir(), clean_name(name) + ".json")


def _count(path):
    try:
        with open(path, encoding="utf-8") as f:
            b = json.load(f).get("bindings", {})
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(b, dict):
        return None
    return sum(1 for v in b.values()
               if isinstance(v, dict) and (v.get("action") not in (None, "none")
                                           or v.get("ccw") or v.get("cw")))


def list_presets():
    """[{name, path, mtime, count}] — новые сверху. Бэкапы не показываются."""
    d = appconfig.presets_dir()
    if not os.path.isdir(d):
        return []
    out = []
    for fn in os.listdir(d):
        p = os.path.join(d, fn)
        if not fn.lower().endswith(".json") or not os.path.isfile(p):
            continue
        out.append({"name": fn[:-5], "path": p, "mtime": os.path.getmtime(p),
                    "count": _count(p)})
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


def exists(name):
    return os.path.exists(path_for(name))


def save_preset(name):
    """Текущие настройки → presets/<name>.json. Возвращает путь."""
    if not clean_name(name):
        raise ValueError("пустое имя")
    os.makedirs(appconfig.presets_dir(), exist_ok=True)
    p = path_for(name)
    appconfig.export_config(p)
    return p


def load_preset(path):
    return appconfig.import_config(path)


def remember_file(path):
    """Положить копию импортируемого файла в пресеты. Возвращает путь копии.

    Файл уже из папки пресетов не копируется. Совпало имя, а содержимое
    другое — к имени добавляется номер, чужой пресет не затирается.
    """
    d = appconfig.presets_dir()
    src = os.path.abspath(path)
    if os.path.dirname(src).lower() == os.path.abspath(d).lower():
        return src
    os.makedirs(d, exist_ok=True)
    base = clean_name(os.path.splitext(os.path.basename(src))[0]) or "Пресет"
    with open(src, "rb") as f:
        data = f.read()
    n = 1
    while True:
        name = base if n == 1 else f"{base} ({n})"
        dst = os.path.join(d, name + ".json")
        if not os.path.exists(dst):
            with open(dst, "wb") as f:
                f.write(data)
            return dst
        with open(dst, "rb") as f:
            if f.read() == data:
                return dst
        n += 1


def delete_preset(path):
    """В Корзину Windows, а не насовсем: пресет можно вернуть оттуда.

    Корзина недоступна (не Windows, сетевой диск) — файл остаётся на месте,
    вызывающий получает False. Удалять мимо корзины молча нельзя.
    """
    try:
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                        ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                        ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                        ("hNameMappings", ctypes.c_void_p),
                        ("lpszProgressTitle", wintypes.LPCWSTR)]
        FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = \
            3, 0x4, 0x10, 0x40, 0x400
        op = SHFILEOPSTRUCTW(None, FO_DELETE, os.path.abspath(path) + "\0", None,
                             FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI,
                             False, None, None)
        rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        return rc == 0 and not os.path.exists(path)
    except Exception:
        return False
