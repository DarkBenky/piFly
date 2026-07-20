import time
import math
import smbus2

# PCA9685 register map
_MODE1        = 0x00
_PRESCALE     = 0xFE
_LED0_ON_L    = 0x06

# Servo pulse bounds in microseconds (standard SG90/MG996R)
PULSE_MIN_US  = 500
PULSE_MAX_US  = 2500
ANGLE_MIN     = 0
ANGLE_MAX     = 180


class ServoDriver:
    def __init__(self, address: int = 0x40, bus: int = 1, freq: int = 50):
        self._bus     = smbus2.SMBus(bus)
        self._address = address
        self._write(_MODE1, 0x00)
        self._set_freq(freq)

    def _write(self, reg: int, value: int) -> None:
        self._bus.write_byte_data(self._address, reg, value)

    def _read(self, reg: int) -> int:
        return self._bus.read_byte_data(self._address, reg)

    def _set_freq(self, freq: int) -> None:
        prescale = int(math.floor(25_000_000.0 / 4096.0 / freq - 1.0 + 0.5))
        old_mode = self._read(_MODE1)
        self._write(_MODE1, (old_mode & 0x7F) | 0x10)   # sleep
        self._write(_PRESCALE, prescale)
        self._write(_MODE1, old_mode)
        time.sleep(0.005)
        self._write(_MODE1, old_mode | 0x80)             # restart

    def _set_pwm(self, channel: int, on: int, off: int) -> None:
        base = _LED0_ON_L + 4 * channel
        self._bus.write_byte_data(self._address, base,     on  & 0xFF)
        self._bus.write_byte_data(self._address, base + 1, on  >> 8)
        self._bus.write_byte_data(self._address, base + 2, off & 0xFF)
        self._bus.write_byte_data(self._address, base + 3, off >> 8)

    def set_pulse(self, channel: int, pulse_us: float) -> None:
        """Drive channel to an explicit pulse width in microseconds (500-2500)."""
        if not 0 <= channel <= 15:
            raise ValueError(f"channel must be 0-15, got {channel}")
        pulse_us = max(PULSE_MIN_US, min(PULSE_MAX_US, pulse_us))
        off = int(pulse_us * 4096 / 20_000)   # 50 Hz -> 20 000 us period
        self._set_pwm(channel, 0, off)

    def set_angle(self, channel: int, angle_deg: float) -> None:
        """Move servo on channel to angle_deg (0-180)."""
        angle_deg = max(ANGLE_MIN, min(ANGLE_MAX, angle_deg))
        pulse_us  = PULSE_MIN_US + (PULSE_MAX_US - PULSE_MIN_US) * angle_deg / ANGLE_MAX
        self.set_pulse(channel, pulse_us)

    def close(self) -> None:
        self._bus.close()


if __name__ == "__main__":
    driver = ServoDriver(address=0x40, bus=1)

    STEP   = 2      # degrees per tick
    TICK_S = 0.02   # seconds between ticks
    REST_S = 1.0    # pause between servos

    while True:
        for ch in range(4):    # channels 0-3
            print(f"servo {ch}: sweeping 0 -> 180")
            for angle in range(ANGLE_MIN, ANGLE_MAX + 1, STEP):
                driver.set_angle(ch, angle)
                time.sleep(TICK_S)
            driver.set_angle(ch, 0)   # return to home
            time.sleep(REST_S)

    # driver.close()