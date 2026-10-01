# Aula F75 Reverse Engineering

Reverse-engineered USB HID protocol for the Aula F75 75% wireless keyboard.
Full keymap read/write and mouse input injection — no official SDK needed.

## Hardware

| Field | Value |
|---|---|
| Device | Aula F75 (2.4GHz wireless) |
| Dongle | Compx 2.4G Receiver |
| VID:PID | `0x3554:0xFA09` |
| HID Interface | FF02 (usage_page=0xFF02), Report ID 0x13, 19 data bytes |
| Firmware | v10.3 |

The dongle enumerates as a **composite HID device**: keyboard + mouse + consumer
control + vendor FF02/FF04. All custom communication goes through FF02.

## Files

| File | Purpose |
|---|---|
| `protocol.py` | Full protocol implementation — read/write keymap, device info |
| `mouse.py` | Mouse input injection via Windows SendInput |
| `hid_enumerate.py` | List all HID devices, identify the dongle |
| `hid_sniff.py` | Sniff live HID reports from all dongle interfaces |
| `requirements.txt` | Python dependencies |

## Protocol

### Report format
All packets are 20 bytes: `[Report_ID=0x13] [CMD] [18 bytes payload] [checksum]`
Checksum = `sum(bytes[0:19]) & 0xFF`

### Commands

| CMD (hex) | Direction | Function |
|---|---|---|
| `0x05` | host→kb, kb responds | Device info (firmware version) |
| `0x41` | host→kb, kb streams | Read keymap layer 1 (36 pages) |
| `0x42` | host→kb, kb streams | Read keymap layer 2 / Fn layer |
| `0x43` | host→kb, kb streams | Read keymap layer 3 |
| `0x44` | host→kb, kb streams | Read lighting config |
| `0x49` | host→kb, kb streams | Read per-key RGB (35 pages) |
| `0x81` | host→kb, kb echoes | Write keymap page (per-page ack) |
| `0xFD` | host→kb, kb acks | Commit RAM → flash |
| `0x0A` | kb→host spontaneous | Heartbeat / battery / connection status |

### Keymap page format
```
Byte 0:  0x13  (report ID)
Byte 1:  0x41  (command)
Byte 2:  total pages (e.g. 0x24 = 36)
Byte 3:  page number (0x00..0x23)
Byte 4:  0x0E  (data bytes in this page = 14)
Bytes 5-18: 7 keys × 2 bytes each, BIG-ENDIAN USB HID usage code
Byte 19: checksum
```

Key position = `page_number × 7 + index_in_page`

### Write flow
```
1. Read all 36 pages via 0x41
2. Modify desired key bytes
3. Write all 36 pages via 0x81 (keyboard echoes each as ack)
4. Send 0xFD to commit to flash
```

### Mouse HID
The dongle exposes a Mouse HID interface (`usage=0x0002`). Windows `mouhid.sys`
claims it exclusively — direct `HID.write()` is blocked (`write() = -1`).
`mouse.py` uses Windows `SendInput()` instead, which feeds the same kernel input
stack. The injected events are hardware-indistinguishable from the dongle's own
mouse reports.

## Setup

```bash
pip install hidapi pynput
```

## Usage

### Keymap

```bash
# Print full keymap
python protocol.py

# Firmware version
python protocol.py info

# Remap a key (position, HID usage hex)
python protocol.py remap 1 0x0052    # ESC -> Up arrow
python protocol.py remap 1 0x0029    # restore ESC

# Python API
from protocol import AulaF75
kb = AulaF75()
kb.connect()
kb.print_keymap()
kb.remap_key(1, 0x0068)   # ESC -> F13
kb.remap_key(1, 0x0029)   # restore
kb.disconnect()
```

### Mouse injection

```bash
python mouse.py move 100 50          # relative move +100x +50y
python mouse.py moveto 960 540       # absolute move (center of 1920x1080)
python mouse.py smooth 300 0         # smooth glide right
python mouse.py click left           # left click
python mouse.py click right          # right click
python mouse.py dclick               # double-click
python mouse.py drag 200 100         # left-drag
python mouse.py scroll 3             # scroll up 3 ticks
python mouse.py pos                  # print current cursor position
python mouse.py demo                 # run a movement demo

# Python API
from mouse import MouseController
m = MouseController()
m.move(50, 0)
m.click_left()
m.move_smooth(200, 100, steps=30, delay=0.008)
m.drag(-150, 0)
m.scroll(-2)
x, y = m.get_pos()
```

### Sniff HID traffic

```bash
# All interfaces (keyboard, mouse, vendor, consumer)
python hid_sniff.py --duration 30

# Vendor commands only (RGB/config protocol)
python hid_sniff.py --usage_page 0xFF02 --duration 30
```

## Key position map (partial)

```
pos   1 = Esc        pos   3 = `        pos   5 = Tab
pos   7 = CapsLock   pos  15 = 1        pos  17 = Q
pos  19 = A          pos  21 = Z        pos  27 = 2
pos  29 = W          pos  31 = S        pos  33 = X
pos  39 = 3          pos  41 = E        pos  43 = D
pos  45 = C          pos  51 = 4        pos  53 = R
pos  55 = F          pos  57 = V        pos  63 = 5
pos  65 = T          pos  67 = G        pos  69 = B
pos  71 = Space      pos  75 = 6        pos  77 = Y
pos  79 = H          pos  81 = N        pos  87 = 7
pos  89 = U          pos  91 = J        pos  93 = M
pos  99 = 8          pos 101 = I        pos 103 = K
pos 109 = 9          pos 111 = 0        pos 113 = O
pos 115 = L          pos 121 = F9       pos 123 = 0
pos 127 = ;          pos 129 = /        pos 133 = F10
pos 135 = -          pos 137 = [        pos 139 = '
pos 145 = F11        pos 147 = =        pos 149 = ]
pos 155 = Left       pos 157 = F12      pos 159 = Backspace
pos 161 = \          pos 163 = Enter    pos 165 = Up
pos 167 = Down       pos 171 = Del      pos 173 = PgUp
pos 175 = PgDn       pos 177 = End      pos 179 = Right
```

Run `python protocol.py` for the complete current mapping.

## Common HID usage codes

```
0x0029 = Esc        0x002B = Tab      0x002C = Space
0x0028 = Enter      0x002A = Backspace
0x003A = F1  ...    0x0045 = F12      0x0068 = F13
0x004F = Right      0x0050 = Left     0x0051 = Down   0x0052 = Up
0x004C = Delete     0x004A = Home     0x004D = End
0x004B = PgUp       0x004E = PgDn
```

Full USB HID usage tables: https://usb.org/sites/default/files/hut1_4.pdf (section 10)

## TODO

- [ ] Decode per-key RGB write (`0x89` + `0xFD` — analogous to keymap write)
- [ ] Decode `0x44` lighting config fields (mode, brightness, speed, color)
- [ ] Map all 90 key positions to physical F75 layout diagram
- [ ] Decode layer 2/3 (Fn layer) remapping
