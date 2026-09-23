"""M-Vave SMC-PAD BLE Protocol Implementation"""

VENDOR_SERVICE     = "0000ae40-0000-1000-8000-00805f9b34fb"
VENDOR_WRITE_CHAR  = "0000ae41-0000-1000-8000-00805f9b34fb"
VENDOR_NOTIFY_CHAR = "0000ae42-0000-1000-8000-00805f9b34fb"
MIDI_CHAR          = "7772e5db-3868-4112-a1a9-f2669d106bf3"

WRITE_COMMAND = 0x22
READ_COMMAND  = 0x23
PRESET_REGION = 0x05
STATE_REGION  = 0x04
ACK           = bytes.fromhex("00590001000000FF")

PRESET_SIZE      = 3539
PAD_TABLE_OFFSET = 211
PAD_COUNT        = 16
PAD_RECORD_SIZE  = 26
PAD_NOTE_OFFSET  = 2
PAD_RGB_OFFSET   = 5
PAD_LED_OFFSET   = 8
LED_UNARMED      = 0xFF

PAD_NOTE_BASE = 36
KNOB_CC_MAP   = {30:1, 31:2, 32:3, 33:4, 34:5, 35:6, 36:7, 37:8}
CC_TO_KNOB    = KNOB_CC_MAP

def vendor_packet(command: int, region: int, address: int, data_or_count: bytes | int) -> bytes:
    """
    Constructs a vendor protocol packet.
    Reference: docs/PROTOCOL.md §2
    """
    if isinstance(data_or_count, int):
        count = data_or_count
        data = b""
    else:
        data = data_or_count
        count = len(data)
        
    payload = bytes((region, *address.to_bytes(4, "little"), *count.to_bytes(3, "little"))) + data
    checksum = (~sum(payload)) & 0xFF
    return bytes((0x00, 0x59, command, *len(payload).to_bytes(3, "little"))) + payload + bytes((checksum,))

def pad_record_address(record_index: int, slot: int = 0, bank: int = 3) -> int:
    return slot * PRESET_SIZE + PAD_TABLE_OFFSET + ((bank - 1) * PAD_COUNT + record_index) * PAD_RECORD_SIZE

def write_packet(address: int, data: bytes, region: int = PRESET_REGION) -> bytes:
    return vendor_packet(WRITE_COMMAND, region, address, data)

def read_packet(address: int, count: int, region: int = PRESET_REGION) -> bytes:
    return vendor_packet(READ_COMMAND, region, address, count)

def rgb_packet(record_index: int, r: int, g: int, b: int, slot: int = 0, bank: int = 3) -> bytes:
    addr = pad_record_address(record_index, slot, bank) + PAD_RGB_OFFSET
    return write_packet(addr, bytes((r, g, b)), PRESET_REGION)

def led_packet(record_index: int, led_note: int, slot: int = 0, bank: int = 3) -> bytes:
    addr = pad_record_address(record_index, slot, bank) + PAD_LED_OFFSET
    return write_packet(addr, bytes((led_note,)), PRESET_REGION)

def state_read_packet() -> bytes:
    return read_packet(0, 16, STATE_REGION)

VENDOR_HEADER_SIZE = 6   # 00 59 <cmd> <len LE×3>
PAYLOAD_PREFIX_SIZE = 8  # <region> <addr LE×4> <count LE×3>


def read_response_data(frame: bytes) -> bytes:
    """Достать полезные данные из ответа на команду чтения.

    Устройство отвечает полным вендорским фреймом, а не голыми данными:
    заголовок (6) + region/addr/count (8) + данные + чексумма (1).
    Скармливать `parse_state_block` весь фрейм нельзя — смещения поедут
    и вернётся правдоподобный мусор вместо ошибки.
    """
    if len(frame) < VENDOR_HEADER_SIZE + PAYLOAD_PREFIX_SIZE + 1:
        raise ValueError(f"ответ слишком короткий: {len(frame)} байт")
    if frame[0] != 0x00 or frame[1] != 0x59:
        raise ValueError(f"не вендорский фрейм: {frame[:2].hex(' ')}")
    if not verify_checksum(frame):
        raise ValueError("чексумма не сходится")

    count = int.from_bytes(frame[11:14], "little")
    data = frame[VENDOR_HEADER_SIZE + PAYLOAD_PREFIX_SIZE:-1]
    if len(data) != count:
        raise ValueError(f"объявлено {count} байт данных, пришло {len(data)}")
    return data


def parse_state_block(data: bytes) -> tuple[int, int]:
    if len(data) < 12:
        raise ValueError("State block too short")
    kk = data[6]
    ss = data[10]
    bb = data[11]
    slot = ss
    bank = 8 if bb == 1 else kk + 1
    bank = max(1, min(8, bank))
    return slot, bank

def verify_checksum(frame: bytes) -> bool:
    if len(frame) < 7:
        return False
    payload = frame[6:-1]
    checksum = frame[-1]
    return (sum(payload) + checksum) & 0xFF == 0xFF

def record_for_note(note: int) -> int | None:
    if PAD_NOTE_BASE <= note < PAD_NOTE_BASE + PAD_COUNT:
        return note - PAD_NOTE_BASE
    return None

def hex_to_rgb(s: str) -> tuple[int, int, int]:
    s = s.lstrip('#')
    if len(s) != 6:
        raise ValueError("Invalid hex color length")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)

def rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"

def scale_rgb(r: int, g: int, b: int, pct: int) -> tuple[int, int, int]:
    """Общая яркость — масштабирование компонент RGB.

    Отдельного канала яркости у железа нет: все три источника протокола
    сходятся на этом, подтверждено на устройстве лесенкой 255/120/48/12.
    """
    k = max(0, min(100, int(pct))) / 100.0
    return int(r * k), int(g * k), int(b * k)


def rgb_for_device(r: int, g: int, b: int) -> tuple[int, int, int]:
    """
    Identity function for now. This is a hook for future color correction because LED colors
    behind a diffuser don't match screen colors exactly, and the correction must be in one place.
    """
    return r, g, b

def parse_ble_midi(data: bytes) -> list[str]:
    """BLE MIDI packet parser per Apple/Bluetooth SIG spec."""
    result = []
    if len(data) < 3:
        return result
    i = 1
    running_status = None
    while i < len(data):
        b = data[i]
        if b & 0x80:
            if i + 1 < len(data) and (data[i+1] & 0x80):
                i += 1
                continue
            elif i + 1 < len(data) and not (data[i+1] & 0x80):
                running_status = b
                i += 1
                continue
            else:
                i += 1
                continue
        if running_status is None:
            i += 1
            continue
        msg_type = running_status & 0xF0
        if msg_type in (0x80, 0x90, 0xB0):
            if i + 1 < len(data) and not (data[i+1] & 0x80):
                d1, d2 = data[i], data[i+1]
                if msg_type == 0x90:
                    result.append(f"note:{d1}:{d2}")
                elif msg_type == 0x80:
                    result.append(f"note:{d1}:0")
                elif msg_type == 0xB0:
                    result.append(f"cc:{d1}:{d2}")
                i += 2
            else:
                i += 1
        else:
            i += 1
    return result
