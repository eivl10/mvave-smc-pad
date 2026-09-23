"""Диагностика: почему запись RGB принимается, но пэд не горит.

Дымовой тест показал: канал AE41 работает, ACK приходят, фрейм на проводе
совпадает с эталоном. Значит отказ дальше по цепочке. Этот скрипт разделяет
три возможные причины, читая память устройства обратно:

  A. Запись вообще не долетает до памяти   -> read-back не покажет наш цвет
  B. Долетает, но не в тот банк/слот        -> перебор банков и слотов
  C. Долетает куда надо, но не отображается -> read-back совпал, а света нет

Плюс лесенка яркости: есть ли у драйвера светодиода промежуточные уровни.

Запуск:  python tools/probe_color.py [этап]
         этапы: readback | banks | slots | records | dim | all   (по умолчанию all)

Всё, что пишется — волатильные правки ОЗУ пресета. Передёргивание питания
возвращает устройство как было. Флеш не трогается.
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bleak import BleakClient

from mvave.protocol import (
    ACK, LED_UNARMED, PAD_LED_OFFSET, PAD_NOTE_OFFSET, PAD_RECORD_SIZE,
    PAD_RGB_OFFSET, PRESET_REGION, VENDOR_NOTIFY_CHAR, VENDOR_WRITE_CHAR,
    led_packet, pad_record_address, parse_state_block, read_packet,
    read_response_data, rgb_packet, state_read_packet, write_packet,
)

# Адрес своего контроллера: переменная окружения SMC_PAD_MAC (виден в
# «Диспетчере устройств» → SMC-PAD → Код экземпляра, BTHLE\DEV_<адрес>).
MAC = os.environ.get("SMC_PAD_MAC", "")
if not MAC:
    sys.exit("задай SMC_PAD_MAC, например: set SMC_PAD_MAC=AA:BB:CC:DD:EE:FF")
HOLD = 0.9          # сколько держать цвет, чтобы успеть увидеть
GAP = 0.03          # пауза между вендорскими фреймами


class Device:
    """Тонкая обёртка: запрос-ответ поверх AE41/AE42."""

    def __init__(self, client):
        self.client = client
        self.pending = None
        self.acks = 0
        self.sent = 0

    def on_notify(self, _sender, data):
        raw = bytes(data)
        if raw == ACK:
            self.acks += 1
            return
        if self.pending is not None and not self.pending.done():
            self.pending.set_result(raw)

    async def send(self, frame):
        await self.client.write_gatt_char(VENDOR_WRITE_CHAR, frame, response=False)
        self.sent += 1
        await asyncio.sleep(GAP)

    async def read_mem(self, address, count, region=PRESET_REGION, timeout=2.0):
        self.pending = asyncio.get_running_loop().create_future()
        await self.send(read_packet(address, count, region))
        try:
            frame = await asyncio.wait_for(self.pending, timeout=timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self.pending = None
        try:
            return read_response_data(frame)
        except ValueError as exc:
            print(f"    !! ответ не разбирается: {exc}")
            return None

    async def dump_record(self, rec, slot, bank, label=""):
        addr = pad_record_address(rec, slot, bank)
        data = await self.read_mem(addr, PAD_RECORD_SIZE)
        if data is None:
            print(f"    запись {rec} @0x{addr:04X}: ответа нет {label}")
            return None
        note = data[PAD_NOTE_OFFSET]
        rgb = tuple(data[PAD_RGB_OFFSET:PAD_RGB_OFFSET + 3])
        led = data[PAD_LED_OFFSET]
        print(f"    запись {rec} @0x{addr:04X}: note={note} rgb={rgb} led=0x{led:02X} {label}")
        print(f"      сырьё: {data.hex(' ').upper()}")
        return data


async def stage_readback(dev, slot, bank):
    print("\n=== A. Долетает ли запись до памяти ===")
    print("  До записи:")
    before = await dev.dump_record(0, slot, bank)

    print("  Пишу Led=0xFF и RGB=(255,0,0) ...")
    await dev.send(led_packet(0, LED_UNARMED, slot, bank))
    await dev.send(rgb_packet(0, 255, 0, 0, slot, bank))
    await asyncio.sleep(0.5)

    print("  После записи:")
    after = await dev.dump_record(0, slot, bank)

    if before is None or after is None:
        print("  ВЫВОД: прочитать не удалось, этап неинформативен")
        return None
    got = tuple(after[PAD_RGB_OFFSET:PAD_RGB_OFFSET + 3])
    if got == (255, 0, 0):
        print("  ВЫВОД: запись ДОЛЕТАЕТ до памяти. Причина не в записи, а в "
              "отображении — не тот банк/слот, либо нужен иной триггер обновления.")
        return True
    print(f"  ВЫВОД: запись НЕ долетела, в памяти {got}. ACK приходит, "
          "но данные не применяются — вероятно неверен регион или адрес.")
    return False


async def stage_banks(dev, slot):
    print("\n=== B. Перебор банков (запись 0, белый) — СМОТРИ НА ПЭДЫ ===")
    for bank in range(1, 9):
        print(f"  банк {bank} ...", flush=True)
        await dev.send(led_packet(0, LED_UNARMED, slot, bank))
        await dev.send(rgb_packet(0, 255, 255, 255, slot, bank))
        await asyncio.sleep(HOLD)
        await dev.send(rgb_packet(0, 0, 0, 0, slot, bank))


async def stage_slots(dev, bank):
    print("\n=== C. Перебор слотов (запись 0, белый) — СМОТРИ НА ПЭДЫ ===")
    for slot in range(8):
        print(f"  слот {slot} ...", flush=True)
        await dev.send(led_packet(0, LED_UNARMED, slot, bank))
        await dev.send(rgb_packet(0, 255, 255, 255, slot, bank))
        await asyncio.sleep(HOLD)
        await dev.send(rgb_packet(0, 0, 0, 0, slot, bank))


async def stage_records(dev, slot, bank):
    print(f"\n=== D. Перебор всех 16 записей (slot={slot}, bank={bank}) — "
          "СМОТРИ, КАКОЙ ПЭД ЗАГОРИТСЯ ===")
    for rec in range(16):
        print(f"  запись {rec} ...", flush=True)
        await dev.send(led_packet(rec, LED_UNARMED, slot, bank))
        await dev.send(rgb_packet(rec, 255, 255, 255, slot, bank))
        await asyncio.sleep(HOLD)
        await dev.send(rgb_packet(rec, 0, 0, 0, slot, bank))


async def stage_dim(dev, slot, bank):
    print("\n=== E. Лесенка яркости на записи 0 — ЕСТЬ ЛИ ПРОМЕЖУТОЧНЫЕ УРОВНИ ===")
    await dev.send(led_packet(0, LED_UNARMED, slot, bank))
    for level in (255, 192, 128, 64, 32, 16, 8, 4, 1):
        print(f"  уровень {level:3d} ...", flush=True)
        await dev.send(rgb_packet(0, level, level, level, slot, bank))
        await asyncio.sleep(HOLD)
    await dev.send(rgb_packet(0, 0, 0, 0, slot, bank))
    print("  Если уровни визуально различались — яркость делается масштабированием RGB.")
    print("  Если было только «горит/не горит» — промежуточных уровней у драйвера нет.")


async def stage_header(dev, slot):
    """Дамп областей, где могут жить глобальные настройки подсветки.

    Гипотеза: есть общий уровень яркости или флаг включения LED, и при нуле
    ничего не покажется, сколько ни пиши в записи пэдов.
    """
    print("\n=== F. Заголовок пресета (region 5, до таблицы пэдов) ===")
    base = slot * 3539
    for off in range(0, 211, 32):
        count = min(32, 211 - off)
        data = await dev.read_mem(base + off, count)
        if data is None:
            print(f"  +{off:03d}: ответа нет")
            continue
        print(f"  +{off:03d}: {data.hex(' ').upper()}")

    print("\n=== G. Live-регион 0x04 ===")
    for off in range(0, 64, 32):
        data = await dev.read_mem(off, 32, region=0x04)
        if data is None:
            print(f"  +{off:03d}: ответа нет")
            continue
        print(f"  +{off:03d}: {data.hex(' ').upper()}")


async def read_pad_table(dev, slot, bank):
    """Прочитать все 16 записей пэдов банка. Возвращает список записей или None."""
    base = pad_record_address(0, slot, bank)
    total = 16 * PAD_RECORD_SIZE
    blob = bytearray()
    chunk = 4 * PAD_RECORD_SIZE          # 104 байта за раз
    for off in range(0, total, chunk):
        part = await dev.read_mem(base + off, min(chunk, total - off))
        if part is None:
            return None
        blob += part
    if len(blob) != total:
        return None
    return [bytes(blob[i * PAD_RECORD_SIZE:(i + 1) * PAD_RECORD_SIZE])
            for i in range(16)]


def summarize_table(records):
    """Короткая сводка банка: сколько пэдов с ненулевым цветом, ноты, типы."""
    colors = [tuple(r[PAD_RGB_OFFSET:PAD_RGB_OFFSET + 3]) for r in records]
    notes = [r[PAD_NOTE_OFFSET] for r in records]
    types = sorted({r[0] for r in records})
    leds = sorted({r[PAD_LED_OFFSET] for r in records})
    lit = sum(1 for c in colors if any(c))
    return colors, notes, types, leds, lit


async def stage_scan(dev):
    """Найти, где на самом деле лежат цвета, которые устройство показывает.

    Пэды светятся своими цветами в покое, значит поле RGB где-то рендерится.
    Ищем банк/слот, где шестнадцать RGB ненулевые — он и есть отображаемый.
    Только чтение, ничего не пишем.
    """
    print("\n=== H. Поиск отображаемой таблицы пэдов (только чтение) ===")
    found = []
    for slot in range(8):
        for bank in range(1, 9):
            records = await read_pad_table(dev, slot, bank)
            if records is None:
                print(f"  slot={slot} bank={bank}: прочитать не удалось")
                continue
            colors, notes, types, leds, lit = summarize_table(records)
            flag = ""
            if lit:
                flag = "  <<< ЕСТЬ ЦВЕТА"
                found.append((slot, bank, colors, notes))
            print(f"  slot={slot} bank={bank}: цветных {lit:2d}/16  "
                  f"ноты {notes[0]}..{notes[-1]}  типы {types}  led {[hex(x) for x in leds]}{flag}")

    if not found:
        print("\n  Нигде в region 5 ненулевых цветов нет. Значит то, что светится,\n"
              "  берётся не из этой таблицы — цвет живёт в другом регионе.")
        return

    print("\n  --- Найденные таблицы с цветами ---")
    for slot, bank, colors, notes in found:
        print(f"\n  slot={slot} bank={bank}, ноты {notes[0]}..{notes[-1]}")
        for row in range(3, -1, -1):        # сверху вниз, как на устройстве
            cells = []
            for col in range(4):
                rec = row * 4 + col
                r, g, b = colors[rec]
                cells.append(f"#{r:02x}{g:02x}{b:02x}")
            print(f"    {'  '.join(cells)}")


async def stage_paint(dev, slot, bank):
    """Красит нижний ряд убывающей яркостью и ОСТАВЛЯЕТ как есть.

    Отвечает сразу на два вопроса: видно ли нашу запись вообще, и различает
    ли драйвер промежуточные уровни (то есть работает ли яркость через
    масштабирование RGB).
    """
    ramp = [(0, 255), (1, 120), (2, 48), (3, 12)]
    print(f"\n=== I. Нижний ряд, убывающая яркость (slot={slot}, bank={bank}) ===")
    for rec, level in ramp:
        await dev.send(led_packet(rec, LED_UNARMED, slot, bank))
        await dev.send(rgb_packet(rec, 0, level, level, slot, bank))
        print(f"  пэд {rec + 1}: бирюзовый, уровень {level}")
    print("\n  Смотри на нижний ряд: должен идти от яркого к еле светящемуся.")
    print("  Цвет остаётся — специально, гасить не буду.")


async def stage_restore(dev, slot, bank, hex_color):
    """Вернуть всему банку один цвет — тот, что был до наших опытов."""
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    print(f"\n=== Восстановление банка {bank} слота {slot} в #{hex_color} ===")
    for rec in range(16):
        await dev.send(led_packet(rec, LED_UNARMED, slot, bank))
        await dev.send(rgb_packet(rec, r, g, b, slot, bank))
    print("  Готово. Записи волатильные — выключение питания вернёт заводские.")


async def main():
    stages = sys.argv[1:] or ["all"]
    if "all" in stages:
        stages = ["readback", "banks", "slots", "records", "dim"]

    print(f"Подключение к {MAC} ...")
    async with BleakClient(MAC, timeout=45.0) as client:
        print(f"Подключено, MTU={client.mtu_size}")
        dev = Device(client)
        await client.start_notify(VENDOR_NOTIFY_CHAR, dev.on_notify)

        slot, bank = 0, 3
        dev.pending = asyncio.get_running_loop().create_future()
        await dev.send(state_read_packet())
        try:
            frame = await asyncio.wait_for(dev.pending, timeout=2.0)
            block = read_response_data(frame)
            slot, bank = parse_state_block(block)
            print(f"Состояние: {block.hex(' ').upper()}")
            print(f"  -> slot={slot}, bank={bank}")
        except (asyncio.TimeoutError, ValueError) as exc:
            print(f"!! состояние не прочиталось ({exc}), беру slot=0 bank=3")
        finally:
            dev.pending = None

        started = time.time()
        if "readback" in stages:
            await stage_readback(dev, slot, bank)
        if "banks" in stages:
            await stage_banks(dev, slot)
        if "slots" in stages:
            await stage_slots(dev, bank)
        if "records" in stages:
            await stage_records(dev, slot, bank)
        if "dim" in stages:
            await stage_dim(dev, slot, bank)
        if "header" in stages:
            await stage_header(dev, slot)
        if "scan" in stages:
            await stage_scan(dev)
        if "paint" in stages:
            await stage_paint(dev, slot, bank)
        if "restore" in stages:
            await stage_restore(dev, slot, bank, "37e6c9")

        print(f"\n--- фреймов {dev.sent}, ACK {dev.acks}, "
              f"{time.time() - started:.1f} с ---")


if __name__ == "__main__":
    asyncio.run(main())
