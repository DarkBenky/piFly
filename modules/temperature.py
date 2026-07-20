import os
import time
import board
import digitalio
from adafruit_bme280 import basic as adafruit_bme280


def cpu_temp() -> float:
    """Return Raspberry Pi CPU temperature in °C."""
    with open("/sys/class/thermal/thermal_zone0/temp") as f:
        return float(f.read().strip()) / 1000.0


spi = board.SPI()
cs = digitalio.DigitalInOut(board.D5)
bme280 = adafruit_bme280.Adafruit_BME280_SPI(spi, cs)

LOG_FILE = os.path.join(os.path.dirname(__file__), "..", "logs", "temperature.log")


if __name__ == "__main__":
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w") as f:
            f.write("timestamp,bme_temp_c,bme_pressure_hpa,bme_humidity_pct,cpu_temp_c\n")

    while True:
        bme_t = bme280.temperature
        bme_p = bme280.pressure
        bme_h = bme280.relative_humidity
        cpu_t = cpu_temp()

        print(f"BME280 => {bme_t:.1f}°C  {bme_p:.1f} hPa  {bme_h:.1f}%  |  CPU => {cpu_t:.1f}°C")

        with open(LOG_FILE, "a") as f:
            f.write(f"{time.time()},{bme_t},{bme_p},{bme_h},{cpu_t}\n")

        time.sleep(15)