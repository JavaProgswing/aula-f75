"""
Aula F75 Wireless Keyboard Protocol
Device: VID=0x3554 PID=0xFA09 (Compx 2.4G dongle)
Interface: HID FF02, Report ID 0x13, 19 data bytes

== Protocol Summary ==

Report format (all directions):
  Byte 0:    0x13 (HID Report ID)
  Byte 1:    Command byte
  Bytes 2-18: Payload (meaning depends on command)
  Byte 19:   Checksum = sum(bytes[0:19]) & 0xFF

Commands:
  READ keymap layer 1:  0x41  → streams 36 pages (cmd=0x41 in response)
  READ keymap layer 2:  0x42  → streams 27 pages
  READ keymap layer 3:  0x43  → streams 37 pages
  READ lighting config: 0x44  → streams 10 pages
  READ per-key RGB:     0x49  → streams 35 pages
  READ device info:     0x05  → single response

  WRITE keymap layer 1: 0x81  → write each page (echoes packet back as ack)
  COMMIT to flash:      0xFD  → saves RAM->flash, responds 01

  STATUS heartbeat:     0x0A  → spontaneous from keyboard, not a command
    Bytes: 13 0A 01 00 [fw_major] [fw_minor] [conn_state...] [checksum]

  CONTROL:
    0xFC  → respond 01 (possibly abort/reset)
    0xFE  → respond 01 (possibly begin session)
    0xFD  → respond 01 + commits write

== Page Format (keymap) ==

Each page packet:
  [0x13, cmd, total_pages, page_num, 0x0E, key0_hi, key0_lo, key1_hi, key1_lo, ..., checksum]
  - 7 keys per page × 2 bytes each (BIG-ENDIAN USB HID usage code)
  - 0x0000 = no key / empty slot
  - 36 pages × 7 keys = 252 slots (F75 has 75 physical keys, rest are 0x0000)

Key positions (page*7 + index):
  pos 1  = Esc       (0x0029)
  pos 3  = `         (0x0035)
  pos 5  = Tab       (0x002B)
  pos 7  = CapsLock  (0x0039)
  ...

== Verified Operations ==
  - Read keymap:   CONFIRMED working
  - Write keymap:  CONFIRMED (write all 36 pages via 0x81, then 0xFD to commit)
  - Key remapping: CONFIRMED (tested ESC->F13->ESC)
  - Per-key RGB:   read CONFIRMED, write TBD (likely 0x89 + 0xFD)

== FF04 Feature Report ==
  Report ID 0x06, 7 bytes
  Default: 06 00 02 64 64 64 A9 3E
  Byte 2: lighting mode?
  Bytes 3-5: brightness/speed/color params (0x64=100)
  Bytes 6-7: unknown (may be additional config)
"""

import hid
import time
import threading
from typing import Optional

VID = 0x3554
PID = 0xFA09

HID_KEYS = {
    0x00: "(none)",   0x04: "A",   0x05: "B",   0x06: "C",   0x07: "D",
    0x08: "E",   0x09: "F",   0x0A: "G",   0x0B: "H",   0x0C: "I",
    0x0D: "J",   0x0E: "K",   0x0F: "L",   0x10: "M",   0x11: "N",
    0x12: "O",   0x13: "P",   0x14: "Q",   0x15: "R",   0x16: "S",
    0x17: "T",   0x18: "U",   0x19: "V",   0x1A: "W",   0x1B: "X",
    0x1C: "Y",   0x1D: "Z",
    0x1E: "1",   0x1F: "2",   0x20: "3",   0x21: "4",   0x22: "5",
    0x23: "6",   0x24: "7",   0x25: "8",   0x26: "9",   0x27: "0",
    0x28: "Enter",  0x29: "Esc",   0x2A: "Backspace", 0x2B: "Tab",
    0x2C: "Space",  0x2D: "-",     0x2E: "=",     0x2F: "[",   0x30: "]",
    0x31: "\\",  0x33: ";",   0x34: "'",   0x35: "`",   0x36: ",",
    0x37: ".",   0x38: "/",   0x39: "CapsLk",
    0x3A: "F1",  0x3B: "F2",  0x3C: "F3",  0x3D: "F4",  0x3E: "F5",
    0x3F: "F6",  0x40: "F7",  0x41: "F8",  0x42: "F9",  0x43: "F10",
    0x44: "F11", 0x45: "F12", 0x68: "F13", 0x69: "F14",
    0x49: "Insert", 0x4A: "Home",  0x4B: "PgUp",  0x4C: "Del",
    0x4D: "End",    0x4E: "PgDn",
    0x4F: "Right",  0x50: "Left",  0x51: "Down",  0x52: "Up",
    0x65: "App",    0x64: "NonUS\\",
}

CMD_READ_KEYMAP   = 0x41
CMD_WRITE_KEYMAP  = 0x81
CMD_READ_KEYMAP2  = 0x42
CMD_READ_KEYMAP3  = 0x43
CMD_READ_LIGHTING = 0x44
CMD_READ_RGB      = 0x49
CMD_DEVICE_INFO   = 0x05
CMD_COMMIT        = 0xFD


def checksum(data: list[int]) -> int:
    return sum(data) & 0xFF


class AulaF75:
    def __init__(self):
        self._h_out: Optional[hid.device] = None
        self._h_in: Optional[hid.device] = None
        self._responses: list[list[int]] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._reader_thread: Optional[threading.Thread] = None

    def connect(self):
        """Open HID FF02 interface for read+write."""
        path = None
        for dev in hid.enumerate(VID, PID):
            if dev['usage_page'] == 0xFF02:
                path = dev['path']
                break
        if path is None:
            raise RuntimeError(f"Aula F75 not found (VID={VID:04X} PID={PID:04X})")

        self._h_out = hid.device()
        self._h_out.open_path(path)
        self._h_out.set_nonblocking(True)

        self._h_in = hid.device()
        self._h_in.open_path(path)
        self._h_in.set_nonblocking(True)

        self._stop.clear()
        self._reader_thread = threading.Thread(target=self._reader, daemon=True)
        self._reader_thread.start()
        time.sleep(0.05)

    def disconnect(self):
        self._stop.set()
        if self._reader_thread:
            self._reader_thread.join(timeout=1)
        if self._h_out:
            self._h_out.close()
        if self._h_in:
            self._h_in.close()

    def _reader(self):
        while not self._stop.is_set():
            data = self._h_in.read(64)
            if data:
                with self._lock:
                    self._responses.append(list(data))
            time.sleep(0.001)

    def _send(self, cmd: int, payload: list[int] = None) -> None:
        body = list(payload) if payload else []
        body = body[:18]
        body += [0x00] * (18 - len(body))
        pkt = [0x13, cmd] + body
        pkt.append(checksum(pkt))
        self._h_out.write(pkt[:20])

    def _drain(self, timeout: float = 1.0, filter_cmd: Optional[int] = None) -> list[list[int]]:
        """Collect responses until no new data for `timeout` seconds."""
        deadline = time.time() + timeout
        result = []
        while time.time() < deadline:
            with self._lock:
                new = self._responses[:]
                self._responses.clear()
            for p in new:
                if filter_cmd is None or p[1] == filter_cmd:
                    result.append(p)
                deadline = time.time() + 0.1  # extend deadline on new data
            time.sleep(0.01)
        return result

    def _send_raw(self, pkt20: list[int]) -> int:
        return self._h_out.write(pkt20[:20])

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def get_device_info(self) -> dict:
        """Read firmware version and connection status."""
        self._drain(0.05)  # flush
        self._send(CMD_DEVICE_INFO)
        pkts = self._drain(0.3, filter_cmd=CMD_DEVICE_INFO)
        if not pkts:
            return {}
        p = pkts[0]
        return {
            "fw_major": p[4],
            "fw_minor": p[5],
            "raw": p,
        }

    def read_keymap(self, layer: int = 1) -> list[list[int]]:
        """
        Read full keymap for layer 1/2/3.
        Returns list of 20-byte page packets sorted by page number.
        """
        cmd = {1: CMD_READ_KEYMAP, 2: CMD_READ_KEYMAP2, 3: CMD_READ_KEYMAP3}[layer]
        self._drain(0.05)
        self._send(cmd)
        pkts = self._drain(2.0, filter_cmd=cmd)
        return sorted(pkts, key=lambda p: p[3])

    def decode_keymap(self, pages: list[list[int]]) -> dict[int, int]:
        """
        Decode page packets into {position: hid_usage_code} dict.
        Position = page_num * 7 + key_index_in_page.
        """
        keymap = {}
        for p in pages:
            page_num = p[3]
            for i in range(7):
                usage = (p[5 + i*2] << 8) | p[5 + i*2 + 1]
                if usage != 0:
                    keymap[page_num * 7 + i] = usage
        return keymap

    def write_keymap(self, pages: list[list[int]]) -> int:
        """
        Write all keymap pages (must be complete set).
        Returns number of pages acknowledged.
        """
        acks = 0
        for page in pages:
            wp = list(page)
            wp[1] = CMD_WRITE_KEYMAP
            wp[19] = checksum(wp[:19])
            self._drain(0.02)
            self._send_raw(wp)
            time.sleep(0.05)
            r = self._drain(0.1, filter_cmd=CMD_WRITE_KEYMAP)
            if r:
                acks += 1
        return acks

    def commit(self) -> bool:
        """Commit RAM keymap to flash. Call after write_keymap()."""
        self._drain(0.05)
        self._send(CMD_COMMIT)
        r = self._drain(0.5)
        return any(p[1] == CMD_COMMIT for p in r)

    def remap_key(self, position: int, new_hid_usage: int, layer: int = 1) -> bool:
        """
        Remap a single key position to a new HID usage code.
        Uses read-modify-write on the full keymap.

        Example:
            kb.remap_key(1, 0x0068)   # ESC -> F13
            kb.remap_key(1, 0x0029)   # ESC -> ESC (restore)

        Returns True on success.
        """
        pages = self.read_keymap(layer)
        if not pages:
            return False

        target_page = position // 7
        target_idx  = position % 7

        for p in pages:
            if p[3] == target_page:
                byte_offset = 5 + target_idx * 2
                p[byte_offset]     = (new_hid_usage >> 8) & 0xFF
                p[byte_offset + 1] = new_hid_usage & 0xFF
                break
        else:
            return False

        acks = self.write_keymap(pages)
        if acks < len(pages):
            print(f"Warning: only {acks}/{len(pages)} pages acknowledged")

        return self.commit()

    def print_keymap(self, layer: int = 1):
        """Pretty-print the current keymap."""
        pages = self.read_keymap(layer)
        keymap = self.decode_keymap(pages)
        print(f"Layer {layer} keymap ({len(keymap)} mapped keys):")
        for pos, usage in sorted(keymap.items()):
            name = HID_KEYS.get(usage & 0xFF, f"0x{usage:04X}") if usage >> 8 == 0 else f"Page{usage>>8:02X}:{usage&0xFF:02X}"
            print(f"  [{pos:3d}] {name}")


# -------------------------------------------------------------------------
# CLI usage
# -------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    kb = AulaF75()
    kb.connect()

    try:
        if len(sys.argv) == 1:
            # Default: show keymap
            kb.print_keymap()

        elif sys.argv[1] == "info":
            info = kb.get_device_info()
            print(f"Firmware: v{info.get('fw_major', '?')}.{info.get('fw_minor', '?')}")

        elif sys.argv[1] == "remap" and len(sys.argv) == 4:
            # Usage: python protocol.py remap <position> <hid_usage_hex>
            pos   = int(sys.argv[2])
            usage = int(sys.argv[3], 16)
            name  = HID_KEYS.get(usage, f"0x{usage:04X}")
            print(f"Remapping position {pos} -> {name} (0x{usage:04X})...")
            ok = kb.remap_key(pos, usage)
            print("Success!" if ok else "Failed.")

        else:
            print("Usage:")
            print("  python protocol.py              # show keymap")
            print("  python protocol.py info         # firmware version")
            print("  python protocol.py remap <pos> <hid_hex>  # remap key")
            print("  python protocol.py remap 1 0x0029         # restore ESC")
            print("  python protocol.py remap 1 0x0068         # ESC->F13")
    finally:
        kb.disconnect()
