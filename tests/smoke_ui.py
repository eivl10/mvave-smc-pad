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
root.geometry("+3000+3000")     # App ставит окно на экран — уводим обратно
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

def knob_rail_repeats_keep_moving():
    # Контроллер упирается в 0/127. Повтор крайнего значения — шаг дальше.
    for v, want in ((125, None), (126, 1), (127, 1), (127, 1), (127, 1), (0, None)):
        executed.clear()
        midi_gui.ble.msg_queue.put(f"cc:30:{v}")
        app.check_queue(); root.update_idletasks()
        if want is not None:
            assert executed and executed[-1][2] == want, (v, executed)
    app._prev_knob_cc.pop("knob_1", None)
    executed.clear()
    midi_gui.ble.msg_queue.put("cc:30:0"); app.check_queue()
    midi_gui.ble.msg_queue.put("cc:30:0"); app.check_queue()
    assert [e[2] for e in executed] == [-1, -1], executed
    return "127,127,127 → +1 +1 +1; 0,0 → −1 −1"
check("упор 0/127: повтор значения двигает дальше", knob_rail_repeats_keep_moving)

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
    # Строка во вкладке «Цвет» говорит только о проблеме и молчит, когда всё хорошо
    midi_gui.ble.msg_queue.put("color:нет")
    app.check_queue()
    root.update_idletasks()
    a = app._color_hint_lbl.cget("text")
    midi_gui.ble.msg_queue.put("color:есть")
    app.check_queue()
    root.update_idletasks()
    b = app._color_hint_lbl.cget("text")
    assert a and not b, (a, b)
    return (a, b)
check("канал цвета: сказано только о проблеме", color_status_from_device)

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

print("\n── новая панель: вкладки, заряд, режим крутилки ─────────")

def pad_has_tabs_knob_does_not():
    app.select_element("pad_2"); root.update()
    assert app._tab_seg.winfo_ismapped(), "у пэда нет вкладок"
    app._show_tab("Цвет"); root.update()
    assert app._tab_color.winfo_ismapped() and not app._tab_action.winfo_ismapped()
    # крутилка обязана вернуть «Действие», иначе список действий не виден
    app.select_element("knob_2"); root.update()
    assert not app._tab_seg.winfo_ismapped(), "у крутилки видны вкладки пэда"
    assert app._action_tree.winfo_ismapped(), "у крутилки не виден список действий"
    assert app._knob_mode_frame.winfo_ismapped()
    # блоки идут сверху вниз: режим → вкладка → кнопки
    ys = [w.winfo_y() for w in (app._knob_mode_frame, app._tab_action, app._btn_frame)]
    assert ys == sorted(ys), f"порядок блоков сломан: {ys}"
    return ys
check("вкладки у пэда, «Действие» у крутилки, порядок блоков", pad_has_tabs_knob_does_not)

def knob_seg_follows_var():
    app.select_element("knob_3")
    app._knob_mode_var.set("pair"); root.update()
    assert app._knob_mode_seg.get() == "Влево / вправо", app._knob_mode_seg.get()
    app._knob_mode_var.set("delta"); root.update()
    assert app._knob_mode_seg.get() == "Плавно"
    app._on_knob_mode_change()
    return app._knob_mode_seg.get()
check("переключатель режима следует за переменной", knob_seg_follows_var)

def battery_messages():
    for m in ("battery:37", "battery:12", "battery:-1"):
        midi_gui.ble.msg_queue.put(m)
        app.check_queue(); root.update()
    got = []
    for m in ("battery:37",):
        midi_gui.ble.msg_queue.put(m); app.check_queue(); root.update()
        got.append(app._battery_lbl.cget("text"))
    midi_gui.ble.msg_queue.put("battery:-1"); app.check_queue(); root.update()
    got.append(app._battery_lbl.cget("text"))
    assert got == ["37%", "—"], got
    return got
check("заряд: процент и прочерк при обрыве", battery_messages)

def firmware_buttons_not_bindable():
    bad = [k for k in ("btn_1", "btn_2", "btn_3", "btn_9", "btn_10") if k in app.ui_elements]
    assert not bad, f"служебные кнопки назначаются: {bad}"
    # uid рабочих кнопок не сдвинулись: на них держатся назначения в конфигах
    assert all(f"btn_{n}" in app.ui_elements for n in range(4, 9))
    assert app._firmware_btns == ["BT", "PAD BANK", "KNOB BANK", "SHIFT", "NOTE REPEAT"], \
        app._firmware_btns
    return len(app.ui_elements)
check("BT, банки, Shift, Note Repeat — серые, не назначаются", firmware_buttons_not_bindable)

print("\n── v0.3: пресеты, палитра, меню, тосты, тема ────────────")
from mvave import dialogs, presets, theme

def no_system_messagebox():
    src = open(midi_gui.__file__, encoding="utf-8").read()
    assert "messagebox" not in src and "colorchooser" not in src
    return "только свои окна"
check("в midi_gui нет системных окон сообщений", no_system_messagebox)

def preset_save_list_open():
    real_ask, real_confirm = dialogs.ask_text, dialogs.confirm
    dialogs.ask_text = lambda *a, **k: "Смоук: тест/1"
    dialogs.confirm = lambda *a, **k: True
    try:
        appconfig.config["bindings"].setdefault("pad_7", {})["action"] = "media.stop"
        name = app._on_save_preset()
        assert name == "Смоук тест 1", name
        items = presets.list_presets()
        assert any(i["name"] == name for i in items), items
        appconfig.config["bindings"]["pad_7"]["action"] = "media.next_track"
        it = next(i for i in items if i["name"] == name)
        assert app._load_config_file(it["path"], name)
        assert appconfig.config["bindings"]["pad_7"]["action"] == "media.stop"
        root.update()
        assert dialogs._toast["win"] is not None, "нет тоста после загрузки"
        return f"{len(items)} пресет(ов), папка {os.path.basename(appconfig.presets_dir())}"
    finally:
        dialogs.ask_text, dialogs.confirm = real_ask, real_confirm
check("пресет: сохранить → в списке → открыть", preset_save_list_open)

def import_keeps_theme_and_palette():
    appconfig.config["theme"] = "light"
    appconfig.config["custom_colors"] = ["#123456"]
    appconfig.save_config()
    it = presets.list_presets()[0]
    real_confirm = dialogs.confirm
    dialogs.confirm = lambda *a, **k: True
    try:
        assert app._load_config_file(it["path"], it["name"])
    finally:
        dialogs.confirm = real_confirm
    assert appconfig.config.get("theme") == "light", appconfig.config.get("theme")
    assert "#123456" in appconfig.config.get("custom_colors", [])
    appconfig.config["theme"] = "dark"
    return "тема и свои цвета на месте"
check("открытие пресета не сбрасывает тему и палитру", import_keeps_theme_and_palette)

def palette_add_remove():
    app.select_element("pad_5")
    n0 = len(app._color_preset_btns)
    app._add_custom_color("#abcdef")
    app._add_custom_color("#ABCDEF")          # повтор не добавляется
    n1 = len(app._color_preset_btns)
    assert n1 == n0 + 1 or "#abcdef" in [c for c, _ in app._color_preset_btns][:n0], (n0, n1)
    assert "#abcdef" in appconfig.config["custom_colors"]
    app._remove_custom_color("#abcdef")
    assert "#abcdef" not in appconfig.config["custom_colors"]
    return f"кружков: {n0} → {n1} → {len(app._color_preset_btns)}"
check("свой цвет остаётся в палитре и убирается", palette_add_remove)

def menu_opens_and_closes():
    app._open_settings_menu(); root.update()
    m = dialogs.PopupMenu._current
    assert m is not None and m.win.winfo_ismapped(), "меню не открылось"
    m._born = False
    dialogs.PopupMenu.close_current(); root.update()
    assert dialogs.PopupMenu._current is None
    return "открылось и закрылось"
check("меню настроек открывается и закрывается", menu_opens_and_closes)

def toast_replaces_previous():
    dialogs.toast(root, "первый"); root.update()
    first = dialogs._toast["win"]
    dialogs.toast(root, "второй"); root.update()
    assert dialogs._toast["win"] is not first and not first.winfo_exists()
    dialogs._toast_close()
    return "один тост за раз"
check("новый тост заменяет старый", toast_replaces_previous)

def theme_switch_roundtrip():
    app.select_element("pad_3"); app._show_tab("Цвет"); root.update()
    n = len(app.ui_elements)
    label_before = app.ui_elements["pad_7"]["lbl"].cget("text")
    app.set_theme("light"); root.update()
    assert theme.applied == "light" and str(app.root.cget("bg")) == theme.BG
    assert len(app.ui_elements) == n, "после пересборки другое число элементов"
    assert app.current_sel == "pad_3" and app._tab_seg.get() == "Цвет"
    assert app.ui_elements["pad_7"]["lbl"].cget("text") == label_before
    assert appconfig.config["theme"] == "light"
    app.set_theme("dark"); root.update()
    assert theme.applied == "dark" and app.current_sel == "pad_3"
    # после пересборки обработка MIDI жива
    executed.clear()
    midi_gui.ble.msg_queue.put("note:42:100"); app.check_queue()
    return f"{n} элементов, выделение и вкладка сохранены"
check("тема: тёмная → светлая → тёмная на ходу", theme_switch_roundtrip)

print("\n── 1.0: углы и крутилки ────────────────────────────────")
def ctk_corners_are_images():
    import customtkinter as ctk
    from mvave import aa_shapes
    f = ctk.CTkFrame(root, width=60, height=40, corner_radius=12, fg_color="#7c5cff")
    f.place(x=0, y=0); root.update()
    cv = f._canvas
    ids = [i for i in cv.find_withtag("ctk_aa_circle_font_element")]
    kinds = {cv.type(i) for i in ids}
    assert ids and kinds == {"image"}, f"углы рисуются не картинкой: {kinds}"
    assert any(cv.itemcget(i, "image") for i in ids), "у кругов пустые картинки"
    f.configure(fg_color="#ff0000"); root.update()   # смена цвета перерисовывает
    n = len(aa_shapes._cache)
    f.destroy()
    return f"{len(ids)} кругов-картинок, кэш {n}"
check("углы CTk — сглаженные картинки", ctk_corners_are_images)

def knob_frames_cached_and_endless():
    k = app.ui_elements["knob_1"]["canvas"]
    a0 = k.angle
    k.rotate(-1000)                    # далеко за оборот: без упоров
    assert k.angle == a0 + 5000
    img = k.itemcget(k._img_id, "image")
    k.rotate(72)                       # полный оборот → тот же кадр
    assert k.itemcget(k._img_id, "image") == img, "кадр не из кэша по angle % 360"
    k.rotate(1000 - 72)
    return f"угол {k.angle:.0f}°, кадр {img}"
check("крутилка: гладкий кадр, вращение без упоров", knob_frames_cached_and_endless)

print("\n── 1.0: высота окна ────────────────────────────────────")
def window_min_from_content():
    root.update()
    mw, mh = root.minsize()
    assert mh >= root.winfo_reqheight(), (mh, root.winfo_reqheight())
    # режим пары — самый высокий инспектор: низ кнопок не срезан
    app.select_element("knob_7")
    app._knob_mode_var.set("pair"); app._on_knob_mode_change(); root.update()
    bf = app._btn_frame
    assert bf.winfo_height() >= bf.winfo_reqheight(), "кнопки инспектора срезаны"
    app._knob_mode_var.set("delta"); app._on_knob_mode_change(); root.update()
    return f"минимум {mw}x{mh}"
check("минимум окна считается от содержимого", window_min_from_content)

def geometry_saved_only_when_normal():
    appconfig.config.pop("window", None)
    root.iconify(); root.update()
    app._remember_geometry()
    assert "window" not in appconfig.config, "запомнено свёрнутое окно"
    root.deiconify(); root.geometry("+3000+3000"); root.update()
    app._remember_geometry()
    g = appconfig.config.get("window")
    assert g and midi_gui.winplace.parse(g), g
    assert "window" in appconfig._APP_KEYS, "пресет перенёс бы место окна"
    return g
check("место окна запоминается только в обычном состоянии", geometry_saved_only_when_normal)

def offscreen_geometry_rejected():
    assert not midi_gui.winplace.on_screen(-20000, -20000, 1320, 780)
    assert midi_gui.winplace.parse("1320x780+-8+0") == (1320, 780, -8, 0)
    return "окно за пределами мониторов не восстанавливается"
check("сохранённое место вне экрана отбрасывается", offscreen_geometry_rejected)

print("\n── 1.0: замок «влево / вправо» ─────────────────────────")
def _pair_knob(uid, binding=None):
    appconfig.config["bindings"][uid] = binding or {"mode": "pair"}
    app.select_element(uid)
    app._knob_mode_var.set("pair"); app._on_knob_mode_change(); root.update()

def _pick(slot, action_id):
    # Смена стороны выделяет её действие в дереве; <<TreeviewSelect>> нужно
    # отработать ДО назначения, как при настоящем клике.
    app._pair_slot.set(slot); app._on_pair_slot_change(); root.update()
    app._assign_action(actions.get(action_id)); root.update()

def lock_mirrors_opposite():
    _pair_knob("knob_8")
    assert midi_gui._pair_locked(appconfig.config["bindings"]["knob_8"]), "новый замок открыт"
    _pick("cw", "media.next_track")
    b = appconfig.config["bindings"]["knob_8"]
    assert midi_gui._side(b, "ccw")["action"] == "media.prev_track", b
    _pick("ccw", "browser.zoom_in")          # слева «больше» — справа «меньше»
    assert midi_gui._side(b, "cw")["action"] == "browser.zoom_out", b
    assert app._lock_btn.cget("text") == "\ue72e"
    return f"{b['ccw']['action']} ↔ {b['cw']['action']}"
check("замок: выбор стороны ставит противоположное", lock_mirrors_opposite)

def lock_opens_without_pair():
    _pair_knob("knob_8")
    _pick("cw", "media.play_pause")
    b = appconfig.config["bindings"]["knob_8"]
    assert b["pair_lock"] is False and not midi_gui._pair_locked(b)
    assert "ccw" not in b or not midi_gui._side(b, "ccw").get("action"), b
    assert app._pair_hint.winfo_ismapped(), "нет подсказки про пару"
    app._on_lock_toggle()                     # закрыть нельзя — пары нет
    assert not midi_gui._pair_locked(b)
    return "замок открылся, подсказка видна"
check("действие без пары открывает замок", lock_opens_without_pair)

def old_config_not_overwritten():
    old = {"mode": "pair", "ccw": {"action": "edit.undo", "param": None},
           "cw": {"action": "media.play_pause", "param": None}}
    _pair_knob("knob_8", old)
    assert not midi_gui._pair_locked(old), "замок закрылся на несвязанных сторонах"
    _pick("ccw", "edit.redo")
    b = appconfig.config["bindings"]["knob_8"]
    assert midi_gui._side(b, "cw")["action"] == "media.play_pause", "чужая сторона переписана"
    paired = {"mode": "pair", "ccw": "window.snap_left", "cw": "window.snap_right"}
    assert midi_gui._pair_locked(paired), "противоположные стороны без ключа — замок закрыт"
    return "несвязанные стороны целы, связанные — под замком"
check("старый конфиг: замок не переписывает стороны", old_config_not_overwritten)

def lock_toggle_closes_and_mirrors():
    _pair_knob("knob_8", {"mode": "pair", "pair_lock": False,
                          "cw": {"action": "desktop.right", "param": None}})
    app._pair_slot.set("cw")
    app._on_lock_toggle()
    b = appconfig.config["bindings"]["knob_8"]
    assert b["pair_lock"] is True and midi_gui._side(b, "ccw")["action"] == "desktop.left", b
    app._on_lock_toggle()
    assert b["pair_lock"] is False
    return "закрытие замка дописало вторую сторону"
check("закрыть замок вручную", lock_toggle_closes_and_mirrors)

def opposite_table_symmetric():
    for a, b in actions.OPPOSITE.items():
        assert actions.OPPOSITE[b] == a, (a, b)
        assert actions.get(a) and actions.get(a).kind == "trigger" and not actions.get(a).param_kind, a
    return f"{len(actions.OPPOSITE) // 2} пар"
check("таблица пар симметрична и без параметров", opposite_table_symmetric)

print("\n── 1.0: автозапуск (тестовый ключ реестра) ─────────────")
def autostart_toggle_writes_registry():
    import winreg
    from mvave import autostart
    real_key = autostart.RUN_KEY
    autostart.RUN_KEY = r"Software\mvave-smc-pad-smoke\Run"
    try:
        app._open_settings_menu(); root.update()
        assert not app._autostart_var.get(), "на пустом ключе галка включена"
        app._autostart_var.set(True); app._on_autostart_toggle()
        assert autostart.state() == "on" and autostart.recorded() == autostart.command()
        assert autostart.command().endswith("--tray")
        # exe переехал: в реестре другой путь → галка выключена
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, autostart.RUN_KEY) as k:
            winreg.SetValueEx(k, autostart.APP_NAME, 0, winreg.REG_SZ, '"C:\\old\\SMC-PAD.exe" --tray')
        assert autostart.state() == "other"
        dialogs.PopupMenu._current._born = False
        dialogs.PopupMenu.close_current(); root.update()
        app._open_settings_menu(); root.update()
        assert not app._autostart_var.get(), "другой путь показан включённым"
        app._autostart_var.set(True); app._on_autostart_toggle()
        assert autostart.state() == "on", "путь не перезаписан"
        app._autostart_var.set(False); app._on_autostart_toggle()
        assert autostart.state() == "off" and autostart.recorded() == ""
        dialogs.PopupMenu._current._born = False
        dialogs.PopupMenu.close_current(); dialogs._toast_close(); root.update()
        return autostart.command()[-40:]
    finally:
        autostart.RUN_KEY = real_key
        for sub in (r"Software\mvave-smc-pad-smoke\Run", r"Software\mvave-smc-pad-smoke"):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
            except OSError:
                pass
check("галка автозапуска пишет и стирает значение", autostart_toggle_writes_registry)

def tray_start_falls_back_to_window():
    class NoIcon:
        visible = False
        def stop(self): pass
    real = app._tray._icon
    app._tray._icon = NoIcon()
    try:
        app.start_in_tray(); root.update()
        assert root.state() == "withdrawn", "--tray не спрятал окно"
        import time
        t0 = time.time()
        while root.state() == "withdrawn" and time.time() - t0 < 3:
            root.update(); time.sleep(0.05)
        assert root.state() == "normal", "значка нет, а окно осталось спрятанным"
    finally:
        app._tray._icon = real
        root.geometry("+3000+3000"); root.update()
    return "значок не появился → окно показано"
check("--tray без значка в трее показывает окно", tray_start_falls_back_to_window)

print("\n── 1.0: имена, банки, справка ──────────────────────────")
def latin_names_everywhere():
    assert app._name("pad_6") == "PAD 6" and app._name("knob_6") == "KNOB 6"
    assert app._name("btn_6") == "BUTTON PLAY"
    app.select_element("knob_2"); root.update()
    assert app._insp_header.cget("text") == "KNOB 2"
    app.select_element("btn_4"); root.update()
    assert app._insp_header.cget("text") == "BUTTON"
    assert app._insp_header_icon.cget("text") == "\ue892"
    assert "knob_2" in app.ui_elements, "uid поменялся"
    for v in (40, 41):
        midi_gui.ble.msg_queue.put(f"cc:31:{v}")
    app.check_queue(); root.update_idletasks()
    txt = app.last_input_var.get()
    assert txt == "KNOB 2 · CC 31 · 41", txt
    return txt
check("имена латиницей, uid прежние", latin_names_everywhere)

def transport_glyphs_distinct():
    g = [app.ui_elements[f"btn_{n}"]["icon"] for n in range(4, 9)]
    assert len(set(g)) == 5, f"значки повторяются: {g}"
    assert all(0xE000 <= ord(c) <= 0xF8FF for c in g), "не MDL2"
    assert app.ui_elements["btn_4"]["icon_lbl"].cget("font").startswith("{Segoe MDL2")
    return " ".join(f"U+{ord(c):04X}" for c in g)
check("назад / вперёд не путаются с play", transport_glyphs_distinct)

def bank_label_neutral():
    app._set_bank(5); root.update()
    assert app._bank_var.get().startswith("Pad bank 5")
    midi_gui.ble.msg_queue.put("cc:40:10"); app.check_queue()
    assert app._bank_var.get() == "Pad bank 5 · Knob bank 2", app._bank_var.get()
    midi_gui.ble.msg_queue.put("cc:32:10"); app.check_queue()
    assert app._bank_var.get().endswith("Knob bank 1")
    app._set_bank(3)
    return app._bank_var.get()
check("банки: нейтральная подпись Pad / Knob bank", bank_label_neutral)

def help_opens_all_sections():
    from mvave import help_text
    seen = {}
    def grab():
        m = app._help_modal
        texts = []
        def walk(w):
            for c in w.winfo_children():
                if isinstance(c, tk.Label):
                    texts.append(c.cget("text"))
                walk(c)
        walk(m.win)
        seen["n"] = sum(1 for t in texts if any(t.endswith(s) for s, _ in help_text.SECTIONS))
        m.close(None)
    root.after(300, grab)
    app._open_help()
    assert seen.get("n") == len(help_text.SECTIONS) == 12, seen
    return f"{seen['n']} разделов"
check("справка открывается, 12 разделов", help_opens_all_sections)

def identify_renamed():
    texts = []
    def walk(w):
        for c in w.winfo_children():
            try:
                texts.append(c.cget("text"))
            except Exception:
                pass
            walk(c)
    walk(root)
    assert "Выбор нажатием" in texts and "Определить" not in texts
    return "«Выбор нажатием»"
check("переключатель переименован", identify_renamed)

def no_raw_private_use_glyphs():
    # Значки MDL2 в исходниках — только \uXXXX: сами символы частной области
    # в редакторе невидимы, а Edit-инструмент пишет их как есть.
    import glob, re
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = [os.path.join(base, "midi_gui.py")] + [
        f for d in ("mvave", "tests", "tools") for f in glob.glob(os.path.join(base, d, "*.py"))]
    bad = [os.path.basename(f) for f in files
           if re.search("[\ue000-\uf8ff]", open(f, encoding="utf-8").read())]
    assert not bad, f"сырые символы U+E000–U+F8FF: {bad}"
    return f"{len(files)} файлов чистые"
check("в .py нет сырых значков частной области", no_raw_private_use_glyphs)

root.destroy()

print()
if fails:
    print(f"ПРОВАЛ: {len(fails)} — " + ", ".join(fails))
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ")
