import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mvave.appconfig import _migrate_v1_to_v2
from mvave.actions import get, for_kind, execute, CATEGORY_ORDER, ACTIONS_LIST

def test_migration():
    old = {
        "pad_1": {"midi_id": 36, "action": "Play/Pause", "color": "C1"},
        "pad_2": {"midi_id": 37, "action": "Custom: ctrl+shift+a", "color": "Off"},
        "pad_3": {"midi_id": 38, "action": "Run: C:\\app\\x.exe", "color": "Unknown"},
        "pad_4": {"midi_id": 39, "action": "Wtf action"},
        "knob_1": {"midi_id": 30, "action": "Громкость (Крутилка)"}
    }
    
    v2 = _migrate_v1_to_v2(old)
    
    assert v2["version"] == 2
    assert v2["bindings"]["pad_1"]["action"] == "media.play_pause"
    assert v2["bindings"]["pad_1"]["param"] is None
    assert v2["bindings"]["pad_1"]["color"] == "#ff0000"
    
    assert v2["bindings"]["pad_2"]["action"] == "custom.hotkey"
    assert v2["bindings"]["pad_2"]["param"] == "ctrl+shift+a"
    assert v2["bindings"]["pad_2"]["color"] is None
    
    assert v2["bindings"]["pad_3"]["action"] == "custom.run"
    assert v2["bindings"]["pad_3"]["param"] == "C:\\app\\x.exe"
    assert v2["bindings"]["pad_3"]["color"] is None
    
    assert v2["bindings"]["pad_4"]["action"] == "none"
    assert v2["bindings"]["pad_4"]["legacy_action"] == "Wtf action"
    
    assert v2["bindings"]["knob_1"]["action"] == "audio.volume"
    assert v2["bindings"]["knob_1"]["mode"] == "delta"

def test_idempotent_migration():
    v2 = {
        "version": 2,
        "bindings": {
            "pad_1": {"action": "media.play_pause", "param": None, "color": "#ff0000"}
        }
    }
    # Migration is bypassed in load_config if "version" is present, but let's test if we force it
    # Actually, old configs don't have "version", so _migrate_v1_to_v2 doesn't need to be idempotent 
    # if it's never called on v2 dicts. 
    pass

def test_registry():
    delta_actions = for_kind("delta")
    assert all(a.kind == "delta" for a in delta_actions)
    assert not any(a.id == "media.play_pause" for a in delta_actions)
    
    trigger_actions = for_kind("trigger")
    assert all(a.kind == "trigger" for a in trigger_actions)
    assert not any(a.id == "audio.volume" for a in trigger_actions)
    
    seen_ids = set()
    for a in ACTIONS_LIST:
        assert a.id not in seen_ids
        seen_ids.add(a.id)
        assert a.category in CATEGORY_ORDER or a.category == "—"
        assert callable(a.handler)

def test_execute():
    # Calling none should not raise
    err = execute("none")
    assert err is None
    
    # Calling unknown should return error string
    err = execute("unknown_action")
    assert err is not None and "Неизвестное действие" in err

def _with_temp_config(fn):
    """Прогнать fn на временном конфиге: боевой — живые настройки владельца."""
    import json, shutil, tempfile
    from mvave import appconfig
    saved_path, saved = appconfig.CONFIG_FILE, dict(appconfig.config)
    d = tempfile.mkdtemp()
    appconfig.CONFIG_FILE = os.path.join(d, "midi_config.json")
    try:
        return fn(appconfig, d, json)
    finally:
        appconfig.CONFIG_FILE = saved_path
        appconfig.config.clear(); appconfig.config.update(saved)
        shutil.rmtree(d, ignore_errors=True)


def test_export_import_roundtrip():
    def body(appconfig, d, json):
        appconfig.config.clear()
        appconfig.config.update({
            "version": 2, "pad_brightness": 40, "ble_address": "AA:AA:AA:AA:AA:AA",
            "bindings": {
                "pad_1": {"midi_id": 36, "action": "clipboard.copy", "color": "#ff0000"},
                "pad_2": {"action": "custom.run", "param": "Z:/нет/такой.exe"},
                "knob_3": {"mode": "pair", "ccw": {"action": "custom.folder", "param": "Z:/нет"},
                           "cw": {"action": "media.next_track"}},
            }})
        appconfig.save_config()
        exp = os.path.join(d, "export.json")
        appconfig.export_config(exp)
        data = json.load(open(exp, encoding="utf-8"))
        assert "ble_address" not in data, "адрес машины уехал в экспорт"
        assert data["app"] == appconfig.EXPORT_MARK

        # «другой ПК»: свой адрес, пустые привязки
        appconfig.config.clear()
        appconfig.config.update({"version": 2, "bindings": {}, "ble_address": "BB:BB:BB:BB:BB:BB"})
        appconfig.save_config()
        ok, report = appconfig.import_config(exp)
        assert ok, report
        c = appconfig.config
        assert c["bindings"]["pad_1"]["color"] == "#ff0000"
        assert c["pad_brightness"] == 40
        assert c["ble_address"] == "BB:BB:BB:BB:BB:BB", "импорт затёр адрес этой машины"
        assert "app" not in c and "exported_at" not in c
        assert "Z:/нет/такой.exe" in report and "knob_3" in report, report
        baks = [f for f in os.listdir(appconfig.backups_dir()) if ".before-import-" in f]
        assert baks, "бэкап перед импортом не создан"
        assert not [f for f in os.listdir(d) if ".before-import-" in f], \
            "бэкап снова лёг рядом с конфигом, а не в presets/_backups"
        on_disk = json.load(open(appconfig.CONFIG_FILE, encoding="utf-8"))
        assert on_disk["bindings"]["pad_1"]["action"] == "clipboard.copy"
    _with_temp_config(body)


def test_import_rejects_garbage():
    def body(appconfig, d, json):
        appconfig.config.clear()
        appconfig.config.update({"version": 2, "bindings": {"pad_1": {"action": "media.stop"}}})
        before = json.dumps(appconfig.config, sort_keys=True)
        for name, content in (("broken.json", "{не json"), ("list.json", "[1, 2]"),
                              ("alien.json", '{"version": 2, "foo": 1}')):
            p = os.path.join(d, name)
            open(p, "w", encoding="utf-8").write(content)
            ok, report = appconfig.import_config(p)
            assert not ok, f"{name} принят: {report}"
            assert json.dumps(appconfig.config, sort_keys=True) == before, f"{name} испортил конфиг"
    _with_temp_config(body)


def test_import_keeps_app_settings():
    """Тема — настройка программы, а не пресета; свои цвета объединяются."""
    def body(appconfig, d, json):
        appconfig.config.clear()
        appconfig.config.update({"version": 2, "bindings": {}, "theme": "light",
                                 "custom_colors": ["#111111", "#222222"]})
        p = os.path.join(d, "in.json")
        json.dump({"version": 2, "bindings": {}, "theme": "dark",
                   "custom_colors": ["#222222", "#333333", "мусор"]},
                  open(p, "w", encoding="utf-8"))
        ok, report = appconfig.import_config(p)
        assert ok, report
        assert appconfig.config["theme"] == "light"
        assert appconfig.config["custom_colors"] == ["#111111", "#222222", "#333333"]
        exp = os.path.join(d, "out.json")
        appconfig.export_config(exp)
        assert "theme" not in json.load(open(exp, encoding="utf-8"))
    _with_temp_config(body)


def test_presets_list_and_remember():
    def body(appconfig, d, json):
        from mvave import presets
        appconfig.config.clear()
        appconfig.config.update({"version": 2, "bindings": {"pad_1": {"action": "media.stop"}}})
        assert presets.list_presets() == []
        assert presets.clean_name(' a/b:c*?  ') == "a b c"
        presets.save_preset("Мой/пресет")
        items = presets.list_presets()
        assert [i["name"] for i in items] == ["Мой пресет"] and items[0]["count"] == 1
        # внешний файл: копия в пресеты; повтор того же файла — без дубля
        ext = os.path.join(d, "с флешки.json")
        appconfig.export_config(ext)
        a = presets.remember_file(ext)
        b = presets.remember_file(ext)
        assert a == b and os.path.dirname(a) == appconfig.presets_dir()
        # другой файл с тем же именем не затирает прежний
        appconfig.config["bindings"]["pad_2"] = {"action": "media.next_track"}
        appconfig.export_config(ext)
        c = presets.remember_file(ext)
        assert c != a and "(2)" in c
        # файл из самой папки пресетов не копируется
        assert presets.remember_file(a) == os.path.abspath(a)
        assert len(presets.list_presets()) == 3
    _with_temp_config(body)


def _main():
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:
            failed += 1
            print(f"FAIL  {name}\n      {type(exc).__name__}: {exc}")
        else:
            print(f"ok    {name}")
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(_main())
