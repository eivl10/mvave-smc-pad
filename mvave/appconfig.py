"""Загрузка и сохранение конфигурации.

Состояние общее между Tk-потоком и BLE-потоком. Импортировать МОДУЛЬ, а не имя:
    from mvave import appconfig   →  appconfig.config[...]
`from mvave.appconfig import config` связывает имя со значением на момент импорта,
и переприсваивание `config` (миграция схемы) до импортёра уже не дойдёт.
"""
import json
import os
import shutil

# Путь считается от КОРНЯ проекта, а не от каталога этого модуля.
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


def save_config():
    """Атомарная запись: временный файл + os.replace.

    save_config() зовётся на КАЖДОЙ ноте и КАЖДОМ CC. Прямая запись поверх
    боевого файла означала, что краш в момент записи рвёт конфиг, а немой
    except в загрузке потом стирает все настройки. os.replace на Windows
    атомарен в пределах тома.
    """
    tmp_path = CONFIG_FILE + ".tmp"
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, CONFIG_FILE)


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
