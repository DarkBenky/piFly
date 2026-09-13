import os
import time

import board
import digitalio
from adafruit_bme280 import basic as adafruit_bme280


class SensorUnavailable(RuntimeError):
    """No BME280 could be reached on any supported bus."""


# Buses probed in order.  i2c-5 lives on GPIO10/11 (physical pins 19/23) and is
# enabled with:   dtparam=spi=off  +  dtoverlay=i2c5,pins_10_11
I2C_BUSES = (
    (5, "i2c-5 (SDA=GPIO10/pin19, SCL=GPIO11/pin23)"),
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


def cpu_temp() -> float:
    """Return Raspberry Pi CPU temperature in °C."""
    with open("/sys/class/thermal/thermal_zone0/temp") as f:
        return float(f.read().strip()) / 1000.0


LOG_FILE = os.path.join(os.path.dirname(__file__), "..", "logs", "temperature.log")

class BME280:
    """BME280 temperature / pressure / humidity sensor.

    Probes I2C bus 5, then bus 1 (addresses 0x77 and 0x76), then finally the
    legacy SPI wiring on GPIO5.  Reads are retried, and the device is
    re-detected once in case it was power-cycled underneath us.
    """

    def __init__(self, retries: int = 3, retry_delay: float = 0.25,
                 read_retries: int = 3):
        self._retries = retries
        self._retry_delay = retry_delay
        self._read_retries = read_retries
        self.bme280 = None
        self.backend = None
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
                return adafruit_bme280.Adafruit_BME280_I2C(bus, address=address)
            except Exception as exc:       # noqa: BLE001 - probing, keep trying
                last_error = exc
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
                self._configure()
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
        self.bme280.mode = adafruit_bme280.MODE_FORCE

    @property
    def available(self) -> bool:
        return self.bme280 is not None

    # -- reading ------------------------------------------------------------
    def _try_read(self):
        """Single read attempt; returns None when the bus errors out."""
        try:
            return {
                "timestamp": time.time(),
                "bme_temp_c": self.bme280.temperature,
                "bme_pressure_hpa": self.bme280.pressure,
                "bme_humidity_pct": self.bme280.relative_humidity,
                "cpu_temp_c": cpu_temp(),
            }
        except (OSError, RuntimeError, ValueError):
            return None

    def get_record(self) -> dict:
        for attempt in range(1, self._read_retries + 1):
            record = self._try_read()
            if record is not None:
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
        if record is not None:
            return record
        raise SensorUnavailable("BME280 re-detected but reads still fail")

    def close(self) -> None:
        self.bme280 = None


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


if __name__ == "__main__":
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w") as f:
            f.write("timestamp,bme_temp_c,bme_pressure_hpa,bme_humidity_pct,cpu_temp_c\n")

    sensor = open_bme280()
    if sensor is not None:
        print(f"[temperature] BME280 detected on {sensor.backend}")

    while True:
        if sensor is None:
            print(f"BME280 => not detected  |  CPU => {cpu_temp():.1f}°C"
                  "  (no sensor row logged; still looking for it)")
            time.sleep(60)
            sensor = open_bme280(quiet=True)   # pick it up when it appears
            continue

        try:
            rec = sensor.get_record()
        except SensorUnavailable as exc:
            print(f"[temperature] {exc}")
            sensor = None
            continue

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

        time.sleep(60)