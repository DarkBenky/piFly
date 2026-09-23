import math
import time

WIDTH = 128
HEIGHT = 64
DEFAULT_BUS = 1
ADDRESSES = (0x3C, 0x3D)
BUS_CANDIDATES = (1, 5, 0)
_MAX_CHUNK = 512

_CTRL_CMD = 0x00
_CTRL_DATA = 0x40

_FONT_PATHS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
)


class DisplayUnavailable(RuntimeError):
    pass


class _Smbus2:

    def __init__(self, bus):
        self._bus = bus

    def write(self, address, payload):
        from smbus2 import i2c_msg
        self._bus.i2c_rdwr(i2c_msg.write(address, payload))

    def probe(self, address):
        try:
            self._bus.read_byte(address)
            return True
        except OSError:
            pass
        try:
            self._bus.write_quick(address)
            return True
        except OSError:
            return False

    def close(self):
        try:
            self._bus.close()
        except Exception:
            pass


class _PureIo:

    def __init__(self, bus):
        self._bus = bus

    def write(self, address, payload):
        self._bus.write_bytes(address, payload)

    def probe(self, address):
        try:
            self._bus.read_byte(address)
            return True
        except OSError:
            pass
        try:
            self._bus.write_quick(address)
            return True
        except OSError:
            return False

    def close(self):
        try:
            self._bus.close()
        except Exception:
            pass


def _open_bus(bus):
    try:
        from smbus2 import SMBus
    except ImportError:
        SMBus = None
    if SMBus is not None:
        try:
            return _Smbus2(SMBus(bus))
        except OSError as exc:
            raise DisplayUnavailable(f"cannot open /dev/i2c-{bus}: {exc}") from exc
    try:
        from Adafruit_PureIO import smbus as pureio
    except ImportError as exc:
        raise DisplayUnavailable("I2C needs the smbus2 or Adafruit_PureIO package") from exc
    try:
        return _PureIo(pureio.SMBus(bus))
    except OSError as exc:
        raise DisplayUnavailable(f"cannot open /dev/i2c-{bus}: {exc}") from exc


class SSD1306:

    def __init__(self, bus=DEFAULT_BUS, address=None, width=WIDTH, height=HEIGHT,
                 rotate=False, contrast=0xCF, addresses=ADDRESSES):
        if (width, height) not in ((128, 64), (128, 32)):
            raise ValueError("unsupported panel size, expected 128x64 or 128x32")
        self.width = width
        self.height = height
        self.bus = bus
        self.rotate = rotate
        self._buf = bytearray(width * height // 8)
        self._fonts = {}
        self._i2c = _open_bus(bus)
        try:
            self.address = address if address is not None else self._find(addresses)
            if self.address is None:
                raise DisplayUnavailable(
                    f"no SSD1306 answered on i2c-{bus} "
                    f"(tried {[hex(a) for a in addresses]})")
            self._setup(rotate, contrast)
        except Exception:
            self._i2c.close()
            raise

    def _find(self, addresses):
        for addr in addresses:
            if self._i2c.probe(addr):
                return addr
        return None

    def _command(self, *values):
        self._i2c.write(self.address, bytes((_CTRL_CMD,)) + bytes(values))

    def _write(self, payload):
        for offset in range(0, len(payload), _MAX_CHUNK):
            chunk = bytes(payload[offset:offset + _MAX_CHUNK])
            self._i2c.write(self.address, bytes((_CTRL_DATA,)) + chunk)

    def _setup(self, rotate, contrast):
        mux = 0x3F if self.height == 64 else 0x1F
        compins = 0x12 if self.height == 64 else 0x02
        seg_remap = 0xA0 if rotate else 0xA1
        com_scan = 0xC0 if rotate else 0xC8
        init = (
            0xAE,
            0xD5, 0x80,
            0xA8, mux,
            0xD3, 0x00,
            0x40,
            0x8D, 0x14,
            0x20, 0x00,
            seg_remap,
            com_scan,
            0xDA, compins,
            0x81, contrast,
            0xD9, 0xF1,
            0xDB, 0x40,
            0xA4,
            0xA6,
            0x2E,
            0xAF,
        )
        for start in range(0, len(init), 16):
            self._command(*init[start:start + 16])
        self.clear()
        self.show()

    def clear(self):
        self._buf[:] = b"\x00" * len(self._buf)

    def fill(self, value=1):
        self._buf[:] = (b"\xff" if value else b"\x00") * len(self._buf)

    def pixel(self, x, y, value=1):
        if 0 <= x < self.width and 0 <= y < self.height:
            index = (y // 8) * self.width + x
            if value:
                self._buf[index] |= 1 << (y % 8)
            else:
                self._buf[index] &= ~(1 << (y % 8)) & 0xFF

    def hline(self, x0, x1, y, value=1):
        if x0 > x1:
            x0, x1 = x1, x0
        for x in range(x0, x1 + 1):
            self.pixel(x, y, value)

    def vline(self, x, y0, y1, value=1):
        if y0 > y1:
            y0, y1 = y1, y0
        for y in range(y0, y1 + 1):
            self.pixel(x, y, value)

    def rect(self, x0, y0, x1, y1, value=1):
        self.hline(x0, x1, y0, value)
        self.hline(x0, x1, y1, value)
        self.vline(x0, y0, y1, value)
        self.vline(x1, y0, y1, value)

    def fill_rect(self, x0, y0, x1, y1, value=1):
        if x0 > x1:
            x0, x1 = x1, x0
        if y0 > y1:
            y0, y1 = y1, y0
        for y in range(y0, y1 + 1):
            self.hline(x0, x1, y, value)

    def line(self, x0, y0, x1, y1, value=1):
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            self.pixel(x0, y0, value)
            if x0 == x1 and y0 == y1:
                break
            err2 = 2 * err
            if err2 >= dy:
                err += dy
                x0 += sx
            if err2 <= dx:
                err += dx
                y0 += sy

    def font(self, size=11):
        if size not in self._fonts:
            self._fonts[size] = _load_font(size)
        return self._fonts[size]

    def text_size(self, s, size=11, font=None):
        font = font or self.font(size)
        box = font.getbbox(s)
        return max(1, box[2] - box[0]), max(1, box[3] - box[1])

    def text(self, s, x=0, y=0, size=11, font=None, value=1):
        from PIL import Image, ImageDraw
        font = font or self.font(size)
        box = font.getbbox(s)
        size_px = self.text_size(s, size, font)
        image = Image.new("1", size_px, 0)
        ImageDraw.Draw(image).text((-box[0], -box[1]), s, font=font, fill=1)
        self._blit(image, x, y, value)
        return size_px

    def lines(self, texts, x=0, y=0, size=11, gap=2, font=None):
        cursor = y
        for item in texts:
            _, height = self.text(item, x, cursor, size=size, font=font)
            cursor += height + gap
        return cursor

    def show_lines(self, texts, **kwargs):
        self.clear()
        self.lines(texts, **kwargs)
        self.show()

    def image(self, source, x=0, y=0, value=1):
        from PIL import Image
        img = Image.open(source) if isinstance(source, str) else source
        img = img.convert("1")
        self._blit(img, x, y, value)
        return img.size

    def _blit(self, image, x, y, value=1):
        pixels = image.load()
        width, height = image.size
        for row in range(height):
            for col in range(width):
                if pixels[col, row]:
                    self.pixel(x + col, y + row, value)

    def show(self):
        self._command(0x21, 0, self.width - 1)
        self._command(0x22, 0, self.height // 8 - 1)
        self._write(self._buf)

    def invert(self, on=True):
        self._command(0xA7 if on else 0xA6)

    def contrast(self, value):
        self._command(0x81, max(0, min(255, int(value))))

    def power(self, on):
        self._command(0xAF if on else 0xAE)

    def close(self):
        self._i2c.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def _load_font(size):
    try:
        from PIL import ImageFont
    except ImportError as exc:
        raise DisplayUnavailable("text rendering needs Pillow (PIL)") from exc
    for path in _FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def find_displays(buses=BUS_CANDIDATES, addresses=ADDRESSES):
    found = []
    errors = []
    for bus in buses:
        try:
            handle = _open_bus(bus)
        except DisplayUnavailable as exc:
            errors.append(exc)
            continue
        try:
            for addr in addresses:
                if handle.probe(addr):
                    found.append((bus, addr))
        finally:
            handle.close()
    if not found and len(errors) == len(buses):
        raise DisplayUnavailable(str(errors[0]))
    return found


def open_display(bus=None, address=None, **kwargs):
    if bus is None:
        hits = find_displays()
        if not hits:
            raise DisplayUnavailable(
                "no SSD1306 found on i2c buses " + ", ".join(str(b) for b in BUS_CANDIDATES))
        bus, detected = hits[0]
        if address is None:
            address = detected
    return SSD1306(bus=bus, address=address, **kwargs)


def _intro(disp):
    title = "piFly"
    width, _ = disp.text_size(title, size=26)
    left = (disp.width - width) // 2
    disp.clear()
    disp.text(title, left, 8, size=26)
    disp.show()
    time.sleep(0.5)
    centre = disp.width // 2
    for step in range(0, 66, 3):
        disp.clear()
        disp.text(title, left, 8, size=26)
        disp.hline(centre - step, centre + step, 44)
        disp.show()
        time.sleep(0.015)
    subtitle = '0.96" OLED 128x64'
    subtitle_w, _ = disp.text_size(subtitle, size=11)
    disp.text(subtitle, (disp.width - subtitle_w) // 2, 50, size=11)
    disp.show()
    time.sleep(1.0)


def _scroll(disp, message, size=15, speed=0.02):
    width, _ = disp.text_size(message, size=size)
    x = disp.width
    while x > -width:
        disp.clear()
        disp.text(message, x, 24, size=size)
        disp.show()
        x -= 2
        time.sleep(speed)


def _bounce(disp, seconds=8.0, speed=0.02):
    deadline = time.time() + seconds
    x, y, dx, dy = 5.0, 5.0, 2.4, 1.7
    box = 10
    trail = []
    while time.time() < deadline:
        x += dx
        y += dy
        if x < 2 or x > disp.width - box - 2:
            dx = -dx
        if y < 2 or y > disp.height - box - 2:
            dy = -dy
        trail.append((int(x), int(y)))
        trail[:] = trail[-7:]
        disp.clear()
        disp.rect(0, 0, disp.width - 1, disp.height - 1)
        for index, (tx, ty) in enumerate(trail):
            if index == len(trail) - 1:
                disp.fill_rect(tx, ty, tx + box - 1, ty + box - 1)
            elif index % 2 == 0:
                disp.rect(tx, ty, tx + box - 1, ty + box - 1)
        disp.show()
        time.sleep(speed)


def _wave(disp, seconds=8.0, speed=0.02):
    phase = 0.0
    deadline = time.time() + seconds
    while time.time() < deadline:
        disp.clear()
        for x in range(disp.width):
            y = int(32 + 25 * math.sin(x / 11.0 + phase))
            disp.pixel(x, y)
            disp.pixel(x, y + 1)
        disp.show()
        phase += 0.28
        time.sleep(speed)


def main():
    try:
        disp = open_display()
    except DisplayUnavailable as exc:
        print(f"no display: {exc}")
        raise SystemExit(1)
    try:
        _intro(disp)
        while True:
            _scroll(disp, "piFly  -  128x64 SSD1306  -  hello!")
            _bounce(disp)
            _wave(disp)
    except KeyboardInterrupt:
        pass
    finally:
        disp.clear()
        disp.show()
        disp.close()


if __name__ == "__main__":
    main()
