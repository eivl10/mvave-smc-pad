"""Дымовой тест вендорского GATT-канала SMC-PAD.

Проверяет единственную рискованную неизвестную всей затеи: отвечает ли
характеристика AE41 на этом конкретном устройстве и этом стеке Windows.
Пока это не подтверждено — дальше двигаться бессмысленно.

Запуск:  python tools/smoke_vendor.py

Что делает с устройством: пишет в ОЗУ пресета байт Led (0xFF — заводское
значение) и поле RGB нижнего левого пэда. Записи волатильные — передёргивание
питания возвращает всё как было. Ни флеш, ни сохранённые пресеты не трогаются.
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bleak import BleakClient

from mvave.protocol import (
    ACK, LED_UNARMED, VENDOR_NOTIFY_CHAR, VENDOR_WRITE_CHAR,
    led_packet, parse_state_block, read_response_data, rgb_packet, state_read_packet,
)

# Адрес своего контроллера: переменная окружения SMC_PAD_MAC (виден в
# «Диспетчере устройств» → SMC-PAD → Код экземпляра, BTHLE\DEV_<адрес>).
MAC = os.environ.get("SMC_PAD_MAC", "")
if not MAC:
    sys.exit("задай SMC_PAD_MAC, например: set SMC_PAD_MAC=AA:BB:CC:DD:EE:FF")

# Обнаружение сервисов мимо кэша винды идёт заметно дольше обычного —
# десяти секунд не хватает, проверено.
TIMEOUT_UNCACHED = 45.0
TIMEOUT_CACHED = 20.0


def make_client(use_cache):
    """Клиент с кэшем сервисов Windows или без него.

    Кэш GATT винды может не отдать сервис AE40, если он не был перечислен при
    сопряжении; тогда запись падает с «characteristic not found», и отказ
    выглядит протокольным, хотя таковым не является. Поэтому первая попытка —
    мимо кэша. Но полное переобнаружение медленное и само по себе может не
    уложиться в таймаут, так что при неудаче откатываемся на кэш.
    """
    if not use_cache:
        try:
            return BleakClient(MAC, timeout=TIMEOUT_UNCACHED,
                               winrt={"use_cached_services": False})
        except TypeError:
            print("!! bleak не принял winrt-kwarg, иду через кэш")
    return BleakClient(MAC, timeout=TIMEOUT_CACHED)


async def main():
    for use_cache in (False, True):
        how = "с кэшем сервисов" if use_cache else "мимо кэша сервисов"
        print(f"Подключение к {MAC} ({how}, таймаут "
              f"{TIMEOUT_CACHED if use_cache else TIMEOUT_UNCACHED:.0f} с) ...")
        try:
            await run(make_client(use_cache))
            return
        except (asyncio.TimeoutError, TimeoutError):
            if use_cache:
                print("\nПРОВАЛ: подключиться не удалось ни одним способом.\n"
                      "Проверь: устройство включено, сопряжено в Параметрах\n"
                      "Bluetooth, и не занято MidiSuite или другим приложением.")
                return
            print("!! таймаут на обнаружении сервисов, пробую через кэш\n")


async def run(client):
    started = time.time()
    sent = 0
    acks = 0
    notifications = []
    state_event = asyncio.Event()

    def on_notify(_sender, data):
        nonlocal acks
        raw = bytes(data)
        line = raw.hex(" ").upper()
        if raw == ACK:
            acks += 1
            print(f"<- {line}   (ACK)")
        else:
            notifications.append(raw)
            print(f"<- {line}")
            state_event.set()

    async with client:
        print(f"Подключено. MTU = {client.mtu_size}")

        print("\n--- Сервисы и характеристики ---")
        found = {}
        for svc in client.services:
            print(f"service {svc.uuid}")
            for ch in svc.characteristics:
                print(f"    char {ch.uuid}  {ch.properties}")
                found[ch.uuid.lower()] = ch

        if VENDOR_WRITE_CHAR not in found:
            print(
                "\nПРОВАЛ ГЕЙТА: характеристика AE41 не найдена.\n"
                "Дальше идти бессмысленно. Полный список сервисов выше — по нему\n"
                "видно, отдала ли винда вендорский сервис вообще. Если AE40 нет\n"
                "в списке, вероятная причина — кэш GATT: удалить устройство из\n"
                "Параметров Bluetooth, сопрячь заново и повторить."
            )
            return

        await client.start_notify(VENDOR_NOTIFY_CHAR, on_notify)

        async def send(frame, what):
            nonlocal sent
            print(f"-> {frame.hex(' ').upper()}   ({what})")
            await client.write_gatt_char(VENDOR_WRITE_CHAR, frame, response=False)
            sent += 1

        print("\n--- Чтение live-состояния ---")
        await send(state_read_packet(), "чтение slot/bank")

        slot, bank = 0, 3
        try:
            await asyncio.wait_for(state_event.wait(), timeout=1.5)
        except asyncio.TimeoutError:
            print("!! ответа на чтение нет за 1.5 с — фолбэк на slot=0, bank=3")
        else:
            try:
                block = read_response_data(notifications[0])
                print(f"   блок состояния: {block.hex(' ').upper()}")
                slot, bank = parse_state_block(block)
            except (ValueError, IndexError) as exc:
                print(f"!! ответ не разбирается ({exc}) — фолбэк на slot=0, bank=3")
            else:
                print(f"Состояние разобрано: slot={slot}, bank={bank}")

        print("\n--- Запись цвета в record 0 (нижний левый пэд) ---")
        await send(led_packet(0, LED_UNARMED, slot, bank), "Led=0xFF, снять arm")
        await asyncio.sleep(0.025)
        await send(rgb_packet(0, 0, 255, 0, slot, bank), "RGB зелёный")

        print("   ... держу 2 с, смотри на пэд ...")
        await asyncio.sleep(2.0)

        await send(rgb_packet(0, 0, 0, 0, slot, bank), "RGB выключить")
        await asyncio.sleep(0.5)

    print("\n--- ИТОГ ---")
    print(f"Время:           {time.time() - started:.2f} с")
    print(f"Фреймов послано: {sent}")
    print(f"ACK получено:    {acks}")
    print(f"Прочих нотификаций: {len(notifications)}")
    print(
        "\nЧто должно было произойти глазами: нижний левый пэд загорелся\n"
        "зелёным примерно на 2 секунды, затем погас.\n"
        "\nРазбор результата:\n"
        "  пэд горел + ACK есть      -> гейт пройден полностью\n"
        "  пэд горел, ACK нет        -> запись работает, не подписались на AE42; мелочь\n"
        "  ACK есть, пэд не горел    -> скорее всего не тот slot/bank. Сверь\n"
        "                               напечатанные выше slot/bank с тем, какой\n"
        "                               пресет реально на дисплее устройства\n"
        "  ни того, ни другого       -> гейт провален, нужен разбор"
    )


if __name__ == "__main__":
    asyncio.run(main())
