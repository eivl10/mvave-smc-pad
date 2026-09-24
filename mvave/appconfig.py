"""Загрузка и сохранение конфигурации.

Состояние общее между Tk-потоком и BLE-потоком. Импортировать МОДУЛЬ, а не имя:
    from mvave import appconfig   →  appconfig.config[...]
`from mvave.appconfig import config` связывает имя со значением на момент импорта,
и переприсваивание `config` (миграция схемы) до импортёра уже не дойдёт.
"""
import json
import os
import shutil
import sys
import time

# Путь считается от КОРНЯ проекта, а не от каталога этого модуля.
# В собранном .exe __file__ указывает во временную распаковку (_MEIPASS),
# которая стирается при выходе, — настройки жили бы до первого закрытия.
# Поэтому там конфиг кладём рядом с самим exe.
if getattr(sys, "frozen", False):
    _ROOT = os.path.dirname(os.path.abspath(sys.executable))
else:
    _ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(_ROOT, "midi_config.json")

config = {}

def _migrate_v1_to_v2(old_conf):
    new_conf = {
        "version": 2,
        "bindings": {}
    }
    
    PALETTE = {
        "Off": None,
        "C1": "#ff0000", "C2": "#00ff00", "C3": "#0000ff", "C4": "#ffff00",
        "C5": "#00ffff", "C6": "#ff00ff", "C7": "#ffffff", "C8": "#ff8800", "C9": "#88ff00", 
        "C10": "#00ff88", "C11": "#0088ff", "C12": "#8800ff", "C13": "#ff0088", 
        "C14": "#ff8888", "C15": "#88ff88", "C16": "#8888ff"
    }

    def map_action(action_str):
        if not action_str or action_str == "None": return "none", None
        if action_str == "Громкость (Крутилка)": return "audio.volume", None
        if action_str == "Скролл (Крутилка)": return "scroll.wheel", None
        if action_str == "Volume Up": return "media.volume_up", None
        if action_str == "Volume Down": return "media.volume_down", None
        if action_str == "Play/Pause": return "media.play_pause", None
        if action_str == "Next Track": return "media.next_track", None
        if action_str == "Prev Track": return "media.prev_track", None
        if action_str == "Mute": return "media.mute", None
        if action_str.startswith("Custom: "): return "custom.hotkey", action_str[8:]
        if action_str.startswith("Run: "): return "custom.run", action_str[5:]
        return "none", None

    for k, v in old_conf.items():
        if not isinstance(v, dict): continue
        if k.startswith("pad_") or k.startswith("btn_"):
            act_str = v.get("action")
            action, param = map_action(act_str)
            color = v.get("color", "Off")
            new_color = PALETTE.get(color, None)
            
            b = {
                "action": action,
                "param": param,
                "color": new_color
            }
            if "midi_id" in v:
                b["midi_id"] = v["midi_id"]
                
            if action == "none" and act_str and act_str != "None":
                b["legacy_action"] = act_str
            
            new_conf["bindings"][k] = b
            
        elif k.startswith("knob_"):
            act_str = v.get("action")
            action, param = map_action(act_str)
            b = {
                "mode": "delta",
                "action": action,
                "param": param
            }
            if "midi_id" in v:
                b["midi_id"] = v["midi_id"]
                
            if action == "none" and act_str and act_str != "None":
                b["legacy_action"] = act_str
                
            new_conf["bindings"][k] = b
            
    return new_conf

#: сюда попадает текст последней ошибки загрузки, чтобы отказ не был немым.
last_error = None


def _atomic_write(path, data):
    tmp_path = path + ".tmp"
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def save_config():
    """Атомарная запись: временный файл + os.replace.

    save_config() зовётся на КАЖДОЙ ноте и КАЖДОМ CC. Прямая запись поверх
    боевого файла означала, что краш в момент записи рвёт конфиг, а немой
    except в загрузке потом стирает все настройки. os.replace на Windows
    атомарен в пределах тома.
    """
    _atomic_write(CONFIG_FILE, config)


# ── Перенос настроек между машинами ──────────────────────────────────────────
# Ключи, которые принадлежат этой машине, а не настройке: адрес контроллера
# у каждого свой, переносить его — значит сломать подключение на новом ПК.
_MACHINE_KEYS = ("ble_address",)
# Настройки самой программы, а не раскладки: смена пресета не должна
# перекрашивать окно. Свои цвета палитры при этом не теряются, а
# объединяются с пришедшими — см. import_config.
_APP_KEYS = ("theme", "window")   # window — размер и место окна этой машины
EXPORT_MARK = "mvave-smc-pad"
MAX_CUSTOM_COLORS = 16


def presets_dir():
    """Папка пресетов — рядом с конфигом (в exe — рядом с exe).

    Считается от CONFIG_FILE при каждом вызове: тесты подменяют путь
    конфига, и пресеты должны уехать вместе с ним.
    """
    return os.path.join(os.path.dirname(os.path.abspath(CONFIG_FILE)), "presets")


def backups_dir():
    return os.path.join(presets_dir(), "_backups")


def export_config(path):
    data = {k: v for k, v in config.items()
            if k not in _MACHINE_KEYS and k not in _APP_KEYS}
    data["app"] = EXPORT_MARK
    data["exported_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _atomic_write(path, data)


def _missing_paths(bindings):
    """Пути программ и папок из привязок, которых нет на этой машине."""
    out = []
    def check(uid, side):
        if not isinstance(side, dict):
            return
        p = side.get("param")
        act = side.get("action") or ""
        if p and act in ("custom.run", "custom.folder") and not os.path.exists(str(p)):
            out.append((uid, str(p)))
    for uid, b in bindings.items():
        check(uid, b)
        for slot in ("ccw", "cw"):
            check(uid, b.get(slot))
    return out


def import_config(path):
    """Заменить настройки содержимым файла.

    Возвращает (ok, отчёт). Текущий конфиг до замены копируется рядом —
    без подтверждённого бэкапа импорт не выполняется. Битый или чужой файл
    текущие настройки не трогает.
    """
    try:
        with open(path, 'r', encoding='utf-8') as f:
            loaded = json.load(f)
    except (OSError, ValueError) as e:
        return False, f"Файл не прочитан: {e}"
    if not isinstance(loaded, dict):
        return False, "Это не файл настроек: внутри не объект JSON."
    if "version" not in loaded:
        loaded = _migrate_v1_to_v2(loaded)   # экспорт самой первой версии
    if not isinstance(loaded.get("bindings"), dict):
        return False, "Это не файл настроек: нет раздела bindings."

    # Бэкапы — в presets/_backups: рядом с exe они копились россыпью.
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bak = os.path.join(backups_dir(), f"midi_config.before-import-{stamp}.json")
    try:
        os.makedirs(backups_dir(), exist_ok=True)
        _atomic_write(bak, config)
    except OSError as e:
        return False, f"Бэкап текущих настроек не создан, импорт отменён: {e}"
    if not os.path.exists(bak):
        return False, "Бэкап текущих настроек не появился, импорт отменён."

    keep = {k: config[k] for k in _MACHINE_KEYS + _APP_KEYS if k in config}
    new = {k: v for k, v in loaded.items()
           if k not in ("app", "exported_at") and k not in _MACHINE_KEYS + _APP_KEYS}
    new.update(keep)
    colors = [c for c in (config.get("custom_colors") or []) + (loaded.get("custom_colors") or [])
              if isinstance(c, str) and c.startswith("#") and len(c) == 7]
    colors = list(dict.fromkeys(c.lower() for c in colors))[:MAX_CUSTOM_COLORS]
    if colors:
        new["custom_colors"] = colors
    else:
        new.pop("custom_colors", None)
    config.clear()
    config.update(new)
    save_config()

    n = sum(1 for b in config["bindings"].values()
            if isinstance(b, dict) and (b.get("action") not in (None, "none")
                                        or b.get("ccw") or b.get("cw")))
    lines = [f"Загружено назначений: {n}.",
             f"Прежние настройки сохранены: {os.path.basename(bak)}"]
    missing = _missing_paths(config["bindings"])
    if missing:
        lines.append("")
        lines.append("На этом компьютере не найдены (назначения оставлены, "
                     "путь можно поправить в панели справа):")
        lines += [f"  {uid}: {p}" for uid, p in missing]
    return True, "\n".join(lines)


def load_config():
    """Читает конфиг, при необходимости мигрирует v1 → v2.

    Ловится только то, что означает «файл нечитаемый» — битый JSON и отказ
    файловой системы. Всё остальное (ошибка в самой миграции) обязано всплыть
    громко: немой except уже один раз спрятал то, что миграция не сохраняется.
    """
    global last_error
    last_error = None
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
        except (OSError, ValueError) as e:
            last_error = f"конфиг не прочитан: {e}"
            loaded = None

        if loaded is not None:
            if "version" not in loaded:
                # Бэкап — часть операции, а не опция: без него миграция
                # необратима. Не сохранился — не мигрируем.
                bak_path = CONFIG_FILE.replace(".json", ".v1.bak")
                try:
                    shutil.copy2(CONFIG_FILE, bak_path)
                    backed_up = os.path.exists(bak_path)
                except OSError as e:
                    last_error = f"бэкап не создан, миграция отменена: {e}"
                    backed_up = False
                if backed_up:
                    config.clear()
                    config.update(_migrate_v1_to_v2(loaded))
                    save_config()
                else:
                    config.clear()
                    config.update(loaded)
            else:
                config.clear()
                config.update(loaded)

    if not config:
        config.clear()
        config.update({"version": 2, "bindings": {}})


# Вызывать ТОЛЬКО после определения save_config: load_config() зовёт её при
# миграции. Раньше вызов стоял выше по файлу, NameError глотался немым except,
# и мигрированный конфиг никогда не доезжал до диска — см. FIXES.md.
load_config()
