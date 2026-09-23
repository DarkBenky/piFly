#!/usr/bin/env python3
"""Visual test for the piFly OLED (SSD1306 0.96" 128x64, I2C).

Run on the Pi:
    python3 modules/tests/test_display.py             # press Enter between steps
    python3 modules/tests/test_display.py --no-wait   # auto-advance, just watch

Each step prints what the screen should show before drawing it, so you can
tell quickly whether the panel works, has the right orientation and renders
text correctly.  Ctrl+C aborts; the panel keeps its last frame either way.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from modules.display import DisplayUnavailable, find_displays, open_display  # noqa: E402


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bus", type=int, default=None,
                        help="I2C bus (default: auto-detect)")
    parser.add_argument("--address", type=lambda v: int(v, 0), default=None,
                        help="I2C address, e.g. 0x3C (default: auto-detect)")
    parser.add_argument("--rotate", action="store_true",
                        help="flip the panel 180 degrees")
    parser.add_argument("--contrast", type=lambda v: int(v, 0), default=0xCF)
    parser.add_argument("--no-wait", action="store_true",
                        help="auto-advance instead of waiting for Enter")
    parser.add_argument("--step-seconds", type=float, default=2.0,
                        help="pause per step with --no-wait (default 2.0)")
    parser.add_argument("--live-seconds", type=float, default=6.0,
                        help="duration of the live clock step (default 6.0)")
    parser.add_argument("--clear", action="store_true",
                        help="blank the screen on exit (default: leave final frame)")
    return parser.parse_args(argv)


def detect():
    print("probing I2C buses for an SSD1306 panel (addresses 0x3C / 0x3D) ...")
    try:
        hits = find_displays()
    except DisplayUnavailable as exc:
        print(f"  I2C unavailable: {exc}")
        return []
    for bus, addr in hits:
        print(f"  found 0x{addr:02X} on i2c-{bus}")
    if not hits:
        print("  nothing found - check VCC/GND and SDA=pin3 / SCL=pin5 wiring,")
        print("  then verify `ls /dev/i2c-1` exists on the Pi")
    return hits


# -- drawing steps -----------------------------------------------------------

def draw_white(disp):
    disp.fill_rect(0, 0, disp.width - 1, disp.height - 1)
    disp.show()


def draw_corners(disp):
    disp.clear()
    disp.line(0, 0, disp.width - 1, disp.height - 1)
    disp.rect(0, 0, disp.width - 1, disp.height - 1)
    disp.fill_rect(2, 2, 11, 11)                              # top-left marker
    disp.rect(2, 2, 11, 11, 0)
    disp.text("TL", 15, 3, size=10)
    disp.fill_rect(disp.width - 12, disp.height - 12,         # bottom-right marker
                   disp.width - 3, disp.height - 3)
    disp.text("BR", disp.width - 30, disp.height - 12, size=10)
    disp.line(58, 31, 70, 31)                                 # centre cross
    disp.line(64, 25, 64, 37)
    disp.show()


def draw_text(disp):
    disp.clear()
    title = "piFly"
    width, _ = disp.text_size(title, size=24)
    disp.text(title, (disp.width - width) // 2, 0, size=24)
    lines = ('0.96" OLED 128x64', "SSD1306 over I2C", "text render OK")
    for index, line in enumerate(lines):
        width, _ = disp.text_size(line, size=10)
        disp.text(line, (disp.width - width) // 2, 30 + index * 11, size=10)
    disp.show()


def draw_animate(disp, speed=0.02):
    positions = list(range(0, disp.width - 16 + 1, 4))
    positions += list(range(disp.width - 16, -1, -4))
    for x in positions:
        disp.clear()
        disp.fill_rect(x, 26, x + 15, 41)
        disp.show()
        time.sleep(speed)


def draw_live(disp, seconds):
    try:
        from modules.temperature import cpu_temp
    except Exception:                                   # sensor stack not importable
        cpu_temp = None
    deadline = time.time() + seconds
    while time.time() < deadline:
        disp.clear()
        clock = time.strftime("%H:%M:%S")
        width, _ = disp.text_size(clock, size=20)
        disp.text(clock, (disp.width - width) // 2, 0, size=20)
        details = []
        if cpu_temp is not None:
            try:
                details.append(f"cpu {cpu_temp():.1f} C")
            except Exception:
                pass
        try:
            with open("/proc/uptime") as handle:
                up = float(handle.read().split()[0])
            details.append(f"up {int(up // 3600)}h{int(up % 3600 // 60):02d}m")
        except OSError:
            pass
        disp.text("   ".join(details), 2, 28, size=10)
        disp.text(f"i2c-{disp.bus} @ 0x{disp.address:02X}", 2, 42, size=10)
        disp.text(time.strftime("%Y-%m-%d"), 2, 54, size=10)
        disp.show()
        time.sleep(0.5)


def draw_final(disp):
    disp.clear()
    for index, (text, size) in enumerate((("display OK", 16),
                                          ('0.96" SSD1306', 10),
                                          (f"i2c-{disp.bus} @ 0x{disp.address:02X}", 10))):
        width, _ = disp.text_size(text, size=size)
        y = 8 + index * 17 if index == 0 else 34 + (index - 1) * 12
        disp.text(text, (disp.width - width) // 2, y, size=size)
    disp.show()


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)

    hits = detect()
    if not hits and args.bus is None:
        return 1

    bus, address = hits[0] if hits else (args.bus, args.address)
    if args.bus is not None:
        bus = args.bus
    if args.address is not None:
        address = args.address

    print(f"opening 0x{address:02X} on i2c-{bus} ...")
    try:
        disp = open_display(bus=bus, address=address,
                            rotate=args.rotate, contrast=args.contrast)
    except DisplayUnavailable as exc:
        print(f"  open failed: {exc}")
        print("  the panel did not answer - double-check wiring and power")
        return 1
    print("  ok\n")

    step = 0
    total = 6

    def showing(title, expected, draw):
        nonlocal step
        step += 1
        print(f"[{step}/{total}] {title}")
        print(f"      expect on screen: {expected}")
        draw(disp)
        if args.no_wait:
            time.sleep(args.step_seconds)
        else:
            try:
                input("      press Enter to continue ")
            except EOFError:
                pass

    try:
        showing("blank", "all pixels off - a dark screen",
                lambda d: (d.clear(), d.show()))
        showing("white", "a uniformly bright white screen", draw_white)
        showing("corners", "border, TL square top-left, BR square bottom-right, "
                           "diagonal line TL -> BR, cross at the centre", draw_corners)
        showing("text", "big 'piFly' title plus three small centred lines, "
                        "nothing clipped at the edges", draw_text)
        showing("animate", "a small box sweeping left -> right and back", draw_animate)
        showing("live", "clock + CPU temp + uptime updating twice a second",
                lambda d: draw_live(d, args.live_seconds))
    except KeyboardInterrupt:
        print("\naborted (Ctrl+C)")
        return 130
    finally:
        try:
            if args.clear:
                disp.clear()
                disp.show()
                print("\nscreen cleared.")
            else:
                draw_final(disp)
                print("\nleft the final frame on screen (use --clear to blank it instead).")
        finally:
            disp.close()

    print("display test finished without I2C errors.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
