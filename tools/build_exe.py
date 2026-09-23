"""Сборка одного .exe для компьютера без Python.

    python tools/build_exe.py

Результат: release/SMC-PAD.exe. Промежуточные файлы PyInstaller — во
временной папке, в проекте не остаются. Настройки программа хранит рядом с exe
(midi_config.json) — см. appconfig.CONFIG_FILE, ветка sys.frozen.
Нужен pyinstaller (pip install pyinstaller), в requirements.txt его нет:
приложению он не нужен, только сборке.
"""
import os
import sys
import tempfile

import PyInstaller.__main__

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
WORK = os.path.join(tempfile.gettempdir(), "smc-pad-build")
# Папку можно передать аргументом: пока старый exe запущен, его файл занят,
# и сборка поверх него падает — тогда собираем рядом и меняем после выхода.
OUT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(ROOT, "release")

PyInstaller.__main__.run([
    os.path.join(ROOT, "midi_gui.py"),
    "--distpath", OUT,
    "--workpath", WORK,
    "--specpath", WORK,
    "--name", "SMC-PAD",
    "--onefile",
    "--windowed",                 # без чёрного окна консоли
    "--noconfirm",
    "--clean",
    # specpath вынесен из проекта — пути к файлам только абсолютные
    "--icon", os.path.join(ROOT, "mvave_icon.ico"),
    # иконка окна и трея читается рядом с midi_gui.py, в распаковке это _MEIPASS
    "--add-data", f"{os.path.join(ROOT, 'mvave_icon.ico')}{os.pathsep}.",
    # темы и шрифты CustomTkinter лежат json/otf-файлами внутри пакета
    "--collect-data", "customtkinter",
    # pynput выбирает бэкенд динамически — PyInstaller его сам не видит
    "--hidden-import", "pynput.keyboard._win32",
    "--hidden-import", "pynput.mouse._win32",
    "--hidden-import", "pystray._win32",
])
print("\nГотово:", os.path.join(OUT, "SMC-PAD.exe"), file=sys.stderr)
