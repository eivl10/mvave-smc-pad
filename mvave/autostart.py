"""Ярлыки: на рабочем столе и в автозагрузке.

Ярлык всегда указывает на `pythonw.exe` — это тот же интерпретатор, что и
`sys.executable`, но без консольного окна. Пути вычисляются, не зашиваются:
старый `create_shortcut.vbs` сломался именно на захардкоженных путях, все
четыре указывали в чужой каталог.
"""
import os
import sys

APP_NAME = "M-Vave SMC-PAD"
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(_ROOT, "midi_gui.py")
ICON = os.path.join(_ROOT, "mvave_icon.ico")


def pythonw() -> str:
    """Путь к pythonw.exe рядом с текущим интерпретатором."""
    exe = sys.executable
    cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return cand if os.path.exists(cand) else exe


def _shell_folder(name: str) -> str:
    from win32com.client import Dispatch
    return Dispatch("WScript.Shell").SpecialFolders(name)


def desktop_dir() -> str:
    return _shell_folder("Desktop")


def startup_dir() -> str:
    return _shell_folder("Startup")


def create_shortcut(lnk_path: str) -> str:
    """Создать .lnk и убедиться, что он появился.

    Код возврата COM-вызова успехом не считается: проверяем файл на диске.
    """
    from win32com.client import Dispatch

    shell = Dispatch("WScript.Shell")
    sc = shell.CreateShortCut(lnk_path)
    sc.Targetpath = pythonw()
    sc.Arguments = f'"{SCRIPT}"'
    sc.WorkingDirectory = _ROOT
    sc.Description = "Назначение действий на пэды M-Vave SMC-PAD"
    if os.path.exists(ICON):
        sc.IconLocation = ICON
    sc.save()

    if not os.path.exists(lnk_path):
        raise OSError(f"ярлык не создан: {lnk_path}")
    return lnk_path


def create_desktop_shortcut() -> str:
    return create_shortcut(os.path.join(desktop_dir(), f"{APP_NAME}.lnk"))


def startup_path() -> str:
    return os.path.join(startup_dir(), f"{APP_NAME}.lnk")


def autostart_enabled() -> bool:
    return os.path.exists(startup_path())


def set_autostart(enable: bool) -> bool:
    path = startup_path()
    if enable:
        create_shortcut(path)
    elif os.path.exists(path):
        os.remove(path)
    return autostart_enabled()
