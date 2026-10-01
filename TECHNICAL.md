# Technical Deep-Dive: Aula F75 Protocol & Input Injection

---

## 1. USB HID Fundamentals

### What is HID?

HID (Human Interface Device) is a USB device class standardised in 1996. It defines a
generic protocol for input devices so the OS doesn't need a custom driver per device.
Every keyboard, mouse, gamepad, and joystick you plug into Windows uses it.

The protocol has three layers:

```
Application (your Python code)
     │
USB HID Class Driver (Windows: hid.sys, hidclass.sys)
     │
USB Host Controller Driver (xhci.sys / ehci.sys)
     │
Physical USB Bus
     │
HID Device (keyboard / dongle)
```

### Reports

All HID communication is in **Reports** — fixed-size byte arrays.

Three types:
- **Input Report**: device → host (key presses, mouse movement)
- **Output Report**: host → device (LEDs, force feedback)
- **Feature Report**: host ↔ device (configuration, via control pipe / EP0)

The **Report Descriptor** (stored in device ROM) tells the OS exactly what each
byte in each report means. The OS reads this once at enumeration and never again.

---

## 2. The Aula F75 Dongle: Composite HID

The wireless dongle `VID=0x3554 PID=0xFA09` (manufactured by Compx) is a
**Composite HID Device** — one USB device that presents multiple logical HID
interfaces simultaneously. Windows assigns a separate driver instance per interface.

```
USB Device: Compx 2.4G Wireless Receiver
├── Interface 0
│   ├── Usage Page 0x0001 (Generic Desktop)
│   └── Usage 0x0006 → Standard Keyboard
│       Driver: kbdhid.sys + kbdclass.sys
│
└── Interface 1 (multiple logical collections)
    ├── Usage Page 0x0001, Usage 0x0002 → Mouse
    │   Driver: mouhid.sys + mouclass.sys
    │
    ├── Usage Page 0x0001, Usage 0x0080 → System Control (power/sleep)
    ├── Usage Page 0x000C, Usage 0x0001 → Consumer Control (media keys)
    │
    ├── Usage Page 0xFF02, Usage 0x0002 → Vendor FF02 (custom protocol)
    │   Driver: hid.sys (generic — no vendor driver needed)
    │
    └── Usage Page 0xFF04, Usage 0x0002 → Vendor FF04 (Feature Report only)
        Driver: hid.sys (generic)
```

The keyboard firmware runs entirely inside the keyboard. The dongle is a
transparent relay: it receives 2.4GHz proprietary RF packets from the keyboard
and re-emits them as USB HID reports to the host, and vice versa.

---

## 3. The FF02 Vendor Protocol (What We Reverse-Engineered)

### Report Descriptor (Raw)

```
FF02 (36 bytes):
  06 02 FF    Usage Page (0xFF02)
  09 02       Usage (0x02)
  A1 01       Collection (Application)
    85 13     Report ID = 0x13
    09 02     Usage (0x02)
    15 00     Logical Min (0)
    26 FF 00  Logical Max (255)
    75 08     Report Size (8 bits)
    95 13     Report Count (19)   ← 19 bytes of data
    81 00     INPUT  report       ← keyboard → host
    09 02     Usage (0x02)
    15 00     Logical Min (0)
    26 FF 00  Logical Max (255)
    75 08     Report Size (8 bits)
    95 13     Report Count (19)   ← 19 bytes of data
    91 00     OUTPUT report       ← host → keyboard
  C0          End Collection
```

Both INPUT and OUTPUT reports: 1 byte Report ID (0x13) + 19 bytes data = 20 bytes.

FF04 (Feature Report only, 7 bytes):
```
  85 06  Report ID = 0x06
  95 07  Report Count (7)
  B1 02  FEATURE
```

### Packet Format

```
Byte  0    Report ID = 0x13 (always)
Byte  1    Command byte
Bytes 2-18 Payload (meaning depends on command)
Byte 19    Checksum = sum(bytes[0..18]) & 0xFF
```

### Why Only FF02 Works

- **Keyboard interface**: claimed by `kbdhid.sys` with exclusive access. `hidapi` gets
  `ERROR_ACCESS_DENIED` on open, or `write()` returns -1 because there is no OUT
  endpoint (interrupt IN only).
- **Mouse interface**: same — `mouhid.sys` exclusive. No OUT endpoint.
- **FF04**: Feature Report only (no OUT endpoint). `send_feature_report()` succeeds
  structurally but the device ignores it (no write path in firmware for this).
- **FF02**: has both INPUT and OUTPUT endpoints. `hid.sys` (generic) opens it without
  exclusive lock. We can read AND write.

### Command Map

| CMD  | Direction       | Function                          | Response         |
|------|-----------------|-----------------------------------|------------------|
| 0x05 | OUT → IN        | Query device info                 | Single packet    |
| 0x41 | OUT → stream IN | Read keymap layer 1               | 36 pages         |
| 0x42 | OUT → stream IN | Read keymap layer 2 (Fn)          | 27 pages         |
| 0x43 | OUT → stream IN | Read keymap layer 3               | 37 pages         |
| 0x44 | OUT → stream IN | Read lighting/RGB config          | 10 pages         |
| 0x49 | OUT → stream IN | Read per-key RGB                  | 35 pages         |
| 0x81 | OUT → echo IN   | Write keymap page                 | Echoes packet    |
| 0xFD | OUT → ack IN    | Commit RAM → flash                | `01 00 ...`      |
| 0x0A | Spontaneous IN  | Heartbeat from keyboard           | Unprompted       |
| 0xFC | OUT → ack IN    | Unknown (abort/reset?)            | `01 00 ...`      |
| 0xFE | OUT → ack IN    | Unknown (begin session?)          | `01 00 ...`      |

### Heartbeat Packet (0x0A)

Sent spontaneously by keyboard when keys are pressed:

```
13 0A 01 00 04 07 00 00 00 00 00 00 00 00 00 00 00 00 00 29
     ^     ^  ^  ^
     │     │  │  └── fw minor = 7  → firmware v10.7... or v4.7
     │     │  └───── fw major = 4  (byte 4) or 10 (0x0A = byte 1?)
     │     └──────── conn type = 1 (2.4GHz)
     └────────────── cmd = 0x0A
```

Checksum: 0x13+0x0A+0x01+0x04+0x07 = 0x29 ✓

### Keymap Page Format

```
Byte 0:  0x13          Report ID
Byte 1:  0x41          Command (read) or 0x81 (write)
Byte 2:  0x24          Total pages (36 = full keymap)
Byte 3:  0x00-0x23     Page number
Byte 4:  0x0E          Data bytes in this page (always 14)
Bytes 5-18: 7 keys × 2 bytes each (BIG-ENDIAN USB HID Usage Code)
Byte 19: checksum
```

7 keys × 36 pages = 252 slots. F75 has 75 physical keys — remaining slots are 0x0000.

Key position number = `page_num × 7 + index_in_page`

### Write Flow (verified)

```python
# 1. Read all 36 pages
pages = send(0x41) → wait for 36 INPUT packets

# 2. Modify desired key
pages[target_page][7] = new_usage_hi
pages[target_page][8] = new_usage_lo

# 3. Write all 36 pages via 0x81 (keyboard echoes each back)
for page in pages:
    page[1] = 0x81
    page[19] = checksum(page[:19])
    write(page)        # keyboard echoes identical packet as ACK

# 4. Commit to flash
send(0xFD) → responds 01 00 00...
```

The keyboard maintains two keymap stores:
- **RAM**: updated immediately by 0x81 writes (volatile, lost on power cycle)
- **Flash**: updated by 0xFD commit (persistent across reboots)

---

## 4. The 2.4GHz Wireless Layer

The dongle and keyboard communicate over a proprietary 2.4GHz RF protocol (not
Bluetooth, not WiFi). Compx (the chip manufacturer) uses a custom GFSK-modulated
protocol with frequency hopping to avoid interference.

```
Keyboard MCU (likely STM32 or Nordic nRF52)
     │  2.4GHz proprietary RF
     ▼
Dongle MCU (Compx chip)
     │  USB HID
     ▼
Windows
```

When we send an OUTPUT report to FF02, the dongle:
1. Receives it via USB
2. Translates it to an RF command packet
3. Broadcasts it to the paired keyboard
4. Keyboard processes it, updates RAM
5. Keyboard sends back an RF response
6. Dongle translates to USB HID INPUT report
7. We read it from FF02

Latency end-to-end: ~5-15ms per exchange (RF round-trip + USB polling interval).

---

## 5. Windows Input Stack Architecture

### How Real HID Input Flows

```
Physical key press
     │
Keyboard firmware → USB HID Input Report
     │
kbdhid.sys (converts HID report to Windows key event)
     │
kbdclass.sys (class driver, queues key events)
     │
win32k.sys (kernel mode GUI subsystem)
     │
Raw Input (GetRawInput) + Message Queue (WM_KEYDOWN)
     │
Application
```

### How SendInput() Flows

```python
ctypes.windll.user32.SendInput(1, byref(inp), sizeof(INPUT))
```

```
user32.dll SendInput()
     │
win32k.sys NtUserSendInput() [kernel mode]
     │  ← injects directly here, bypassing kbdhid/mouhid entirely
     │
Same queue as real hardware input
     │
Application (WM_MOUSEMOVE, WM_LBUTTONDOWN, GetCursorPos, etc.)
```

SendInput bypasses the entire HID layer. It writes events directly into the
kernel's input queue in `win32k.sys`. To the application reading `WM_MOUSEMOVE`,
the events look identical to hardware — same data, same timing.

However, Windows marks injected events with a flag:

```c
// In MSLLHOOKSTRUCT (low-level mouse hook):
DWORD flags;
// bit 0: LLMHF_INJECTED = 1 if SendInput/mouse_event
// bit 1: LLMHF_LOWER_IL_INJECTED = 1 if injected from lower integrity level
```

This flag is visible to any process that installs a `WH_MOUSE_LL` hook.

---

## 6. Anti-Cheat Detection Analysis

### What Anti-Cheats Do

Modern anti-cheats (Vanguard, EAC, BattlEye) run kernel-mode drivers
(ring 0, same privilege as the OS kernel). They can inspect everything.

```
Ring 3 (User mode): Game, anti-cheat UI, your scripts
Ring 0 (Kernel mode): Vanguard driver, EAC driver, Windows drivers, hid.sys
```

### Method 1: SendInput (what mouse.py uses)

**Detection level: HIGH — all major anti-cheats detect this.**

Three detection vectors:

**A. Low-level hook flag**
Anti-cheat installs `WH_MOUSE_LL` / `WH_KEYBOARD_LL` global hooks.
Every mouse/keyboard event passes through them. The `MSLLHOOKSTRUCT.flags`
field has `LLMHF_INJECTED = 1` for any `SendInput()` call.

```c
LRESULT CALLBACK LowLevelMouseProc(int nCode, WPARAM wParam, LPARAM lParam) {
    MSLLHOOKSTRUCT* p = (MSLLHOOKSTRUCT*)lParam;
    if (p->flags & LLMHF_INJECTED) {
        // This input was synthesized, not from hardware
        ReportCheat();
    }
}
```

**B. dwExtraInfo = 0**
Real hardware inputs carry a non-zero `dwExtraInfo` value set by the HID driver.
`SendInput()` sends `dwExtraInfo = 0` (or whatever you set, default 0). Anti-cheats
check this field.

**C. Kernel-level IRP inspection**
Vanguard (ring 0) can hook `IRP_MJ_READ` on `mouclass.sys`. Real hardware inputs
arrive as IRPs from `mouhid.sys`. `SendInput()` arrives via `NtUserSendInput()`
system call — entirely different code path. Distinguishable at kernel level.

**Verdict: SendInput is detectable. Do not use in anti-cheat protected games.**

---

### Method 2: Keymap Remap (what protocol.py does)

**Detection level: ZERO — this is hardware-level firmware modification.**

When you use `protocol.py remap 1 0x0068` to map ESC→F13:

```
You physically press ESC key
     │
Keyboard matrix detects keypress
     │
Keyboard firmware looks up remapped keycode (F13 = 0x68) from flash
     │
Sends HID Input Report with 0x68 over 2.4GHz
     │
Dongle converts to USB HID Input Report
     │
mouhid.sys / kbdhid.sys processes it as normal hardware input
     │
LLMHF_INJECTED = 0 (real hardware), dwExtraInfo = set by kbdhid.sys
     │
Game sees F13 keypress — indistinguishable from typing on any keyboard
```

The remapping happens IN the keyboard's flash memory. No software runs during
gameplay. No hooks, no drivers, no API calls. The OS and anti-cheat see a
completely standard HID keyboard.

**Anti-cheat cannot distinguish this from a factory keyboard.**

**Limitation**: you still have to physically press the key. This is remapping,
not automation.

---

### Method 3: Direct HID Write to Mouse Interface (NOT implemented — blocked)

If someone replaced `mouhid.sys` with a custom driver (via Zadig/WinUSB) and
wrote directly to the mouse HID endpoint:

**Detection level: MEDIUM**

- The HID Input Report would come from the correct hardware path
- `LLMHF_INJECTED = 0` (real HID path, not SendInput)
- BUT: Vanguard / EAC scan loaded drivers at ring 0. A non-Microsoft-signed
  driver replacing `mouhid.sys` would be flagged immediately.
- Also: the dongle's mouse endpoint only emits valid mouse packets when triggered
  by actual keyboard matrix events — there's no "inject arbitrary mouse movement
  via the wireless dongle" command in the protocol we found.

---

### Comparison Table

| Method | LLMHF_INJECTED | Kernel path | Driver scan | Detectable? |
|---|---|---|---|---|
| `SendInput()` | **= 1** | NtUserSendInput | N/A | **YES** |
| Direct HID write (no driver swap) | N/A | Blocked | N/A | Blocked |
| Direct HID write (custom driver) | = 0 | mouhid path | **FLAGGED** | **YES** |
| Keymap remap (protocol.py) | = 0 | Real hardware | None | **NO** |
| Physical human pressing keys | = 0 | Real hardware | None | No |

---

### Specific Anti-Cheats

**Riot Vanguard**
- Ring 0 kernel driver, loads at Windows boot (not just game launch)
- Hooks `NtUserSendInput` at SSDT level — detects all SendInput calls
- Monitors driver load list — flags unsigned or suspicious drivers
- Has hardware ID fingerprinting — tracks VID/PID of input devices
- **Will detect**: SendInput, custom HID drivers
- **Will NOT detect**: keymap remap via FF02 (looks like normal keyboard)

**Easy Anti-Cheat (EAC)**
- Kernel driver loaded at game launch
- `WH_MOUSE_LL` hook + `LLMHF_INJECTED` check
- Monitors process memory for injection patterns
- **Will detect**: SendInput (via hook flag)
- **Will NOT detect**: keymap remap

**BattlEye**
- Kernel driver
- Behavioral analysis (inputs that are too precise, too fast, or inhuman patterns)
- `LLMHF_INJECTED` check
- **Will detect**: SendInput
- **Behavioral**: even real HID inputs that are pixel-perfect or millisecond-perfect
  may trigger behavioral flags regardless of injection method

**VAC (Valve Anti-Cheat)**
- User mode only (no kernel driver) — weakest of the major ACs
- Scans loaded modules and memory signatures
- Does NOT hook input at kernel level
- **Will detect**: SendInput hook flag (if scanning hooks)
- Less aggressive than Vanguard/EAC

---

## 7. What This Project Actually Is

**This is keyboard firmware access, not cheat software.**

What we built:
- A reverse-engineered protocol library to read/write keyboard configuration
- Standard key remapping (the same thing Aula's official software does, just
  without the official app)
- A mouse input library using standard Windows APIs

The keymap writer (`protocol.py`) is equivalent to what keyboard firmware
configurators like QMK, VIA, and Vial do. It modifies the keyboard's stored
keymap — a standard, well-understood operation.

The mouse injector (`mouse.py`) uses `SendInput`, which is the same API used by
Windows accessibility tools, AutoHotkey, and remote desktop software.

---

## 8. Summary

```
What we reverse-engineered:
  FF02 vendor HID interface on 0x3554:0xFA09 dongle
  20-byte packet format with 0x13 report ID
  0x41 read / 0x81 write / 0xFD commit keymap protocol
  Checksum = sum(bytes[0:19]) & 0xFF

What works hardware-level (anti-cheat transparent):
  Keymap remapping via protocol.py → edits keyboard flash directly
  No software involved during actual key press

What is software-level (anti-cheat detectable):
  mouse.py → uses SendInput() → LLMHF_INJECTED = 1
  All major anti-cheats (Vanguard, EAC, BattlEye) check this flag
```
