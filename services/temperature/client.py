import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from modules.temperature import BME280
import requests
import time

SERVER_URL = "http://91.98.145.193:5000/api/ingest"
SAMPLES = 8
PERIOD_S = 15
SMOOTHING = 0.4  # blend 40% new value, 60% previous value


if __name__ == "__main__":
    print(f"Target server: {SERVER_URL}")
    sensor = BME280()
    prev_rec = None

    while True:
        records = []
        for _ in range(SAMPLES):
            rec = sensor.get_record()
            records.append(rec)
            time.sleep(PERIOD_S / SAMPLES)

        if not records:
            continue

        rec = {
            "timestamp": records[0]["timestamp"],
            "bme_temp_c": sum(r["bme_temp_c"] for r in records) / len(records),
            "bme_pressure_hpa": sum(r["bme_pressure_hpa"] for r in records) / len(records),
            "bme_humidity_pct": sum(r["bme_humidity_pct"] for r in records) / len(records),
            "cpu_temp_c": sum(r["cpu_temp_c"] for r in records) / len(records),
        }

        # blend with previous value for extra smoothness
        if prev_rec is not None:
            alpha = SMOOTHING
            rec["bme_temp_c"] = alpha * rec["bme_temp_c"] + (1 - alpha) * prev_rec["bme_temp_c"]
            rec["bme_pressure_hpa"] = alpha * rec["bme_pressure_hpa"] + (1 - alpha) * prev_rec["bme_pressure_hpa"]
            rec["bme_humidity_pct"] = alpha * rec["bme_humidity_pct"] + (1 - alpha) * prev_rec["bme_humidity_pct"]
            rec["cpu_temp_c"] = alpha * rec["cpu_temp_c"] + (1 - alpha) * prev_rec["cpu_temp_c"]

        try:
            resp = requests.post(SERVER_URL, json=rec, timeout=10)
            if resp.status_code == 200:
                print(f"Sent -> {rec}")
                prev_rec = rec
            else:
                print(f"Server error {resp.status_code}: {resp.text}")
        except requests.exceptions.RequestException as e:
            print(f"POST failed: {e}. Retrying in 5s...")
            time.sleep(5)

