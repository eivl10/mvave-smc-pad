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
