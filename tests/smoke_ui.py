"""Дымовой прогон правок этого раунда на живом окне Tk, без железа.

Боевой конфиг НЕ трогаем: appconfig грузит его на импорте, а прогон назначает
действия и сохраняет. Уводим на временную копию ДО импорта midi_gui.
"""
import os
import shutil
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mvave import actions, appconfig

_real = appconfig.CONFIG_FILE
_tmp = os.path.join(tempfile.mkdtemp(), "midi_config.json")
if os.path.exists(_real):
    shutil.copy(_real, _tmp)
appconfig.CONFIG_FILE = _tmp
appconfig.load_config()
print(f"конфиг прогона: {_tmp}")

import tkinter as tk
import midi_gui

executed = []
def fake_execute(action_id, param=None, delta=0):
    executed.append((action_id, param, delta))
    return None
actions.execute = fake_execute

fails = []
def check(name, fn):
    try:
        r = fn()
        print(f"  OK     {name}" + (f"  → {r}" if r is not None else ""))
    except Exception:
        fails.append(name)
        print(f"  ПАДАЕТ {name}")
        traceback.print_exc(limit=3)

# Окно ДОЛЖНО быть отображено. На withdraw() ветки с winfo_ismapped() уходят
# в другую сторону, и баг с before= в режиме «влево/вправо» прогон не увидел.
root = tk.Tk()
root.geometry("1200x700+3000+3000")     # за краем экрана, но настоящее окно
root.update()
app = midi_gui.App(root)
root.update()
print(f"элементов на схеме: {len(app.ui_elements)}")
print(f"дерево действий отображено: {bool(app._action_tree.winfo_ismapped())}")

def drain_cmds():
    out = []
    while not midi_gui.ble.cmd_queue.empty():
        out.append(midi_gui.ble.cmd_queue.get_nowait())
    return out

print("\n── кнопки: значок не затирается назначением ────────────")
def btn_icon_survives():
    app.select_element("btn_4")
    app._action_tree.selection_set("edit.undo")
    app._on_tree_select(None)
    el = app.ui_elements["btn_4"]
    assert el["icon_lbl"].cget("text") == el["icon"], "значок затёрт"
    assert el["lbl"].cget("text"), "подпись действия пустая"
    return f"значок {el['icon_lbl'].cget('text')!r} + действие {el['lbl'].cget('text')!r}"
check("значок кнопки остаётся виден", btn_icon_survives)

print("\n── привязка MIDI к кнопке ──────────────────────────────")
def unbound_cc_is_reported():
    executed.clear()
    midi_gui.ble.msg_queue.put("cc:26:127")
    app.check_queue()
    root.update_idletasks()
    assert not executed, "непривязанный CC что-то запустил"
    txt = app.last_input_var.get()
    assert "не привязан" in txt, f"молчание вместо подсказки: {txt!r}"
    return txt
check("непривязанный CC виден в шапке", unbound_cc_is_reported)

def learn_binds_cc_to_button():
    app.select_element("btn_4")
    app._on_learn_toggle()
    assert app._learn_uid == "btn_4"
    midi_gui.ble.msg_queue.put("cc:26:127")
    app.check_queue()
    root.update_idletasks()
    assert app._learn_uid is None, "режим привязки не выключился"
    b = appconfig.config["bindings"]["btn_4"]
    assert b.get("midi_id") == 26 and b.get("midi_kind") == "cc", b
    return (b["midi_kind"], b["midi_id"], app._learn_lbl.cget("text"))
check("«Привязать» ловит CC 26 на кнопку 4", learn_binds_cc_to_button)

def bound_cc_fires_action():
    import time as _t
    _t.sleep(0.12)                     # переждать дебаунс 80 мс
    executed.clear()
    midi_gui.ble.msg_queue.put("cc:26:127")
    app.check_queue()
    root.update_idletasks()
    assert executed, "привязанная кнопка не сработала"
    return executed[-1]
check("нажатие кнопки запускает действие", bound_cc_fires_action)

def release_does_not_fire():
    executed.clear()
    midi_gui.ble.msg_queue.put("cc:26:0")   # отпускание
    app.check_queue()
    root.update_idletasks()
    assert not executed, "сработало на отпускании"
    return "отпускание проигнорировано"
check("отпускание кнопки не запускает действие", release_does_not_fire)

def learned_cc_beats_knob_map():
    # CC 33 штатно принадлежит крутилке 4. Явная привязка должна победить.
    app.select_element("btn_5")
    app._on_learn_toggle()
    midi_gui.ble.msg_queue.put("cc:33:127")
    app.check_queue()
    root.update_idletasks()
    assert app.ui_elements["btn_5"]["midi_id"] == 33
    return app._learned_uid("cc", 33)
check("ручная привязка главнее карты крутилок", learned_cc_beats_knob_map)

def forget_clears_binding():
    app.select_element("btn_5")
    app._on_learn_forget()
    assert app.ui_elements["btn_5"]["midi_id"] is None
    assert "midi_id" not in appconfig.config["bindings"].get("btn_5", {})
    return app._learn_lbl.cget("text")
check("«Забыть» снимает привязку", forget_clears_binding)

print("\n── пэды: старое поведение не сломано ───────────────────")
def pad_note_still_works():
    import time as _t
    _t.sleep(0.12)
    executed.clear()
    app.select_element("pad_1")
    app._action_tree.selection_set("media.play_pause")
    app._on_tree_select(None)
    midi_gui.ble.msg_queue.put("note:36:127")
    app.check_queue()
    root.update_idletasks()
    assert executed, "пэд не сработал"
    assert appconfig.config["bindings"]["pad_1"]["midi_id"] == 36
    return executed[-1]
check("нота 36 запускает pad_1", pad_note_still_works)

def debounce_still_works():
    executed.clear()
    midi_gui.ble.msg_queue.put("note:36:127")
    app.check_queue()
    root.update_idletasks()
    assert not executed, "дебаунс 80 мс сломан — FIXES #8"
    return "повтор подавлен"
check("дебаунс 80 мс на месте", debounce_still_works)

def knob_delta_still_works():
    executed.clear()
    app.select_element("knob_1")
    app._action_tree.selection_set("audio.volume")
    app._on_tree_select(None)
    for v in (10, 11, 12):
        midi_gui.ble.msg_queue.put(f"cc:30:{v}")
    app.check_queue()
    root.update_idletasks()
    assert executed and executed[-1][2] == 1, f"дельта неверна: {executed}"
    return executed
check("абсолютный декодер CC даёт дельту +1", knob_delta_still_works)

def knob_wraparound():
    executed.clear()
    midi_gui.ble.msg_queue.put("cc:30:127")   # 12 → 127 это -13, не +115
    app.check_queue()
    root.update_idletasks()
    assert executed[-1][2] < 0, f"заворот ±64 сломан: {executed}"
    return executed[-1][2]
check("заворот 0↔127 через ±64", knob_wraparound)

print("\n── цикл обработки не должен умирать ────────────────────")
def loop_survives_exception():
    boom = {"n": 0}
    real = app._on_midi
    def exploding(kind, mid, val):
        boom["n"] += 1
        raise RuntimeError("подстроенный сбой")
    app._on_midi = exploding
    midi_gui.ble.msg_queue.put("note:36:127")
    app.check_queue()             # не должен пробросить исключение
    app._on_midi = real
    executed.clear()
    import time as _t
    _t.sleep(0.12)
    midi_gui.ble.msg_queue.put("note:36:127")
    app.check_queue()
    root.update_idletasks()
    assert executed, "после сбоя обработка не восстановилась"
    return f"сбой пойман, очередь жива ({boom['n']} падение)"
check("исключение не убивает обработку MIDI", loop_survives_exception)

print("\n── цвет ────────────────────────────────────────────────")
def color_goes_to_device():
    app.select_element("pad_2")
    drain_cmds()
    app._apply_color("#ff0000")
    cmds = drain_cmds()
    assert ("rgb", 2, "#ff0000") in cmds, f"цвет не ушёл в очередь: {cmds}"
    assert appconfig.config["bindings"]["pad_2"]["color"] == "#ff0000"
    return cmds
check("выбор цвета шлёт кадр на устройство", color_goes_to_device)

def brightness_does_not_compound():
    app.select_element("pad_2")
    app._apply_color("#ff0000")
    drain_cmds()
    app._brightness_var.set(50); app._on_brightness_change(50)
    first = appconfig.config["bindings"]["pad_2"]["color"]
    app._brightness_var.set(50); app._on_brightness_change(50)
    second = appconfig.config["bindings"]["pad_2"]["color"]
    assert first == second, f"яркость множится сама на себя: {first} → {second}"
    return (first, second)
check("ползунок яркости не гасит пэд повторно", brightness_does_not_compound)

def select_does_not_resend():
    app.select_element("pad_2")
    drain_cmds()
    app.select_element("pad_3")
    app.select_element("pad_2")
    cmds = drain_cmds()
    assert not cmds, f"простой клик по пэду шлёт кадры: {cmds}"
    return "клик по пэду ничего не шлёт"
check("выбор пэда не переписывает его цвет", select_does_not_resend)

def color_status_from_device():
    midi_gui.ble.msg_queue.put("color:нет")
    app.check_queue()
    root.update_idletasks()
    a = app._color_status_var.get()
    midi_gui.ble.msg_queue.put("color:есть")
    app.check_queue()
    root.update_idletasks()
    return (a, app._color_status_var.get())
check("статус канала цвета доезжает в шапку", color_status_from_device)

print("\n── банк ────────────────────────────────────────────────")
def bank_from_device():
    drain_cmds()
    midi_gui.ble.msg_queue.put("state:0:5")
    app.check_queue()
    root.update_idletasks()
    assert app._current_bank == 5, app._current_bank
    cmds = drain_cmds()
    assert ("bank", None, 5) in cmds, f"банк не доехал до транспорта: {cmds}"
    return app._bank_var.get()
check("живое состояние устройства задаёт банк", bank_from_device)

print("\n── очистка не сносит привязку к железу ─────────────────")
def clear_keeps_hardware():
    app.select_element("btn_4")
    app.clear_current()
    b = appconfig.config["bindings"].get("btn_4", {})
    assert b.get("midi_id") == 26, f"привязка к CC 26 потеряна: {b}"
    assert b.get("action") in (None, "none"), b
    return b
check("«Очистить» сохраняет привязку MIDI", clear_keeps_hardware)

print("\n── конфиг остаётся записываемым ────────────────────────")
def every_action_serializable():
    import json
    app.select_element("pad_8")
    bad = []
    for a in actions.ACTIONS_LIST:
        if a.kind != "trigger" or a.id == "none":
            continue
        app._assign_action(a)
        try:
            json.dumps(appconfig.config, ensure_ascii=False)
        except TypeError as e:
            bad.append((a.id, str(e)))
    assert not bad, f"не сериализуется после: {bad[:3]}"
    return f"проверено действий: {len([a for a in actions.ACTIONS_LIST if a.kind == 'trigger']) - 1}"
check("любое действие оставляет конфиг записываемым", every_action_serializable)

print("\n── режим «влево/вправо» на ОТОБРАЖЁННОМ окне ───────────")
def pair_toggle_on_mapped_window():
    app.select_element("knob_3")
    root.update()
    assert app._action_tree.winfo_ismapped(), "окно не отображено — проверка впустую"
    app._knob_mode_var.set("pair")
    app._on_knob_mode_change()               # тут падал TclError на before=
    root.update()
    assert app._pair_frame.winfo_ismapped(), "панель сторон не показалась"
    assert appconfig.config["bindings"]["knob_3"]["mode"] == "pair"
    app._knob_mode_var.set("delta")
    app._on_knob_mode_change()
    root.update()
    return "переключение туда-обратно прошло"
check("галка «влево/вправо» включается", pair_toggle_on_mapped_window)

def pair_survives_reselect():
    app.select_element("knob_3")
    app._knob_mode_var.set("pair")
    app._on_knob_mode_change()
    app.select_element("pad_1")
    app.select_element("knob_3")             # тот же путь в select_element
    root.update()
    assert app._pair_frame.winfo_ismapped(), "после возврата панель сторон пропала"
    return "панель сторон вернулась"
check("режим пары переживает переключение элемента", pair_survives_reselect)

def tk_errors_go_to_debug():
    before = len(app._debug_lines)
    app._on_tk_error(ValueError, ValueError("подстроенный сбой"), None)
    assert len(app._debug_lines) > before, "сбой Tk не попал в панель"
    return app._debug_lines[-1][:48]
check("сбой Tk уходит в панель, а не в stderr", tk_errors_go_to_debug)

print("\n── привязка задним числом ──────────────────────────────")
def bind_last_after_the_fact():
    app.select_element("btn_6")
    app._on_learn_forget()
    midi_gui.ble.msg_queue.put("cc:99:127")     # прилетело, пока ничего не ждали
    app.check_queue()
    root.update_idletasks()
    assert app._last_unbound == ("cc", 99), app._last_unbound
    app.select_element("btn_6")                  # выделяем УЖЕ после сигнала
    assert app._bind_last_btn.winfo_manager(), "кнопка «повесить сюда» не показана"
    app._on_bind_last()
    b = appconfig.config["bindings"]["btn_6"]
    assert b.get("midi_id") == 99 and b.get("midi_kind") == "cc", b
    return app._bind_last_btn.cget("text")
check("«повесить сюда» вешает последний сигнал", bind_last_after_the_fact)

def learn_follows_selection():
    app.select_element("btn_6")
    app._on_learn_toggle()
    assert app._learn_uid == "btn_6"
    app.select_element("pad_4")                  # передумали
    assert app._learn_uid is None, "ожидание осталось на прежнем элементе"
    return "ожидание снято"
check("смена выделения снимает режим привязки", learn_follows_selection)

def learn_needs_selection():
    app.current_sel = None
    app._learn_uid = None
    app._on_learn_toggle()
    assert app._learn_uid is None
    return "без выделения не вооружается"
check("«Привязать» без выделения не молчит впустую", learn_needs_selection)
app.select_element("pad_1")

print("\n── общая яркость пэдов ─────────────────────────────────")
def fader_changes_all_pads():
    app._pad_dim_var.set(40)
    app._on_pad_dim_change()
    root.update_idletasks()
    assert appconfig.config["pad_brightness"] == 40
    # pad_7 в конфиге бирюзовый #00ffff → на экране должен потемнеть
    shown = app.ui_elements["pad_7"]["frame"].cget("bg")
    assert shown.lower() != "#00ffff", f"экран не потемнел: {shown}"
    return (appconfig.config["pad_brightness"], shown, app._pad_dim_lbl.cget("text"))
check("фейдер гасит все пэды и экран", fader_changes_all_pads)

def fader_reaches_pads_without_color():
    # Пэд, которому цвет никогда не задавали, обязан слушаться фейдера.
    # Пэд без цвета готовим сами: конфиг прогона — копия живого, а владелец
    # может раскрасить все 16, и тогда проверять было бы нечего.
    bare = "pad_16"
    appconfig.config["bindings"].setdefault(bare, {}).pop("color", None)
    app.update_ui_from_config()
    app._pad_dim_var.set(100); app._on_pad_dim_change()
    full = app.ui_elements[bare]["frame"].cget("bg")
    app._pad_dim_var.set(20); app._on_pad_dim_change()
    dim = app.ui_elements[bare]["frame"].cget("bg")
    assert full.lower() == midi_gui.DEFAULT_PAD_COLOR, f"{bare} без цвета: {full}"
    assert dim != full, f"{bare} не потемнел: {full} → {dim}"
    return (bare, full, dim)
check("фейдер достаёт до пэдов без своего цвета", fader_reaches_pads_without_color)

def all_sixteen_pads_go_to_device():
    import asyncio
    sent = []
    class FakeClient:
        async def write_gatt_char(self, char, data, response=False): sent.append(char)
    state = {"vendor": True, "slot": 0, "bank": 3, "armed": set(), "notify": None}
    midi_gui.ble.VENDOR_PACE = 0        # без пауз, это прогон
    asyncio.run(midi_gui.ble._reapply_colors(FakeClient(), state))
    # на каждый пэд: разармить (Led) + RGB
    assert len(sent) == 32, f"кадров {len(sent)}, а пэдов 16"
    assert len(state["armed"]) == 16, state["armed"]
    return f"кадров: {len(sent)}"
check("на устройство уходят все 16 пэдов", all_sixteen_pads_go_to_device)

def fader_zero_turns_pads_off():
    app._pad_dim_var.set(0); app._on_pad_dim_change()
    off = app.ui_elements["pad_7"]["frame"].cget("bg")
    assert off == "#000000", f"нулевая яркость не гасит: {off}"
    app._pad_dim_var.set(100); app._on_pad_dim_change()
    return off
check("яркость 0 гасит пэды полностью", fader_zero_turns_pads_off)

def fader_sends_once_after_pause():
    drain_cmds()
    for v in (41, 42, 43, 44):
        app._pad_dim_var.set(v); app._on_pad_dim_change()
    assert not drain_cmds(), "шлём на каждое движение ползунка"
    import time as _t
    deadline = _t.time() + 1.0
    while app._pad_dim_job is not None and _t.time() < deadline:
        root.update()
        _t.sleep(0.02)
    cmds = drain_cmds()
    assert ("reapply", None) in [(c[0], c[1]) for c in cmds], cmds
    return cmds
check("отправка одна, после паузы", fader_sends_once_after_pause)

def knob_drives_fader():
    app._pad_dim_var.set(50); app._on_pad_dim_change()
    actions._pad_brightness_cb(+7)
    a = app._pad_dim_var.get()
    actions._pad_brightness_cb(-30)
    return (a, app._pad_dim_var.get())
check("крутилка двигает общую яркость", knob_drives_fader)

def fader_clamps():
    app._pad_dim_var.set(3); app._on_pad_dim_change()
    actions._pad_brightness_cb(-50)
    low = app._pad_dim_var.get()
    app._pad_dim_var.set(97); app._on_pad_dim_change()
    actions._pad_brightness_cb(+50)
    assert low == 0 and app._pad_dim_var.get() == 100, (low, app._pad_dim_var.get())
    return (low, app._pad_dim_var.get())
check("яркость не выходит за 0-100", fader_clamps)

def brightness_action_registered():
    a = actions.get("pads.brightness")
    assert a and a.kind == "delta", a
    app.select_element("knob_5")
    ids = []
    t = app._action_tree
    for cat in t.get_children(""):
        ids += list(t.get_children(cat))
    assert "pads.brightness" in ids, "действия нет в дереве крутилки"
    return a.label
check("«Яркость пэдов» доступна для крутилки", brightness_action_registered)

print("\n── громкость: прямая запись уровня ─────────────────────")
def volume_uses_scalar_not_keys():
    # Настоящую системную громкость не трогаем — подменяем конечную точку.
    class FakeVol:
        def __init__(self): self.level = 0.56
        def GetMasterVolumeLevelScalar(self): return self.level
        def SetMasterVolumeLevelScalar(self, v, _): self.level = v
    fake = FakeVol()
    real = actions._master_volume
    pressed = []
    real_press = actions.keyboard.press
    actions.keyboard.press = lambda k: pressed.append(k)
    actions._master_volume = lambda: fake
    try:
        for _ in range(20):
            actions._vol_delta(None, -1)
        low = fake.level
        for _ in range(200):
            actions._vol_delta(None, -1)
    finally:
        actions._master_volume = real
        actions.keyboard.press = real_press
    assert not pressed, "всё ещё жмёт медиа-клавиши"
    assert low < 0.56, f"уровень не падает: {low}"
    assert fake.level == 0.0, f"упёрлось не в ноль: {fake.level}"
    return (round(low, 3), fake.level)
check("громкость доезжает до нуля без медиа-клавиш", volume_uses_scalar_not_keys)

def real_pycaw_endpoint_resolves():
    # Только чтение: системную громкость прогон не двигает.
    ep = actions._master_volume()
    assert ep is not None, "конечная точка не найдена"
    level = ep.GetMasterVolumeLevelScalar()
    assert 0.0 <= level <= 1.0, level
    return f"уровень системы: {round(level * 100)}%"
check("настоящий pycaw отдаёт рабочую конечную точку", real_pycaw_endpoint_resolves)

root.destroy()

print()
if fails:
    print(f"ПРОВАЛ: {len(fails)} — " + ", ".join(fails))
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ")
