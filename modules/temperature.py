import os
import time
import board
import digitalio
from adafruit_bme280 import basic as adafruit_bme280


def cpu_temp() -> float:
    """Return Raspberry Pi CPU temperature in °C."""
    with open("/sys/class/thermal/thermal_zone0/temp") as f:
        return float(f.read().strip()) / 1000.0


LOG_FILE = os.path.join(os.path.dirname(__file__), "..", "logs", "temperature.log")

class BME280:
    def __init__(self):
        self.spi = board.SPI()
        self.cs = digitalio.DigitalInOut(board.D5)
        self.bme280 = adafruit_bme280.Adafruit_BME280_SPI(self.spi, self.cs)
    
    def get_record(self):
        return {
            "timestamp": time.time(),
            "bme_temp_c": self.bme280.temperature,
            "bme_pressure_hpa": self.bme280.pressure,
            "bme_humidity_pct": self.bme280.relative_humidity,
            "cpu_temp_c": cpu_temp()
        }


if __name__ == "__main__":
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w") as f:
            f.write("timestamp,bme_temp_c,bme_pressure_hpa,bme_humidity_pct,cpu_temp_c\n")

    sensor = BME280()
    while True:
        rec = sensor.get_record()

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

        time.sleep(15)