"""BLE-транспорт SMC-PAD.

Два независимых канала на одном подключении:
  * MIDI-характеристика — вход (ноты и CC от устройства);
  * вендорский GATT AE41/AE42 — цвет пэдов.

Цвет по MIDI Note On НЕ работает: заводское значение байта `Led` = 0xFF
выводит пэд из-под управления хостом (FIXES, запись от 2026-09-17).
Единственный рабочий путь — вендорский фрейм в AE41.
"""
import asyncio
import queue

from bleak import BleakClient, BleakScanner

from mvave import appconfig
from mvave import protocol

# Устройство ищется по имени, которое оно рекламирует: у каждого экземпляра
# свой MAC, зашитый адрес работал бы только с одним контроллером.
# Ускорить можно ключом "ble_address" в midi_config.json.
DEVICE_NAME = "SMC-PAD"
MIDI_CHAR = "7772E5DB-3868-4112-A1A9-F2669D106BF3"
_address = None   # найденный адрес: переподключение не сканирует заново

msg_queue = queue.Queue()   # BLE-поток → Tk
cmd_queue = queue.Queue()   # Tk → BLE-поток

# Темп между вендорскими фреймами. ACK не ждём: устройство отвечает ~410 мс,
# на клик по цвету это ощущалось бы как зависание.
VENDOR_PACE = 0.025

# Цвет пэда, у которого своего цвета нет. Должен совпадать с DEFAULT_PAD_COLOR
# в midi_gui: экран обязан показывать то же, что уходит на железо.
DEFAULT_PAD_COLOR = "#ffffff"

_debug_callback = None
_first_connect = True


def set_debug_callback(fn):
    global _debug_callback
    _debug_callback = fn


def _log(msg):
    if _debug_callback:
        _debug_callback(msg)


def notification_handler(sender, data):
    """Вход с MIDI-характеристики."""
    _log(f"RAW [{len(data)}]: {' '.join(f'{b:02X}' for b in data)}")
    for msg in protocol.parse_ble_midi(data):
        msg_queue.put_nowait(msg)


# ── API для Tk-потока ────────────────────────────────────────────────────────

def set_pad_color(pad_num, hex_col):
    """Поставить цвет пэда в очередь отправки. Вызывается из Tk-потока."""
    cmd_queue.put(("rgb", int(pad_num), hex_col))


def resend_all_colors():
    cmd_queue.put(("reapply", None, None))


def set_bank(bank):
    """Сообщить транспорту активный банк: адрес записи пэда зависит от банка."""
    cmd_queue.put(("bank", None, int(bank)))


# ── вендорский канал ─────────────────────────────────────────────────────────

async def _vendor_write(client, frame):
    await client.write_gatt_char(protocol.VENDOR_WRITE_CHAR, frame, response=False)
    await asyncio.sleep(VENDOR_PACE)


async def _read_state(state, timeout=2.0):
    """Спросить у устройства живой слот и банк.

    Ответ приходит полным вендорским фреймом на AE42, вперемешку с ACK.
    ACK короче минимального ответа, поэтому отсеивается разбором.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        left = deadline - loop.time()
        if left <= 0:
            raise asyncio.TimeoutError("устройство не ответило на чтение состояния")
        frame = await asyncio.wait_for(state["notify"].get(), timeout=left)
        try:
            return protocol.parse_state_block(protocol.read_response_data(bytes(frame)))
        except ValueError:
            continue    # ACK или чужой фрейм — ждём дальше


async def _open_vendor(client):
    state = {"vendor": False, "slot": 0, "bank": 3,
             "armed": set(), "notify": asyncio.Queue()}

    def _vendor_notify(sender, data):
        _log(f"AE42 [{len(data)}]: {' '.join(f'{b:02X}' for b in data)}")
        state["notify"].put_nowait(bytes(data))

    try:
        await client.start_notify(protocol.VENDOR_NOTIFY_CHAR, _vendor_notify)
    except Exception as e:
        # Вендорского канала нет — цвет недоступен, но MIDI-вход не роняем.
        msg_queue.put("color:нет")
        _log(f"AE42 недоступен: {type(e).__name__}: {e}")
        return state

    state["vendor"] = True
    try:
        await _vendor_write(client, protocol.state_read_packet())
        slot, bank = await _read_state(state)
        state["slot"], state["bank"] = slot, bank
        msg_queue.put(f"state:{slot}:{bank}")
        _log(f"состояние устройства: слот {slot}, банк {bank}")
    except Exception as e:
        # Запись в чужой банк ACK-ается и молча ничего не делает,
        # поэтому банк неизвестен — честно говорим об этом в UI.
        msg_queue.put("state:?:?")
        _log(f"состояние не прочитано: {type(e).__name__}: {e}")
    msg_queue.put("color:есть")
    return state


async def _write_pad_color(client, state, pad_num, hex_col):
    rec = int(pad_num) - 1
    if not 0 <= rec < protocol.PAD_COUNT:
        return
    try:
        r, g, b = protocol.hex_to_rgb(str(hex_col))
    except (ValueError, AttributeError):
        return
    # Общая яркость — живой множитель поверх сохранённого цвета, а не часть
    # его. Иначе ползунок пришлось бы «вжигать» в каждый из 16 цветов.
    r, g, b = protocol.scale_rgb(r, g, b, appconfig.config.get("pad_brightness", 100))
    r, g, b = protocol.rgb_for_device(r, g, b)

    if rec not in state["armed"]:
        # Заармленный пэд принимает RGB и не показывает его. Разармливаем
        # один раз на пэд на подключение: записи волатильные.
        await _vendor_write(client, protocol.led_packet(
            rec, protocol.LED_UNARMED, state["slot"], state["bank"]))
        state["armed"].add(rec)

    await _vendor_write(client, protocol.rgb_packet(
        rec, r, g, b, state["slot"], state["bank"]))
    _log(f"RGB pad={pad_num} {hex_col} банк={state['bank']}")


async def _reapply_colors(client, state):
    """Переприменить все цвета из конфига.

    Записи волатильные — теряются при выключении питания, поэтому вызывается
    на каждом подключении, а не один раз при старте приложения.
    """
    if not state["vendor"]:
        return
    bindings = appconfig.config.get("bindings", {})
    for num in range(1, protocol.PAD_COUNT + 1):
        col = bindings.get(f"pad_{num}", {}).get("color")
        if not (isinstance(col, str) and col.startswith("#")):
            # Пэд без своего цвета тоже наш: иначе общий фейдер яркости
            # не достал бы до него, и часть пэдов жила бы заводским цветом.
            col = DEFAULT_PAD_COLOR
        await _write_pad_color(client, state, num, col)


async def _drain_commands(client, state):
    """Забрать очередь целиком, схлопнув повторы по (вид, пэд).

    Ползунок яркости шлёт команду на каждое движение; без схлопывания
    очередь растёт быстрее, чем уходит в эфир при темпе 25 мс.
    """
    pending = {}
    order = []
    while True:
        try:
            kind, key, value = cmd_queue.get_nowait()
        except queue.Empty:
            break
        k = (kind, key)
        if k not in pending:
            order.append(k)
        pending[k] = value

    for k in order:
        kind, key = k
        value = pending[k]
        if kind == "bank":
            if value != state["bank"]:
                state["bank"] = value
                state["armed"].clear()   # адрес записи пэда зависит от банка
                await _reapply_colors(client, state)
        elif kind == "reapply":
            await _reapply_colors(client, state)
        elif kind == "rgb" and state["vendor"]:
            await _write_pad_color(client, state, key, value)


# ── основной цикл ────────────────────────────────────────────────────────────

BATTERY_CHAR = "00002a19-0000-1000-8000-00805f9b34fb"   # Battery Level, 0-100 %


def _on_battery(_handle, data):
    if data:
        msg_queue.put(f"battery:{int(data[0])}")


async def _open_battery(client):
    """Заряд: прочитать сразу, дальше устройство само шлёт изменения.

    Замер 2026-09-23: 2A19 у SMC-PAD — read + notify, читается без сопряжения.
    Сбой здесь не должен рвать подключение: заряд — справка, не функция.
    """
    try:
        _on_battery(None, await client.read_gatt_char(BATTERY_CHAR))
        await client.start_notify(BATTERY_CHAR, _on_battery)
    except Exception as e:
        _log(f"заряд недоступен: {type(e).__name__}: {e}")


async def _resolve_address():
    """Адрес контроллера: из конфига, из прошлого подключения или поиском."""
    global _address
    if _address:
        return _address
    configured = appconfig.config.get("ble_address")
    if configured:
        _address = configured
        return _address
    msg_queue.put(f"status:Поиск {DEVICE_NAME}...")
    dev = await BleakScanner.find_device_by_filter(
        lambda d, adv: (d.name or adv.local_name or "").upper().startswith(DEVICE_NAME),
        timeout=10.0)
    if dev is None:
        return None
    _log(f"найдено {dev.name} {dev.address}")
    _address = dev.address
    return _address


async def ble_loop():
    global _first_connect
    while True:
        try:
            address = await _resolve_address()
            if not address:
                msg_queue.put(f"status:{DEVICE_NAME} не найден")
                await asyncio.sleep(3)
                continue
            msg_queue.put("status:Подключение...")
            kwargs = {"timeout": 10.0}
            if _first_connect:
                # Кэш GATT Windows может не отдать вендорский сервис AE40.
                # На первом подключении сессии перечитываем сервисы заново.
                kwargs["winrt"] = {"use_cached_services": False}
            async with BleakClient(address, **kwargs) as client:
                _first_connect = False
                if not client.is_connected:
                    msg_queue.put("status:Ошибка")
                    await asyncio.sleep(2)
                    continue

                msg_queue.put("status:Подключено")
                await client.start_notify(MIDI_CHAR, notification_handler)
                await _open_battery(client)

                state = await _open_vendor(client)
                await _reapply_colors(client, state)

                while client.is_connected:
                    await _drain_commands(client, state)
                    await asyncio.sleep(0.05)
        except Exception as e:
            msg_queue.put("battery:-1")   # старый процент без связи — неправда
            msg_queue.put("status:Переподключение...")
            _log(f"BLE: {type(e).__name__}: {e}")
            await asyncio.sleep(2)


def start_ble_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(ble_loop())
