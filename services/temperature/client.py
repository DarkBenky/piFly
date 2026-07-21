import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from modules.temperature import BME280
import socket
import time

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 5444

if __name__ == "__main__":
    sensor = BME280()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((SERVER_HOST, SERVER_PORT))
        while True:
            rec = sensor.get_record()
            s.sendall(f"{rec['timestamp']},{rec['bme_temp_c']},"
                      f"{rec['bme_pressure_hpa']},{rec['bme_humidity_pct']},"
                      f"{rec['cpu_temp_c']}\n".encode())
            print(f"Sent -> {rec}")
            time.sleep(15)