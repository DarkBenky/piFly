import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from modules.temperature import BME280
import socket
import time

SERVER_HOST = "91.98.145.193"
SERVER_PORT = 5444
SAMPLES = 8
PERIOD_S = 15

def significantChange(a, b, threshold):
    return abs(a - b) > threshold

if __name__ == "__main__":
    print(f"Connecting to {SERVER_HOST}:{SERVER_PORT}")
    sensor = BME280()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((SERVER_HOST, SERVER_PORT))
        prev_temp = None
        while True:
            records = []
            for _ in range(SAMPLES):
                rec = sensor.get_record()
                if  prev_temp is not None and significantChange(rec["bme_temp_c"], prev_temp, 0.25):
                    print(f"Temp changed from {prev_temp} to {rec['bme_temp_c']} -> skipping")
                    continue
                records.append(rec)
                time.sleep(PERIOD_S / SAMPLES)

            if not records:
                continue

            rec = {
                "timestamp": records[0]["timestamp"],
                "bme_temp_c": sum([r["bme_temp_c"] for r in records]) / len(records),
                "bme_pressure_hpa": sum([r["bme_pressure_hpa"] for r in records]) / len(records),
                "bme_humidity_pct": sum([r["bme_humidity_pct"] for r in records]) / len(records),
                "cpu_temp_c": sum([r["cpu_temp_c"] for r in records]) / len(records),
            }
            
            s.sendall(f"{rec['timestamp']},{rec['bme_temp_c']},"
                      f"{rec['bme_pressure_hpa']},{rec['bme_humidity_pct']},"
                      f"{rec['cpu_temp_c']}\n".encode())
            print(f"Sent -> {rec}")
            prev_temp = rec["bme_temp_c"]
