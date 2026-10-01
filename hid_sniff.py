"""
Step 2: Read raw HID reports from Aula F75 (0x3554 0xFA09 — Compx 2.4G dongle).

Sniff all interfaces simultaneously to decode:
  - Keyboard reports    (usage_page=0x0001 usage=0x0006)
  - Mouse reports       (usage_page=0x0001 usage=0x0002)
  - Vendor commands     (usage_page=0xFF02, 0xFF04)
  - Media keys          (usage_page=0x000C)

Usage:
    python hid_sniff.py                        # sniff all ifaces
    python hid_sniff.py --usage_page 0xFF02    # vendor only
    python hid_sniff.py --duration 60
"""
import hid
import argparse
import time
import threading

VID = 0x3554
PID = 0xFA09

USAGE_PAGE_NAMES = {
    0x0001: "Generic Desktop",
    0x000C: "Consumer",
    0xFF02: "Vendor FF02",
    0xFF04: "Vendor FF04",
}

USAGE_NAMES = {
    (0x0001, 0x0002): "Mouse",
    (0x0001, 0x0006): "Keyboard",
    (0x0001, 0x0080): "System Control",
    (0x000C, 0x0001): "Consumer Control",
    (0xFF02, 0x0002): "Vendor FF02",
    (0xFF04, 0x0002): "Vendor FF04",
}

# HID Consumer Control usage codes (USB HID Usage Tables 1.4, page 0x000C)
CONSUMER_USAGES = {
    0x00B5: "ScanNextTrack",
    0x00B6: "ScanPrevTrack",
    0x00B7: "Stop",
    0x00CD: "PlayPause",
    0x00E2: "Mute",
    0x00E9: "VolumeUp",
    0x00EA: "VolumeDown",
    0x0183: "MediaSelect",
    0x018A: "Email",
    0x0192: "Calculator",
    0x0221: "BrowserSearch",
    0x0223: "BrowserHome",
    0x0224: "BrowserBack",
    0x0225: "BrowserForward",
    0x0226: "BrowserStop",
    0x0227: "BrowserRefresh",
}

def decode_consumer(data):
    """Parse consumer control report: [report_id, usage_lo, usage_hi, ...]"""
    if len(data) < 3:
        return ""
    usage = data[1] | (data[2] << 8)
    if usage == 0:
        return " (release)"
    name = CONSUMER_USAGES.get(usage, f"Usage=0x{usage:04X}")
    return f" → {name}"

def label(dev):
    up = dev['usage_page']
    u  = dev['usage']
    name = USAGE_NAMES.get((up, u), f"0x{up:04X}/0x{u:04X}")
    return f"iface={dev['interface_number']} [{name}]"

def sniff_one(dev, duration, filter_up=None):
    up = dev['usage_page']
    if filter_up is not None and up != filter_up:
        return
    lbl = label(dev)
    is_consumer = (up == 0x000C)
    is_vendor   = (up in (0xFF02, 0xFF04))
    try:
        h = hid.device()
        h.open_path(dev['path'])
        h.set_nonblocking(True)
        start = time.time()
        while time.time() - start < duration:
            data = h.read(64)
            if data:
                hexstr = ' '.join(f'{b:02X}' for b in data)
                ts = time.time() - start
                annotation = decode_consumer(data) if is_consumer else ""
                if is_vendor:
                    annotation = f" [cmd=0x{data[0]:02X} len={len(data)}]"
                print(f"[{ts:6.2f}s] {lbl:<35} {hexstr}{annotation}")
            time.sleep(0.001)
        h.close()
    except Exception as e:
        print(f"ERROR {lbl}: {e}")

def sniff_all(duration=30.0, filter_up=None):
    devices = hid.enumerate(VID, PID)
    if not devices:
        print(f"Device {VID:04X}:{PID:04X} not found.")
        return

    print(f"Aula F75 interfaces found: {len(devices)}")
    for d in devices:
        print(f"  {label(d)}")
    print(f"\nSniffing {duration}s... interact with keyboard/fn keys.\n")

    threads = []
    for dev in devices:
        t = threading.Thread(target=sniff_one, args=(dev, duration, filter_up), daemon=True)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--duration', type=float, default=30.0)
    parser.add_argument('--usage_page', type=lambda x: int(x, 16), default=None,
                        help="Filter by usage page, e.g. 0xFF02")
    args = parser.parse_args()

    sniff_all(args.duration, args.usage_page)
