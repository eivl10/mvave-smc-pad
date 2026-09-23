import time
import os
import subprocess
import ctypes
from dataclasses import dataclass
from typing import Any, Callable, Optional

from pynput.keyboard import Controller as KeyController, Key, KeyCode
from pynput.mouse import Controller as MouseController

try:
    from ctypes import POINTER, cast as _cast
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
except ImportError:
    AudioUtilities = None
    IAudioEndpointVolume = None

VOL_SENSITIVITY = 0.4
keyboard = KeyController()
mouse = MouseController()

_clipboard = None
vol_acc = 0.0
scroll_acc = 0.0

def set_clipboard_provider(provider):
    global _clipboard
    _clipboard = provider


_pad_brightness_cb = None


def set_pad_brightness_provider(fn):
    """fn(delta) → None: сдвинуть общую яркость подсветки пэдов.

    Живёт в App, потому что яркость — состояние интерфейса и конфига,
    а реестр действий про Tk ничего не знает.
    """
    global _pad_brightness_cb
    _pad_brightness_cb = fn


def _pad_brightness(param, delta):
    if _pad_brightness_cb is None:
        return "Яркость пэдов недоступна"
    _pad_brightness_cb(delta)


_mon_acc = 0.0


def _monitor_brightness(param, delta):
    """Яркость мониторов через DimTray.

    Шаг DimTray — целые проценты, а крутилка шлёт мелкие дельты. Копим
    дробную часть, иначе быстрое вращение давало бы рывки, а медленное —
    ничего.
    """
    global _mon_acc
    from mvave import dimtray
    _mon_acc += delta * 0.5
    step = int(_mon_acc)
    if step == 0:
        return None
    _mon_acc -= step
    return dimtray.adjust_all(step)


def _monitor_step(step):
    def handler(param, delta):
        from mvave import dimtray
        return dimtray.adjust_all(step)
    return handler


def _monitor_reset(param, delta):
    from mvave import dimtray
    return dimtray.reset_all()

def map_tk_key_to_pynput(k):
    mapping = {
        "return": Key.enter, "space": Key.space, "escape": Key.esc,
        "backspace": Key.backspace, "tab": Key.tab, "up": Key.up,
        "down": Key.down, "left": Key.left, "right": Key.right,
        "delete": Key.delete, "home": Key.home, "end": Key.end,
        "page_up": Key.page_up, "page_down": Key.page_down,
        "f1": Key.f1, "f2": Key.f2, "f3": Key.f3, "f4": Key.f4,
        "f5": Key.f5, "f6": Key.f6, "f7": Key.f7, "f8": Key.f8,
        "f9": Key.f9, "f10": Key.f10, "f11": Key.f11, "f12": Key.f12,
        "minus": "-", "equal": "=", "bracketleft": "[", "bracketright": "]",
        "backslash": "\\", "semicolon": ";", "apostrophe": "'",
        "comma": ",", "period": ".", "slash": "/", "grave": "`"
    }
    return mapping.get(k, k)

@dataclass(frozen=True)
class Action:
    id: str
    label: str
    category: str
    kind: str
    short: str
    hint: str
    handler: Callable
    param_kind: Optional[str] = None
    default_param: Any = None

def _hotkey(param, delta):
    if isinstance(param, Key):
        keyboard.press(param)
        keyboard.release(param)
    elif isinstance(param, str):
        keys = param.split("+")
        to_release = []
        for k in keys:
            if k == "ctrl": keyboard.press(Key.ctrl); to_release.append(Key.ctrl)
            elif k == "shift": keyboard.press(Key.shift); to_release.append(Key.shift)
            elif k == "alt": keyboard.press(Key.alt); to_release.append(Key.alt)
            elif k == "win": keyboard.press(Key.cmd); to_release.append(Key.cmd)
            else:
                mapped = map_tk_key_to_pynput(k)
                if isinstance(mapped, str) and len(mapped) == 1 and mapped.isascii() and mapped.isalnum():
                    # Буква/цифра — по виртуальному коду, а не символом: в русской
                    # раскладке 'c' в ней нет, pynput шлёт юникод-пакет, и Ctrl+C
                    # до программы не доходит как сочетание.
                    key_obj = KeyCode.from_vk(ord(mapped.upper()))
                    keyboard.press(key_obj)
                    to_release.append(key_obj)
                elif hasattr(Key, mapped) if isinstance(mapped, str) else False:
                    key_obj = getattr(Key, mapped)
                    keyboard.press(key_obj)
                    to_release.append(key_obj)
                else:
                    keyboard.press(mapped)
                    to_release.append(mapped)
        for k in reversed(to_release):
            keyboard.release(k)

_endpoint_volume = None


def _master_volume():
    """IAudioEndpointVolume системного вывода, один раз на процесс.

    У pycaw две несовместимые формы, причём в ОДНОЙ версии сразу:
    `GetSpeakers()` отдаёт обёртку `AudioDevice` с готовым свойством
    `EndpointVolume`, а `GetMicrophone()` — сырой `POINTER(IMMDevice)`,
    который надо активировать вручную. Проверено на pycaw 20251023.
    Поэтому пробуем оба пути, а не угадываем версию.
    """
    global _endpoint_volume
    if _endpoint_volume is not None:
        return _endpoint_volume
    if AudioUtilities is None or IAudioEndpointVolume is None:
        return None
    dev = AudioUtilities.GetSpeakers()
    if not dev:
        return None

    ev = getattr(dev, "EndpointVolume", None)
    if ev is not None:
        _endpoint_volume = ev
        return _endpoint_volume

    iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    _endpoint_volume = _cast(iface, POINTER(IAudioEndpointVolume))
    return _endpoint_volume


def volume_state():
    """(уровень 0..100, как получен интерфейс) — для отладочного лога.

    Своя функция, а не чтение из GUI: путь получения интерфейса должен быть
    ровно тот же, что у самой регулировки, иначе диагностика мерит не то.
    """
    try:
        vol = _master_volume()
    except Exception as e:
        return None, f"исключение: {e}"
    if vol is None:
        return None, "интерфейс не получен (pycaw недоступен)"
    try:
        return vol.GetMasterVolumeLevelScalar() * 100.0, type(vol).__name__
    except Exception as e:
        return None, f"чтение упало: {e}"


def _vol_delta(param, delta):
    """Громкость — прямой записью уровня, а не медиа-клавишами.

    Медиа-клавиши слались пачкой в цикле без пауз, и Windows часть событий
    просто теряла: чем шире мах крутилкой, тем больше нажатий пропадало.
    Отсюда была «ступенька» — громкость упиралась то в 56, то в 22, то в 10
    и дальше не шла. Прямая запись уровня не теряет ничего и попадает точно.
    """
    global vol_acc
    vol = None
    try:
        vol = _master_volume()
    except Exception as e:
        return f"Ошибка pycaw: {e}"

    if vol is None:
        # Запасной путь: медиа-клавиши по одной за событие, без цикла.
        vol_acc += delta * VOL_SENSITIVITY
        steps = int(vol_acc)
        if steps:
            key = Key.media_volume_up if steps > 0 else Key.media_volume_down
            keyboard.press(key)
            keyboard.release(key)
            vol_acc -= 1 if steps > 0 else -1
        return

    try:
        cur = vol.GetMasterVolumeLevelScalar()
        new = min(1.0, max(0.0, cur + delta * VOL_SENSITIVITY / 100.0 * 2.5))
        vol.SetMasterVolumeLevelScalar(new, None)
    except Exception as e:
        return f"Ошибка pycaw: {e}"

def _mic_mute(param, delta):
    # GetMicrophone() отдаёт IMMDevice — у него НЕТ SetMute/GetMute.
    # Мьютом заведует IAudioEndpointVolume, его надо отдельно активировать
    # на устройстве. Проверено на этой машине: без Activate обработчик
    # всегда падал в AttributeError и действие молча не работало.
    if AudioUtilities is None or IAudioEndpointVolume is None:
        return "pycaw не установлен"
    try:
        dev = AudioUtilities.GetMicrophone()
        if not dev:
            return "Микрофон не найден"
        iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        vol = _cast(iface, POINTER(IAudioEndpointVolume))
        vol.SetMute(not vol.GetMute(), None)
    except Exception as e:
        return f"Ошибка pycaw: {e}"

def _system_lock(param, delta):
    ctypes.windll.user32.LockWorkStation()

def _monitor_off(param, delta):
    # WM_SYSCOMMAND / SC_MONITORPOWER / 2 = выключить. Post, а не Send:
    # SendMessage на HWND_BROADCAST ждёт каждое окно и может повиснуть.
    ctypes.windll.user32.PostMessageW(0xFFFF, 0x0112, 0xF170, 2)

def _scroll_wheel(param, delta):
    global scroll_acc
    scroll_acc += delta * 1.5
    steps = int(scroll_acc)
    if steps != 0:
        mouse.scroll(0, steps)
        scroll_acc -= steps

def _scroll_zoom(param, delta):
    global scroll_acc
    scroll_acc += delta * 1.5
    steps = int(scroll_acc)
    if steps != 0:
        keyboard.press(Key.ctrl)
        mouse.scroll(0, steps)
        keyboard.release(Key.ctrl)
        scroll_acc -= steps

def _scroll_arrows_v(param, delta):
    global scroll_acc
    scroll_acc += delta * 1.5
    steps = int(scroll_acc)
    if steps > 0:
        for _ in range(steps):
            keyboard.press(Key.up)
            keyboard.release(Key.up)
        scroll_acc -= steps
    elif steps < 0:
        for _ in range(-steps):
            keyboard.press(Key.down)
            keyboard.release(Key.down)
        scroll_acc -= steps

def _scroll_arrows_h(param, delta):
    global scroll_acc
    scroll_acc += delta * 1.5
    steps = int(scroll_acc)
    if steps > 0:
        for _ in range(steps):
            keyboard.press(Key.right)
            keyboard.release(Key.right)
        scroll_acc -= steps
    elif steps < 0:
        for _ in range(-steps):
            keyboard.press(Key.left)
            keyboard.release(Key.left)
        scroll_acc -= steps

def _text_type(param, delta):
    # Через буфер обмена, а не pynput.type(): тот врёт на кириллице в Windows.
    if _clipboard is None:
        return "Провайдер буфера не задан"
    if not param:
        return "Текст не задан"
    old = _clipboard.get()
    try:
        _clipboard.set(param)
        v = KeyCode.from_vk(0x56)  # VK_V: символ 'v' в русской раскладке не нажимается
        keyboard.press(Key.ctrl)
        keyboard.press(v)
        keyboard.release(v)
        keyboard.release(Key.ctrl)
        # Вставка асинхронна: приложение-получатель читает буфер уже после
        # того, как мы отпустили клавиши. Без паузы буфер успевает вернуться
        # к старому значению, и вставляется не то.
        time.sleep(0.15)
    finally:
        _clipboard.set(old)

def _startfile(param, delta):
    if not param:
        return "Путь не задан"
    if not os.path.exists(param) and not param.startswith("http"):
        return f"Не найдено: {param}"
    try:
        os.startfile(param)
    except Exception as e:
        return f"Ошибка запуска: {e}"

def _none(param, delta):
    pass

ACTIONS_LIST = [
    Action("none", "— не назначено —", "—", "trigger", "", "Действие не назначено", _none),
    Action("media.play_pause", "Play / Пауза", "Медиа", "trigger", "Play", "Приостановить или продолжить воспроизведение", _hotkey, default_param=Key.media_play_pause),
    Action("media.next_track", "Следующий трек", "Медиа", "trigger", "Трек ▶", "Перейти к следующему треку", _hotkey, default_param=Key.media_next),
    Action("media.prev_track", "Предыдущий трек", "Медиа", "trigger", "◀ Трек", "Перейти к предыдущему треку", _hotkey, default_param=Key.media_previous),
    Action("media.stop", "Стоп", "Медиа", "trigger", "Стоп", "Остановить воспроизведение", _hotkey, default_param=Key.media_stop),
    Action("media.volume_up", "Громкость +", "Медиа", "trigger", "Громк +", "Увеличить громкость", _hotkey, default_param=Key.media_volume_up),
    Action("media.volume_down", "Громкость −", "Медиа", "trigger", "Громк −", "Уменьшить громкость", _hotkey, default_param=Key.media_volume_down),
    Action("media.mute", "Без звука", "Медиа", "trigger", "Тихо", "Выключить звук", _hotkey, default_param=Key.media_volume_mute),
    Action("audio.volume", "Громкость", "Звук", "delta", "Громк", "Плавная регулировка громкости", _vol_delta),
    Action("audio.mic_mute", "Микрофон вкл/выкл", "Звук", "trigger", "Микроф", "Аппаратное отключение микрофона", _mic_mute),
    Action("window.maximize", "Развернуть окно", "Окна", "trigger", "Развер", "Развернуть на весь экран", _hotkey, default_param="win+up"),
    Action("window.minimize", "Свернуть окно", "Окна", "trigger", "Сверн", "Свернуть текущее окно", _hotkey, default_param="win+down"),
    Action("window.snap_left", "Прижать влево", "Окна", "trigger", "◀ Край", "Прижать окно к левому краю", _hotkey, default_param="win+left"),
    Action("window.snap_right", "Прижать вправо", "Окна", "trigger", "Край ▶", "Прижать окно к правому краю", _hotkey, default_param="win+right"),
    Action("window.close", "Закрыть окно", "Окна", "trigger", "Закрыть", "Закрыть активное окно", _hotkey, default_param="alt+f4"),
    Action("window.show_desktop", "Показать рабочий стол", "Окна", "trigger", "Стол", "Свернуть все окна", _hotkey, default_param="win+d"),
    Action("window.task_view", "Task View", "Окна", "trigger", "Все окна", "Обзор открытых окон", _hotkey, default_param="win+tab"),
    Action("window.alt_tab", "Предыдущее окно", "Окна", "trigger", "Пред окно", "Переключиться на предыдущее окно", _hotkey, default_param="alt+tab"),
    Action("window.monitor_left", "Окно на левый монитор", "Окна", "trigger", "◀ Монитор", "Перенести окно на соседний монитор слева (Win+Shift+←)", _hotkey, default_param="win+shift+left"),
    Action("window.monitor_right", "Окно на правый монитор", "Окна", "trigger", "Монитор ▶", "Перенести окно на соседний монитор справа (Win+Shift+→)", _hotkey, default_param="win+shift+right"),
    Action("window.minimize_others", "Свернуть остальные", "Окна", "trigger", "Сверн др", "Свернуть все окна, кроме активного (Win+Home)", _hotkey, default_param="win+home"),
    Action("window.cycle", "Следующее окно по кругу", "Окна", "trigger", "Окна по кр", "Перебрать окна по порядку открытия (Alt+Esc)", _hotkey, default_param="alt+escape"),
    Action("desktop.new","Новый рабочий стол", "Рабочие столы", "trigger", "Стол +", "Создать виртуальный рабочий стол", _hotkey, default_param="ctrl+win+d"),
    Action("desktop.close", "Закрыть рабочий стол", "Рабочие столы", "trigger", "Стол −", "Закрыть текущий рабочий стол", _hotkey, default_param="ctrl+win+f4"),
    Action("desktop.left", "Рабочий стол влево", "Рабочие столы", "trigger", "◀ Стол", "Переключиться влево", _hotkey, default_param="ctrl+win+left"),
    Action("desktop.right", "Рабочий стол вправо", "Рабочие столы", "trigger", "Стол ▶", "Переключиться вправо", _hotkey, default_param="ctrl+win+right"),
    Action("system.lock", "Заблокировать экран", "Система", "trigger", "Замок", "Заблокировать компьютер", _system_lock),
    Action("system.screenshot_area", "Скриншот области", "Система", "trigger", "Снимок обл", "Скриншот выделенной области", _hotkey, default_param="win+shift+s"),
    Action("system.screenshot", "Скриншот экрана", "Система", "trigger", "Снимок", "Скриншот всего экрана", _hotkey, default_param=Key.print_screen),
    Action("system.emoji", "Панель эмодзи", "Система", "trigger", "Эмодзи", "Открыть панель эмодзи", _hotkey, default_param="win+."),
    Action("system.explorer", "Проводник", "Система", "trigger", "Провод", "Открыть Проводник", _hotkey, default_param="win+e"),
    Action("system.task_manager", "Диспетчер задач", "Система", "trigger", "Диспетч", "Открыть Диспетчер задач", _hotkey, default_param="ctrl+shift+esc"),
    Action("system.start", "Меню Пуск", "Система", "trigger", "Пуск", "Открыть меню Пуск (Win)", _hotkey, default_param="win"),
    Action("system.search", "Поиск Windows", "Система", "trigger", "Поиск", "Открыть поиск Windows (Win+S)", _hotkey, default_param="win+s"),
    Action("system.run", "Выполнить…", "Система", "trigger", "Выполн", "Окно «Выполнить» (Win+R)", _hotkey, default_param="win+r"),
    Action("system.settings", "Параметры Windows", "Система", "trigger", "Парам", "Открыть Параметры (Win+I)", _hotkey, default_param="win+i"),
    Action("system.quick_settings", "Быстрые настройки", "Система", "trigger", "Быстр наст", "Панель Wi-Fi, звука, Bluetooth (Win+A)", _hotkey, default_param="win+a"),
    Action("system.notifications", "Уведомления", "Система", "trigger", "Уведомл", "Центр уведомлений и календарь (Win+N)", _hotkey, default_param="win+n"),
    Action("system.project", "Режим экранов", "Система", "trigger", "Экраны", "Дублировать / расширить экраны (Win+P)", _hotkey, default_param="win+p"),
    Action("system.game_bar", "Игровая панель", "Система", "trigger", "Game Bar", "Открыть Xbox Game Bar (Win+G)", _hotkey, default_param="win+g"),
    Action("system.record", "Запись экрана вкл/выкл", "Система", "trigger", "Запись", "Начать или остановить запись Game Bar (Win+Alt+R)", _hotkey, default_param="win+alt+r"),
    Action("system.monitor_off", "Погасить мониторы", "Система", "trigger", "Экран выкл", "Выключить мониторы, пока не шевельнёшь мышью", _monitor_off),
    Action("browser.new_tab", "Новая вкладка", "Браузер", "trigger", "Вкладка +", "Открыть новую вкладку (Ctrl+T)", _hotkey, default_param="ctrl+t"),
    Action("browser.close_tab", "Закрыть вкладку", "Браузер", "trigger", "Вкладка −", "Закрыть текущую вкладку (Ctrl+W)", _hotkey, default_param="ctrl+w"),
    Action("browser.reopen_tab", "Вернуть закрытую вкладку", "Браузер", "trigger", "Вернуть вкл", "Открыть последнюю закрытую вкладку (Ctrl+Shift+T)", _hotkey, default_param="ctrl+shift+t"),
    Action("browser.next_tab", "Следующая вкладка", "Браузер", "trigger", "Вкладка ▶", "Перейти на вкладку правее (Ctrl+Tab)", _hotkey, default_param="ctrl+tab"),
    Action("browser.prev_tab", "Предыдущая вкладка", "Браузер", "trigger", "◀ Вкладка", "Перейти на вкладку левее (Ctrl+Shift+Tab)", _hotkey, default_param="ctrl+shift+tab"),
    Action("browser.back", "Назад", "Браузер", "trigger", "◀ Назад", "Назад по истории (Alt+←)", _hotkey, default_param="alt+left"),
    Action("browser.forward", "Вперёд", "Браузер", "trigger", "Вперёд ▶", "Вперёд по истории (Alt+→)", _hotkey, default_param="alt+right"),
    Action("browser.refresh", "Обновить страницу", "Браузер", "trigger", "Обновить", "Перезагрузить страницу (F5)", _hotkey, default_param="f5"),
    Action("browser.hard_refresh", "Обновить без кэша", "Браузер", "trigger", "Обн без кэш", "Перезагрузить страницу мимо кэша (Ctrl+F5)", _hotkey, default_param="ctrl+f5"),
    Action("browser.address", "Адресная строка", "Браузер", "trigger", "Адрес", "Перейти в адресную строку (Ctrl+L)", _hotkey, default_param="ctrl+l"),
    Action("browser.fullscreen", "Полный экран", "Браузер", "trigger", "Полн экр", "Полноэкранный режим (F11)", _hotkey, default_param="f11"),
    Action("browser.zoom_in", "Крупнее", "Браузер", "trigger", "Зум +", "Увеличить масштаб страницы (Ctrl+=)", _hotkey, default_param="ctrl+equal"),
    Action("browser.zoom_out", "Мельче", "Браузер", "trigger", "Зум −", "Уменьшить масштаб страницы (Ctrl+−)", _hotkey, default_param="ctrl+minus"),
    Action("browser.zoom_reset", "Масштаб 100%", "Браузер", "trigger", "Зум 100", "Сбросить масштаб страницы (Ctrl+0)", _hotkey, default_param="ctrl+0"),
    Action("scroll.wheel", "Прокрутка", "Прокрутка", "delta", "Скролл", "Вертикальная прокрутка мышью", _scroll_wheel),
    Action("scroll.zoom", "Масштаб", "Прокрутка", "delta", "Масштаб", "Масштабирование (Ctrl+Скролл)", _scroll_zoom),
    Action("scroll.arrows_v", "Стрелки вверх/вниз", "Прокрутка", "delta", "↑↓", "Нажатия стрелок вверх-вниз", _scroll_arrows_v),
    Action("scroll.arrows_h", "Стрелки влево/вправо", "Прокрутка", "delta", "←→", "Нажатия стрелок влево-вправо", _scroll_arrows_h),
    Action("edit.undo", "Отменить", "Текст и буфер", "trigger", "Отмена", "Отменить последнее действие (Ctrl+Z)", _hotkey, default_param="ctrl+z"),
    Action("edit.redo", "Повторить", "Текст и буфер", "trigger", "Повтор", "Повторить последнее действие (Ctrl+Y)", _hotkey, default_param="ctrl+y"),
    Action("edit.select_all", "Выделить всё", "Текст и буфер", "trigger", "Выдел всё", "Выделить всё (Ctrl+A)", _hotkey, default_param="ctrl+a"),
    Action("edit.save", "Сохранить", "Текст и буфер", "trigger", "Сохран", "Сохранить документ (Ctrl+S)", _hotkey, default_param="ctrl+s"),
    Action("edit.find", "Найти", "Текст и буфер", "trigger", "Найти", "Поиск по документу или странице (Ctrl+F)", _hotkey, default_param="ctrl+f"),
    Action("edit.print", "Печать", "Текст и буфер", "trigger", "Печать", "Отправить на печать (Ctrl+P)", _hotkey, default_param="ctrl+p"),
    Action("clipboard.history", "История буфера", "Текст и буфер", "trigger", "Буфер ист", "Журнал буфера обмена Windows (Win+V)", _hotkey, default_param="win+v"),
    Action("text.lang", "Сменить язык ввода", "Текст и буфер", "trigger", "Язык", "Переключить раскладку (Win+Пробел)", _hotkey, default_param="win+space"),
    Action("clipboard.copy", "Копировать", "Текст и буфер", "trigger", "Копир", "Копировать в буфер (Ctrl+C)", _hotkey, default_param="ctrl+c"),
    Action("clipboard.paste", "Вставить", "Текст и буфер", "trigger", "Встав", "Вставить из буфера (Ctrl+V)", _hotkey, default_param="ctrl+v"),
    Action("clipboard.cut", "Вырезать", "Текст и буфер", "trigger", "Вырез", "Вырезать в буфер (Ctrl+X)", _hotkey, default_param="ctrl+x"),
    Action("text.type", "Вставить текст", "Текст и буфер", "trigger", "Текст", "Вставить заданный текст", _text_type, param_kind="text"),
    Action("custom.hotkey", "Своя комбинация клавиш", "Свои", "trigger", "Хоткей", "Произвольное сочетание клавиш", _hotkey, param_kind="hotkey"),
    Action("custom.run", "Запустить программу", "Свои", "trigger", "Прогр", "Запустить исполняемый файл", _startfile, param_kind="exe"),
    Action("custom.folder", "Открыть папку", "Свои", "trigger", "Папка", "Открыть папку в Проводнике", _startfile, param_kind="folder"),
    Action("custom.url", "Открыть ссылку", "Свои", "trigger", "Ссылка", "Открыть ссылку в браузере", _startfile, param_kind="url"),
    Action("pads.brightness", "Яркость пэдов", "Подсветка", "delta", "Ярк пэд", "Общая яркость подсветки всех пэдов", _pad_brightness),
    Action("pads.brightness_up", "Яркость пэдов +", "Подсветка", "trigger", "Ярк пэд +", "Поднять общую яркость пэдов на шаг", lambda p, d: _pad_brightness(p, 5)),
    Action("pads.brightness_down", "Яркость пэдов −", "Подсветка", "trigger", "Ярк пэд −", "Опустить общую яркость пэдов на шаг", lambda p, d: _pad_brightness(p, -5)),
    Action("monitor.brightness", "Яркость мониторов (DimTray)", "Подсветка", "delta", "Ярк экран", "Плавно менять яркость всех мониторов через DimTray", _monitor_brightness),
    Action("monitor.brightness_up", "Яркость мониторов +", "Подсветка", "trigger", "Экран +", "Поднять яркость мониторов на 5% через DimTray", _monitor_step(5)),
    Action("monitor.brightness_down", "Яркость мониторов −", "Подсветка", "trigger", "Экран −", "Опустить яркость мониторов на 5% через DimTray", _monitor_step(-5)),
    Action("monitor.brightness_reset", "Мониторы на 100%", "Подсветка", "trigger", "Экран 100", "Вернуть все мониторы на полную яркость", _monitor_reset),
]

ACTIONS = {a.id: a for a in ACTIONS_LIST}
CATEGORY_ORDER = ["Медиа", "Звук", "Подсветка", "Окна", "Рабочие столы", "Браузер", "Система", "Прокрутка", "Текст и буфер", "Свои"]

def get(action_id: str) -> Optional[Action]:
    return ACTIONS.get(action_id)

def for_kind(kind: str) -> list[Action]:
    return [a for a in ACTIONS_LIST if a.kind == kind]

def execute(action_id: str, param=None, delta: int = 0) -> Optional[str]:
    action = get(action_id)
    if not action:
        return f"Неизвестное действие: {action_id}"
    
    p = action.default_param if param is None else param
    try:
        err = action.handler(p, delta)
        if err is not None:
            return str(err)
        return None
    except Exception as e:
        return str(e)


def perform_action(action_str, delta=0):
    if not action_str or action_str == "None": execute("none", delta=delta)
    elif action_str == "Громкость (Крутилка)": execute("audio.volume", delta=delta)
    elif action_str == "Скролл (Крутилка)": execute("scroll.wheel", delta=delta)
    elif action_str == "Volume Up": execute("media.volume_up", delta=delta)
    elif action_str == "Volume Down": execute("media.volume_down", delta=delta)
    elif action_str == "Play/Pause": execute("media.play_pause", delta=delta)
    elif action_str == "Next Track": execute("media.next_track", delta=delta)
    elif action_str == "Prev Track": execute("media.prev_track", delta=delta)
    elif action_str == "Mute": execute("media.mute", delta=delta)
    elif action_str.startswith("Custom: "): execute("custom.hotkey", param=action_str[8:], delta=delta)
    elif action_str.startswith("Run: "): execute("custom.run", param=action_str[5:], delta=delta)
    elif get(action_str): execute(action_str, delta=delta)
    else: pass
