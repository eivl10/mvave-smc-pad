"""Тесты чистого кодека вендорского протокола. Железо не требуется.

Запускается двумя способами:
    python -m pytest tests/test_protocol.py -v
    python tests/test_protocol.py          # если pytest не установлен
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mvave.protocol import (
    vendor_packet, rgb_packet, write_packet, led_packet, pad_record_address,
    parse_state_block, verify_checksum, record_for_note, hex_to_rgb, rgb_to_hex,
    parse_ble_midi, read_response_data,
    PAD_RGB_OFFSET, PAD_COUNT, PRESET_REGION, WRITE_COMMAND
)


def test_golden_rgb_packet():
    pkt = rgb_packet(record_index=0, r=0x00, g=0xFF, b=0x00, slot=0, bank=3)
    expected = bytes.fromhex("00 59 22 0B 00 00 05 18 04 00 00 03 00 00 00 FF 00 DC")
    assert pkt == expected, pkt.hex(" ")
    assert verify_checksum(pkt)


def test_checksum_invariant():
    random.seed(42)
    for _ in range(200):
        address = random.randint(0, 0xFFFFFFFF)
        region = random.randint(0, 0xFF)
        cmd = random.choice([0x22, 0x23, 0x11])
        data_len = random.randint(0, 50)
        data = bytes(random.choices(range(256), k=data_len))

        pkt = vendor_packet(cmd, region, address, data)
        assert verify_checksum(pkt)


def test_pad_record_address():
    """Сверка общей формулы с известной частной константой для slot 0 / bank 3."""
    for r in range(16):
        addr = pad_record_address(r, 0, 3)
        assert addr + PAD_RGB_OFFSET == 0x0418 + r * 0x1A


def test_rgb_packet_length():
    """Сторож: фрейм обязан влезать в минимальный ATT payload 20 байт."""
    for r in range(16):
        pkt = rgb_packet(record_index=r, r=255, g=255, b=255, slot=0, bank=3)
        assert len(pkt) <= 20


def test_record_for_note():
    assert record_for_note(36) == 0
    assert record_for_note(51) == 15
    assert record_for_note(35) is None
    assert record_for_note(52) is None


def test_parse_state_block():
    # Валидный пример из docs/PROTOCOL.md §5
    valid_data = bytes.fromhex("78 00 32 04 00 00 02 00 01 01 00 00 00 00 00 00")
    slot, bank = parse_state_block(valid_data)
    assert slot == 0
    assert bank == 3

    # BB=1 → банк-образ 8
    valid_data2 = bytes.fromhex("78 00 32 04 00 00 02 00 01 01 01 01 00 00 00 00")
    slot, bank = parse_state_block(valid_data2)
    assert slot == 1
    assert bank == 8

    # Битые данные не должны молча возвращать мусор
    try:
        parse_state_block(bytes.fromhex("78 00 32 04 00"))
    except ValueError:
        pass
    else:
        raise AssertionError("короткий блок состояния должен поднимать ValueError")


def test_read_response_data_real_capture():
    """Реальный ответ устройства, снят 2026-09-17 через tools/smoke_vendor.py.

    Именно здесь пряталась ошибка: если скормить `parse_state_block` весь фрейм
    вместо данных внутри него, смещения едут и возвращается правдоподобный
    мусор (bank=5 вместо bank=3) — без единого исключения. Записи тогда уходят
    в чужой банк, устройство исправно шлёт ACK, а пэд не горит.
    """
    frame = bytes.fromhex(
        "00 59 23 18 00 00 04 00 00 00 00 10 00 00"
        "E1 00 3F 00 00 00 02 00 02 01 00 00 00 00 00 00 C6"
    )
    block = read_response_data(frame)
    assert block == bytes.fromhex("E1 00 3F 00 00 00 02 00 02 01 00 00 00 00 00 00")
    assert len(block) == 16

    slot, bank = parse_state_block(block)
    assert (slot, bank) == (0, 3)

    # Сверка с независимым фактом: bank 3 — заводская карта, ноты 36-51,
    # ровно то, что приложение видит на входе (docs/PROTOCOL.md §8).
    assert parse_state_block(frame) != (slot, bank), (
        "разбор сырого фрейма обязан давать другой результат — иначе тест "
        "не ловит ту самую ошибку"
    )


def test_read_response_data_rejects_garbage():
    for bad, why in [
        (b"\x00\x59\x23", "слишком короткий"),
        (bytes.fromhex("AA BB 23 18 00 00 04 00 00 00 00 10 00 00 E1 C6"), "не 00 59"),
        (bytes.fromhex("00 59 23 18 00 00 04 00 00 00 00 10 00 00 E1 FF"), "битая чексумма"),
    ]:
        try:
            read_response_data(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"должен был отвергнуть: {why}")


def test_hex_to_rgb():
    assert hex_to_rgb("#ff00aA") == (255, 0, 170)
    assert hex_to_rgb("00FF00") == (0, 255, 0)
    assert hex_to_rgb("#000000") == (0, 0, 0)


def test_rgb_to_hex():
    assert rgb_to_hex(255, 0, 170).lower() == "#ff00aa"
    assert rgb_to_hex(0, 255, 0).lower() == "#00ff00"


def test_rgb_hex_roundtrip():
    assert hex_to_rgb(rgb_to_hex(12, 34, 56)) == (12, 34, 56)


def test_parse_ble_midi_regression():
    """Векторы синтетические — собраны по логике парсера, не сняты с железа.

    Парсер перенесён из midi_gui.py:124-179 байт-в-байт. Эти тесты фиксируют
    ЕГО поведение, а не идеальное поведение BLE MIDI: задача переноса была
    сохранить существующую отлаженную логику (FIXES.md #5), а не улучшить её.
    """
    # Одиночный Note On: заголовок, таймстемп, статус, нота, velocity
    assert parse_ble_midi(bytes([0x80, 0x80, 0x90, 36, 127])) == ["note:36:127"]

    # Note Off приводится к velocity 0
    assert parse_ble_midi(bytes([0x80, 0x80, 0x80, 36, 127])) == ["note:36:0"]

    # Control Change
    assert parse_ble_midi(bytes([0x80, 0x80, 0xB0, 30, 64])) == ["cc:30:64"]

    # Два сообщения подряд на running status
    assert parse_ble_midi(bytes([0x80, 0x80, 0x90, 36, 127, 37, 100])) == [
        "note:36:127", "note:37:100"
    ]

    # Слишком короткий пакет игнорируется целиком
    assert parse_ble_midi(bytes([0x80, 0x80])) == []


def test_parse_ble_midi_midpacket_timestamp_quirk():
    """ИЗВЕСТНЫЙ КВИРК, зафиксирован намеренно.

    Эвристика различения таймстемпа и статуса: байт с битом 7 считается
    таймстемпом, только если у СЛЕДУЮЩЕГО байта тоже стоит бит 7. Поэтому
    таймстемп в середине пакета, за которым идёт байт данных (продолжение
    running status), принимается за статус-байт и затирает running status.

    Здесь 0x81 читается как Note Off канала 2, и `37 100` отдаётся как
    "note:37:0" вместо "note:37:100".

    На практике не стреляет: устройство шлёт таймстемпы всегда нулевыми (0x80)
    и даёт каждому сообщению собственный статус-байт, без running status
    через таймстемп. Тест существует, чтобы поведение не изменилось молча —
    если понадобится чинить, это осознанное изменение поведения, а не фаза 1.
    """
    assert parse_ble_midi(bytes([0x80, 0x80, 0x90, 36, 127, 0x81, 37, 100])) == [
        "note:36:127", "note:37:0"
    ]


def _main():
    """Прогон без pytest — в песочнице нет сети, pip не достаёт индекс."""
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
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
