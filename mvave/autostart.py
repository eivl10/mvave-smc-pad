"""Автозапуск с Windows и ярлык на рабочем столе.

Автозапуск — значение в `HKCU\\...\\CurrentVersion\\Run`, а не ярлык в папке
«Автозагрузка»: ярлык делался через `win32com`, а pywin32 в exe не
собирается — в exe автозапуск не работал вовсе. Реестр доступен через
стандартный `winreg`.

Ярлык на рабочем столе создаётся PowerShell-ом через тот же COM-объект
`WScript.Shell` — без pywin32, поэтому работает и в exe. Факт создания
проверяется по файлу на диске, а не по коду возврата.

Пути вычисляются, не зашиваются: старый `create_shortcut.vbs` сломался
именно на захардкоженных путях.
"""
import ctypes
import os
import subprocess
import sys
import uuid
import winreg

APP_NAME = "M-Vave SMC-PAD"
FROZEN = bool(getattr(sys, "frozen", False))
_ROOT = (os.path.dirname(sys.executable) if FROZEN
         else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(_ROOT, "midi_gui.py")
ICON = os.path.join(_ROOT, "mvave_icon.ico")

# Подменяется в smoke_ui на тестовый ключ: боевой реестр прогон не трогает
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

_FOLDERID = {"Desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
             "Startup": "{B97D20BB-F46A-4C97-BA10-5E3608430854}"}


def pythonw() -> str:
    """Путь к pythonw.exe рядом с текущим интерпретатором."""
    exe = sys.executable
    cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return cand if os.path.exists(cand) else exe


def _target():
    """(программа, аргументы) для запуска приложения на этой машине."""
    if FROZEN:
        return sys.executable, ""
    return pythonw(), f'"{SCRIPT}"'


def command() -> str:
    """Строка автозапуска: окно стартует спрятанным в трей."""
    exe, args = _target()
    return " ".join(p for p in (f'"{exe}"', args, "--tray") if p)


# ── Автозапуск ────────────────────────────────────────────────────────────────
def _read():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            v, _ = winreg.QueryValueEx(k, APP_NAME)
            return v
    except OSError:
        return None


def state() -> str:
    """'on' — записан этот запуск; 'other' — записан другой путь
    (exe переехал); 'off' — не записан."""
    v = _read()
    if v is None:
        return "off"
    return "on" if os.path.normcase(v.strip()) == os.path.normcase(command()) else "other"


def recorded() -> str:
    return _read() or ""


def set_autostart(enable: bool) -> str:
    """Включить (путь перезаписывается) или выключить. Возвращает state()
    после записи — проверка чтением, а не верой в отсутствие исключения."""
    if enable:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, command())
    else:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, APP_NAME)
        except FileNotFoundError:
            pass
    return state()


# ── Папки и ярлыки ────────────────────────────────────────────────────────────
class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]


def known_folder(name: str) -> str:
    """Путь известной папки (Desktop, Startup) через SHGetKnownFolderPath —
    учитывает перенос папки пользователем и OneDrive."""
    u = uuid.UUID(_FOLDERID[name])
    g = _GUID(u.fields[0], u.fields[1], u.fields[2],
              (ctypes.c_ubyte * 8).from_buffer_copy(u.bytes[8:]))
    p = ctypes.c_wchar_p()
    hr = ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None,
                                                    ctypes.byref(p))
    if hr != 0:
        raise OSError(f"SHGetKnownFolderPath({name}) = {hr:#x}")
    try:
        return p.value
    finally:
        ctypes.windll.ole32.CoTaskMemFree(p)


def desktop_dir() -> str:
    return known_folder("Desktop")


def startup_dir() -> str:
    return known_folder("Startup")


def _ps_quote(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def create_shortcut(lnk_path: str) -> str:
    """Создать .lnk на приложение и убедиться, что он появился на диске."""
    exe, args = _target()
    icon = exe if FROZEN else ICON
    ps = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut("
          f"{_ps_quote(lnk_path)}); "
          f"$s.TargetPath = {_ps_quote(exe)}; $s.Arguments = {_ps_quote(args)}; "
          f"$s.WorkingDirectory = {_ps_quote(_ROOT)}; "
          f"$s.Description = {_ps_quote('Назначение действий на пэды M-Vave SMC-PAD')}; ")
    if os.path.exists(icon):
        ps += f"$s.IconLocation = {_ps_quote(icon)}; "
    ps += "$s.Save()"
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                   capture_output=True, timeout=30,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if not os.path.exists(lnk_path):
        raise OSError(f"ярлык не создан: {lnk_path}")
    return lnk_path


def create_desktop_shortcut() -> str:
    return create_shortcut(os.path.join(desktop_dir(), f"{APP_NAME}.lnk"))


def legacy_startup_shortcut():
    """Старый ярлык автозагрузки (до перехода на реестр), если он есть.
    Вместе со значением в реестре он запускал бы программу дважды."""
    try:
        p = os.path.join(startup_dir(), f"{APP_NAME}.lnk")
    except OSError:
        return None
    return p if os.path.exists(p) else None
