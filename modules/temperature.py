import os
import struct
import time

import board
import digitalio
from adafruit_bme280 import basic as adafruit_bme280


class SensorUnavailable(RuntimeError):
    """No BME280 could be reached on any supported bus."""


# Buses probed in order.  i2c-5 lives on GPIO10/11 (physical pins 19/23) and is
# enabled with:   dtparam=spi=off  +  dtoverlay=i2c5,pins_10_11
# i2c-0 is the HAT-ID bus: GPIO0/GPIO1 = physical pins 27/28.
I2C_BUSES = (
    (5, "i2c-5 (SDA=GPIO10/pin19, SCL=GPIO11/pin23)"),
    (0, "i2c-0 (SDA=GPIO0/pin27, SCL=GPIO1/pin28, HAT-ID pins)"),
    (1, "i2c-1 (SDA=GPIO2/pin3, SCL=GPIO3/pin5)"),
)
I2C_ADDRESSES = (0x77, 0x76)   # 0x77 = Adafruit default, 0x76 = ADDR jumper closed
SPI_CS_PIN = board.D5           # legacy SPI wiring: CS on GPIO5 / physical pin 29


class _SMBusI2C:
    """busio.I2C-compatible shim for an arbitrary /dev/i2c-N bus.

    Blinka can only resolve i2c-1 (and i2c-0) from pin objects, so a second
    hardware bus such as i2c-5 on GPIO10/11 needs this thin adapter.
    """

    def __init__(self, bus_num: int):
        from Adafruit_PureIO import smbus
        self._bus = smbus.SMBus(bus_num)

    def try_lock(self) -> bool:
        return True

    def unlock(self) -> None:
        pass

    def writeto(self, address, buffer, *, start=0, end=None, stop=True):
        end = len(buffer) if end is None else end
        data = bytes(buffer[start:end])
        if data:
            self._bus.write_bytes(address, data)
        else:
            self._bus.write_quick(address)   # address-only probe

    def readfrom_into(self, address, buffer, *, start=0, end=None, stop=True):
        end = len(buffer) if end is None else end
        data = self._bus.read_bytes(address, end - start)
        for offset, value in enumerate(data):
            buffer[start + offset] = value


def _use_single_ioctl_reads(device, bus_num: int, address: int):
    """Re-route the driver's register reads through one combined I2C ioctl.

    The stock driver reads a register in two separate transfers (write the
    register pointer, then read).  Anything else using the bus - the IMU or
    the OLED on i2c-1, the other service process - can slip between the two
    and the read then returns data from the wrong register, which the
    compensation maths turns into huge bogus values (e.g. -140 C / 500 hPa).
    A combined ioctl shrinks that window a lot, but it is not a guarantee -
    every read in this module is double-read and checked on top of it (see
    BME280._read_raw_data).
    """
    try:
        from smbus2 import SMBus
        smbus = SMBus(bus_num)
        device._bus_implementation.read_register = (
            lambda register, length: bytes(
                smbus.read_i2c_block_data(address, register, length))
        )
    except Exception:                  # noqa: BLE001 - fall back to driver reads
        pass
    return device


def _is_not_ready(block: bytes) -> bool:
    """True when the results block holds the power-on 'not ready' markers."""
    t_raw = (block[3] << 16 | block[4] << 8 | block[5]) >> 4
    p_raw = (block[0] << 16 | block[1] << 8 | block[2]) >> 4
    h_raw = block[6] << 8 | block[7]
    return t_raw == 0x80000 or p_raw == 0x80000 or h_raw == 0x8000


def cpu_temp() -> float:
    """Return Raspberry Pi CPU temperature in °C."""
    with open("/sys/class/thermal/thermal_zone0/temp") as f:
        return float(f.read().strip()) / 1000.0


LOG_FILE = os.path.join(os.path.dirname(__file__), "..", "logs", "temperature.log")

class BME280:
    """BME280 temperature / pressure / humidity sensor.

    Probes I2C bus 5, bus 0 (HAT-ID pins 27/28), then bus 1 (addresses 0x77 and
    0x76), then finally the legacy SPI wiring on GPIO5.  The sensor stays in
    continuous mode; every register read is done twice and compared, values
    that are impossible for a real measurement are rejected, and the device
    is re-detected once in case it was power-cycled underneath us.
    """

    def __init__(self, retries: int = 3, retry_delay: float = 0.25,
                 read_retries: int = 3):
        self._retries = retries
        self._retry_delay = retry_delay
        self._read_retries = read_retries
        self.bme280 = None
        self.backend = None
        self._calib = None
        self._last_good = None
        self._connect(retries, retry_delay)

    # -- connection ---------------------------------------------------------
    def _backends(self):
        for bus, label in I2C_BUSES:
            yield label, (lambda bus=bus: self._open_i2c(bus))
        yield f"SPI (CS=GPIO{SPI_CS_PIN})", self._open_spi

    def _open_i2c(self, bus_num: int):
        bus = _SMBusI2C(bus_num)
        last_error = None
        for address in I2C_ADDRESSES:
            try:
                device = adafruit_bme280.Adafruit_BME280_I2C(bus, address=address)
            except Exception as exc:       # noqa: BLE001 - probing, keep trying
                last_error = exc
                continue
            return _use_single_ioctl_reads(device, bus_num, address)
        raise last_error

    def _open_spi(self):
        spi = board.SPI()
        cs = digitalio.DigitalInOut(SPI_CS_PIN)
        try:
            return adafruit_bme280.Adafruit_BME280_SPI(spi, cs)
        except Exception:
            cs.deinit()
            raise

    def _connect(self, retries: int = None, retry_delay: float = None) -> None:
        retries = self._retries if retries is None else retries
        retry_delay = self._retry_delay if retry_delay is None else retry_delay
        errors = []
        for attempt in range(1, max(1, retries) + 1):
            errors = []
            for label, opener in self._backends():
                try:
                    device = opener()
                except Exception as exc:   # noqa: BLE001 - probing, keep trying
                    errors.append(f"{label}: {type(exc).__name__}: {exc}")
                    continue
                self.bme280 = device
                self.backend = label
                try:
                    self._configure()
                    self._load_calibration()
                    self._check_live()
                except Exception as exc:   # noqa: BLE001 - bus errors etc.
                    errors.append(f"{label}: {type(exc).__name__}: {exc}")
                    self.bme280 = None
                    self._calib = None
                    continue
                self._last_good = None     # fresh baseline for this session
                return
            if attempt < retries:
                time.sleep(retry_delay)
        raise SensorUnavailable(
            "BME280 not found on " + " / ".join(lbl for lbl, _ in self._backends())
            + " -- " + "; ".join(errors[-3:])
        )

    def _configure(self) -> None:
        self.bme280.overscan_temperature = adafruit_bme280.OVERSCAN_X1
        self.bme280.overscan_pressure = adafruit_bme280.OVERSCAN_X1
        self.bme280.overscan_humidity = adafruit_bme280.OVERSCAN_X1
        # Continuous (normal) mode with a 500 ms standby: every read returns
        # the result of the last completed conversion, so a read can never
        # land during a conversion.  Forced mode re-triggers a conversion on
        # every property read and used to return torn registers - the
        # one-sample -140 C / 500 hPa spikes.
        # The writes are read back because a write issued while the sensor is
        # busy (NVM copy right after a reset) can be ignored - that is how a
        # sensor ends up stuck on its reset markers.
        for _ in range(10):
            try:
                self.bme280._write_register_byte(0xF5, 0x80)   # t_sb = 500 ms
                self.bme280.mode = adafruit_bme280.MODE_NORMAL
                if self.bme280._read_register(0xF4, 1)[0] == self.bme280._ctrl_meas:
                    return
            except (OSError, RuntimeError, ValueError, ArithmeticError):
                pass
            time.sleep(0.01)
        raise SensorUnavailable("BME280 did not enter normal mode")

    @property
    def available(self) -> bool:
        return self.bme280 is not None

    # -- calibration & raw reads --------------------------------------------
    def _load_calibration(self) -> None:
        """Read the calibration blocks - double-read, sanity-checked.

        This is a one-shot read at connect time, and a torn read here would
        silently poison every later value (seen 2026-10-15: a process that
        computed -31 C from perfectly correct raw registers).  Reading each
        block twice and requiring a match makes that practically impossible.
        """
        for _ in range(6):
            try:
                first = self._read_calibration_bytes()
                second = self._read_calibration_bytes()
            except (OSError, RuntimeError, ValueError, struct.error):
                time.sleep(0.05)
                continue
            if first == second:
                calib = self._parse_calibration(*first)
                if calib is not None:
                    self._calib = calib
                    return
            time.sleep(0.05)
        raise SensorUnavailable("BME280 calibration reads kept changing")

    def _read_calibration_bytes(self):
        return (bytes(self.bme280._read_register(0x88, 24)),
                bytes(self.bme280._read_register(0xA1, 1)),
                bytes(self.bme280._read_register(0xE1, 7)))

    @staticmethod
    def _parse_calibration(coeff: bytes, h1: bytes, he: bytes):
        if len(coeff) != 24 or len(h1) != 1 or len(he) != 7:
            return None
        raw = list(struct.unpack("<HhhHhhhhhhhh", coeff))
        t_calib = [float(v) for v in raw[:3]]
        p_calib = [float(v) for v in raw[3:]]
        hc = list(struct.unpack("<hBbBbb", he))
        h_calib = [float(h1[0]), float(hc[0]), float(hc[1]),
                   float((hc[2] << 4) | (hc[3] & 0x0F)),
                   float((hc[4] << 4) | (hc[3] >> 4)), float(hc[5])]
        # wide plausibility bounds: a corrupted block is usually wild
        if not 10000.0 <= t_calib[0] <= 40000.0:
            return None
        if not 10000.0 <= p_calib[0] <= 50000.0:
            return None
        return (t_calib, p_calib, h_calib)

    def _check_live(self) -> None:
        """Make sure real measurements are flowing, not reset markers."""
        for _ in range(5):
            if self._read_raw_data() is not None:
                return
            time.sleep(0.15)
        raise SensorUnavailable("BME280 is not producing measurements")

    def _read_raw_data(self):
        """The 8-byte results block, read twice and compared.

        A single read can come back torn - another process, or the IMU/OLED
        on the same bus, slipping between the register write and the read,
        or the sensor updating the registers mid-read.  Two identical reads
        rule that out, and reset markers ('not ready') are rejected.
        """
        for _ in range(4):
            try:
                first = bytes(self.bme280._read_register(0xF7, 8))
                second = bytes(self.bme280._read_register(0xF7, 8))
            except (OSError, RuntimeError, ValueError):
                time.sleep(0.05)
                continue
            if first == second and not _is_not_ready(first):
                return first
            time.sleep(0.05)
        return None

    def _compensate(self, block: bytes):
        """Convert one results block to (temp C, pressure hPa, humidity %).

        Ported from adafruit_bme280.basic (including the integer truncation
        of t_fine) so values match what the driver produced historically.
        """
        t_calib, p_calib, h_calib = self._calib
        raw_t = (block[3] << 16 | block[4] << 8 | block[5]) >> 4
        raw_p = (block[0] << 16 | block[1] << 8 | block[2]) >> 4
        raw_h = block[6] << 8 | block[7]

        var1 = (raw_t / 16384.0 - t_calib[0] / 1024.0) * t_calib[1]
        var2 = ((raw_t / 131072.0 - t_calib[0] / 8192.0) ** 2) * t_calib[2]
        t_fine = int(var1 + var2)
        temp = t_fine / 5120.0

        var1 = t_fine / 2.0 - 64000.0
        var2 = var1 * var1 * p_calib[5] / 32768.0
        var2 += var1 * p_calib[4] * 2.0
        var2 = var2 / 4.0 + p_calib[3] * 65536.0
        var3 = p_calib[2] * var1 * var1 / 524288.0
        var1 = (var3 + p_calib[1] * var1) / 524288.0
        var1 = (1.0 + var1 / 32768.0) * p_calib[0]
        if not var1:
            raise ArithmeticError("invalid BME280 calibration")
        press = 1048576.0 - raw_p
        press = ((press - var2 / 4096.0) * 6250.0) / var1
        var1 = p_calib[8] * press * press / 2147483648.0
        var2 = press * p_calib[7] / 32768.0
        press = (press + (var1 + var2 + p_calib[6]) / 16.0) / 100.0

        var1 = float(t_fine) - 76800.0
        var2 = h_calib[3] * 64.0 + (h_calib[4] / 16384.0) * var1
        var3 = raw_h - var2
        var4 = h_calib[1] / 65536.0
        var5 = 1.0 + (h_calib[2] / 67108864.0) * var1
        var6 = 1.0 + (h_calib[5] / 67108864.0) * var1 * var5
        var6 = var3 * var4 * (var5 * var6)
        humid = var6 * (1.0 - h_calib[0] * var6 / 524288.0)
        if humid > 100.0:
            humid = 100.0
        elif humid < 0.0:
            humid = 0.0
        return temp, press, humid

    # -- reading ------------------------------------------------------------
    def _try_read(self):
        """Single read attempt; returns None when the bus errors out."""
        try:
            if self.bme280 is None or self._calib is None:
                return None
            block = self._read_raw_data()
            if block is None:
                return None
            temp, press, humid = self._compensate(block)
            cpu = cpu_temp()
        except (OSError, RuntimeError, ValueError, ArithmeticError):
            return None
        return {
            "timestamp": time.time(),
            "bme_temp_c": temp,
            "bme_pressure_hpa": press,
            "bme_humidity_pct": humid,
            "cpu_temp_c": cpu,
        }

    # plausible value ranges and the largest change physically possible
    # between two consecutive reads (used to reject torn-register reads)
    RANGES = {
        "bme_temp_c": (-45.0, 90.0),
        "bme_pressure_hpa": (850.0, 1090.0),   # ground-level air pressure
        "bme_humidity_pct": (0.0, 100.0),
    }
    MAX_STEP = {
        "bme_temp_c": 4.0,
        "bme_pressure_hpa": 4.0,
        "bme_humidity_pct": 12.0,
    }

    def _plausible(self, record: dict) -> bool:
        """Reject impossible values before they pollute the data.

        A bad read shows up as an out-of-range value (e.g. -140 C,
        500 hPa) or an impossible jump versus the previous accepted read.
        """
        for key, (low, high) in self.RANGES.items():
            if not low <= record[key] <= high:
                return False
        last = self._last_good
        if last is not None and record["timestamp"] - last["timestamp"] < 300.0:
            for key, limit in self.MAX_STEP.items():
                if abs(record[key] - last[key]) > limit:
                    return False
        return True

    def get_record(self) -> dict:
        for attempt in range(1, self._read_retries + 1):
            record = self._try_read()
            if record is not None and self._plausible(record):
                self._last_good = record
                return record
            if attempt < self._read_retries:
                time.sleep(self._retry_delay)
        # the device may have been power-cycled: re-detect once before giving up
        try:
            self._connect(retries=1)
        except SensorUnavailable as exc:
            raise SensorUnavailable(
                f"BME280 unreadable and no longer detected: {exc}"
            ) from exc
        record = self._try_read()
        if record is not None and self._plausible(record):
            self._last_good = record
            return record
        raise SensorUnavailable("BME280 re-detected but reads still fail")

    def close(self) -> None:
        self.bme280 = None

    # -- diagnostics --------------------------------------------------------
    def health_check(self, seconds: float = 30.0, period: float = 0.05,
                     verbose: bool = True) -> dict:
        """Hammer the sensor and report connection quality.

        Use this to tell a solid contact apart from a marginal one:
        `python temperature.py --check`
        """
        start = time.time()
        samples = ok = transitions = 0
        present = None
        longest_gap = 0.0
        last_ok = None
        while time.time() - start < seconds:
            alive = self._try_read() is not None
            samples += 1
            if alive:
                ok += 1
                if last_ok is not None:
                    longest_gap = max(longest_gap, time.time() - last_ok)
                last_ok = time.time()
            if present is None:
                present = alive
            elif alive != present:
                present = alive
                transitions += 1
                if verbose:
                    print(f"[{time.time() - start:6.1f}s] sensor "
                          f"{'came back' if alive else 'dropped out'}")
            time.sleep(period)

        success = 100.0 * ok / samples if samples else 0.0
        summary = {
            "backend": self.backend,
            "samples": samples,
            "ok": ok,
            "success_pct": success,
            "transitions": transitions,
            "longest_gap_s": longest_gap,
        }
        if verbose:
            print(f"\n{self.backend}")
            print(f"  {ok}/{samples} reads OK  ({success:.1f}%)  over {seconds:.0f}s")
            print(f"  dropouts/returns: {transitions}")
            if success >= 99.0:
                print("  verdict: connection looks SOLID")
            elif success >= 50.0:
                print("  verdict: connection is MARGINAL -- reseat VIN/GND/SDA/SCL "
                      "and try another jumper on the worst wire")
            else:
                print("  verdict: connection is BROKEN most of the time -- check "
                      "wiring, then re-run")
        return summary


def open_bme280(retries: int = 2, retry_delay: float = 0.25,
                quiet: bool = False) -> "BME280 | None":
    """Open the BME280, returning None instead of raising when it is absent.

    Use this wherever the sensor is optional: a missing barometer should never
    take down navigation, it should just leave the altitude updates out.
    """
    try:
        return BME280(retries=retries, retry_delay=retry_delay)
    except SensorUnavailable as exc:
        if not quiet:
            print(f"[temperature] BME280 unavailable: {exc}")
        return None


def _parse_args(argv):
    """--check runs a connection health test; --interval sets the logging period."""
    opts = {"check": False, "seconds": 30.0, "interval": 60.0}
    for i, arg in enumerate(argv):
        if arg == "--check":
            opts["check"] = True
        elif arg == "--seconds" and i + 1 < len(argv):
            opts["seconds"] = float(argv[i + 1])
        elif arg == "--interval" and i + 1 < len(argv):
            opts["interval"] = float(argv[i + 1])
    return opts


if __name__ == "__main__":
    import sys

    opts = _parse_args(sys.argv[1:])

    if opts["check"]:
        print(f"BME280 connection health check -- {opts['seconds']:.0f}s of "
              "continuous reads\n")
        sensor = open_bme280()
        if sensor is None:
            print("sensor NOT detected on any bus.")
            print("wiring: VIN=pin17 (3V3), GND=pin20, SDA=pin27, SCL=pin28")
            raise SystemExit(2)
        summary = sensor.health_check(seconds=opts["seconds"])
        raise SystemExit(0 if summary["success_pct"] >= 99.0 else 1)

    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w") as f:
            f.write("timestamp,bme_temp_c,bme_pressure_hpa,bme_humidity_pct,cpu_temp_c\n")

    sensor = open_bme280()
    if sensor is not None:
        print(f"[temperature] BME280 detected on {sensor.backend}")

    reads_ok = 0
    reads_failed = 0
    failures_in_a_row = 0

    while True:
        if sensor is None:
            print(f"BME280 => not detected  |  CPU => {cpu_temp():.1f}°C"
                  "  (no sensor row logged; still looking for it)")
            time.sleep(opts["interval"])
            sensor = open_bme280(quiet=True)   # pick it up when it appears
            continue

        try:
            rec = sensor.get_record()
        except SensorUnavailable as exc:
            reads_failed += 1
            failures_in_a_row += 1
            print(f"[temperature] read failed (#{failures_in_a_row} in a row): {exc}")
            if failures_in_a_row >= 3:
                print("[temperature] connection looks broken -- check VIN(17)/GND(20)/"
                      "SDA(27)/SCL(28), then run: python temperature.py --check")
                sensor = None
            time.sleep(opts["interval"])
            continue

        failures_in_a_row = 0
        reads_ok += 1

        print(
            f"BME280 => {rec['bme_temp_c']:.1f}°C  "
            f"{rec['bme_pressure_hpa']:.1f} hPa  "
            f"{rec['bme_humidity_pct']:.1f}%  |  "
            f"CPU => {rec['cpu_temp_c']:.1f}°C"
        )

        with open(LOG_FILE, "a") as f:
            f.write(
                f"{rec['timestamp']},{rec['bme_temp_c']},"
                f"{rec['bme_pressure_hpa']},{rec['bme_humidity_pct']},"
                f"{rec['cpu_temp_c']}\n"
            )

        total = reads_ok + reads_failed
        if total % 10 == 0:
            print(f"[temperature] connection health: {reads_ok}/{total} reads OK "
                  f"({100.0 * reads_ok / total:.1f}%)")

        time.sleep(opts["interval"])