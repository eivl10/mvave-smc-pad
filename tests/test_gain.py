"""Множитель щелчка крутилки: действует на все действия вида «delta», на триггеры — нет."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mvave import actions

passed = failed = 0


def check(name, fn):
    global passed, failed
    try:
        fn()
        print(f"ok    {name}")
        passed += 1
    except Exception as e:
        print(f"FAIL  {name}: {e!r}")
        failed += 1


def _spy(kind_id):
    """Подменяет обработчик действия и возвращает список принятых delta."""
    got = []
    a = actions.get(kind_id)
    orig = a.handler
    object.__setattr__(a, "handler", lambda p, d: got.append(d))   # Action заморожен
    return got, lambda: object.__setattr__(a, "handler", orig)


def test_every_delta_action_gets_gain():
    for a in actions.ACTIONS_LIST:
        if a.kind != "delta":
            continue
        got, restore = _spy(a.id)
        try:
            actions.execute(a.id, delta=2)
        finally:
            restore()
        assert got == [2 * actions.KNOB_GAIN], (a.id, got)


def test_trigger_actions_untouched():
    got, restore = _spy("media.play_pause")
    try:
        actions.execute("media.play_pause", delta=1)
    finally:
        restore()
    assert got == [1], got


def test_pad_brightness_accumulates_fraction():
    steps = []
    actions.set_pad_brightness_provider(steps.append)
    actions._pad_acc = 0.0
    for _ in range(4):
        actions.execute("pads.brightness", delta=1)   # 4 щелчка × 1.5 = 6
    assert sum(steps) == 6, steps
    actions.set_pad_brightness_provider(None)


def test_monitor_color_step_is_int():
    sent = []
    from mvave import dimtray
    orig = dimtray.shift_kelvin
    dimtray.shift_kelvin = lambda d: sent.append(d)
    try:
        actions.execute("monitor.color", delta=1)
    finally:
        dimtray.shift_kelvin = orig
    assert sent == [90] and isinstance(sent[0], int), sent


def test_speed_overrides_gain():
    got, restore = _spy("scroll.wheel")
    try:
        actions.execute("scroll.wheel", delta=2, speed=3)
        actions.execute("scroll.wheel", delta=2)
    finally:
        restore()
    assert got == [6.0, 2 * actions.KNOB_GAIN], got


def test_clamp_speed():
    c = actions.clamp_speed
    assert c(0) == actions.SPEED_MIN and c(99) == actions.SPEED_MAX
    assert c(1.6) == 1.5 and c(2.9) == 3.0
    for junk in (None, "abc", float("nan"), [], {}):
        assert c(junk) == actions.KNOB_GAIN, junk


for name, fn in list(globals().items()):
    if name.startswith("test_"):
        check(name, fn)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
