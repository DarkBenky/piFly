import time
import board
import digitalio
from adafruit_bme280 import basic as adafruit_bme280


spi = board.SPI()
cs = digitalio.DigitalInOut(board.D5)
bme280 = adafruit_bme280.Adafruit_BME280_SPI(spi, cs)


if __name__ == "__main__":
    while True:
        print("Temperature: %0.1f C" % bme280.temperature)
        print("Pressure: %0.1f hPa" % bme280.pressure)
        print("Humidity: %0.1f %%" % bme280.relative_humidity)
        print("")
        with open("../logs/temperature.log", "a") as f:
            f.write(f"{time.time()},{bme280.temperature},{bme280.pressure},{bme280.relative_humidity}\n")
        time.sleep(30)