import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import requests

from modules.temperature import open_bme280

SERVER_URL = "http://91.98.145.193:5000/api/ingest"
DEFAULT_SAMPLES = 4
DEFAULT_PERIOD_S = 120.0
SMOOTHING = 0.4


def parse_args(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    parser.add_argument("--period", type=float, default=DEFAULT_PERIOD_S,
                        help="seconds for one full sample-and-send cycle")
    parser.add_argument("--smoothing", type=float, default=SMOOTHING)
    parser.add_argument("--server", default=SERVER_URL)
    parser.add_argument("--no-log", action="store_true",
                        help="read the sensor but send nothing to the server")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    print(f"Target server: {args.server}"
          + (" (no-log: nothing will be sent)" if args.no_log else ""))
    sensor = open_bme280()
    if sensor is None:
        print("No BME280 detected -- check the sensor wiring/power. Exiting.")
        return 1

    prev_rec = None
    while True:
        records = []
        for _ in range(args.samples):
            records.append(sensor.get_record())
            time.sleep(args.period / args.samples)

        rec = {
            "timestamp": records[0]["timestamp"],
            "bme_temp_c": sum(r["bme_temp_c"] for r in records) / len(records),
            "bme_pressure_hpa": sum(r["bme_pressure_hpa"] for r in records) / len(records),
            "bme_humidity_pct": sum(r["bme_humidity_pct"] for r in records) / len(records),
            "cpu_temp_c": sum(r["cpu_temp_c"] for r in records) / len(records),
        }

        if prev_rec is not None:
            for key in ("bme_temp_c", "bme_pressure_hpa", "bme_humidity_pct", "cpu_temp_c"):
                rec[key] = args.smoothing * rec[key] + (1 - args.smoothing) * prev_rec[key]

        if args.no_log:
            print(f"[no-log] {time.strftime('%H:%M:%S')} "
                  f"{rec['bme_temp_c']:.2f}C {rec['bme_humidity_pct']:.1f}% "
                  f"{rec['bme_pressure_hpa']:.1f}hPa cpu {rec['cpu_temp_c']:.1f}C")
            prev_rec = rec
            continue

        try:
            resp = requests.post(args.server, json=rec, timeout=10)
            if resp.status_code == 200:
                print(f"Sent -> {rec}")
                prev_rec = rec
            else:
                print(f"Server error {resp.status_code}: {resp.text}")
        except requests.exceptions.RequestException as e:
            print(f"POST failed: {e}. Retrying in 5s...")
            time.sleep(5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

