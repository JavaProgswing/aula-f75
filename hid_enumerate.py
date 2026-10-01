"""
Step 1: Find Aula F75 VID/PID and enumerate its HID interfaces.
Run this with keyboard plugged in.
"""
import hid

def enumerate_devices():
    print(f"{'VID':>6} {'PID':>6}  {'Manufacturer':<20} {'Product':<30} {'Interface'}")
    print("-" * 80)
    for device in hid.enumerate():
        print(
            f"0x{device['vendor_id']:04X} 0x{device['product_id']:04X}  "
            f"{device['manufacturer_string']:<20} "
            f"{device['product_string']:<30} "
            f"iface={device['interface_number']}  "
            f"usage_page=0x{device['usage_page']:04X}  usage=0x{device['usage']:04X}"
        )

def find_aula():
    """Filter for likely Aula devices."""
    results = []
    for device in hid.enumerate():
        mfr = device['manufacturer_string'].lower()
        prod = device['product_string'].lower()
        if 'aula' in mfr or 'aula' in prod or 'f75' in prod:
            results.append(device)
    return results

if __name__ == "__main__":
    print("=== All HID Devices ===")
    enumerate_devices()

    print("\n=== Aula Devices ===")
    aula = find_aula()
    if aula:
        for d in aula:
            print(d)
    else:
        print("No Aula device found. Check VID/PID manually above.")
        print("Hint: Aula common VID = 0x1EA7 or 0x258A")
