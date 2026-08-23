import json
import os
import time

import requests

from location import IMU_STATIC_OFFSETS, PRESSURE_STATIC_OFFSET
from position import Position

LOCATION_UI_URL = "http://91.98.145.193:5001/api/ingest"


def loadBaseline(path: str):
    with open(path, "r") as f:
        data = json.load(f)
    imu_d = data["imu"]
    pres_d = data["pressure"]
    imu = IMU_STATIC_OFFSETS(
        imu_d["xAcceleration"], imu_d["yAcceleration"], imu_d["zAcceleration"],
        imu_d["xGyro"], imu_d["yGyro"], imu_d["zGyro"],
        imu_d["xMagneto"], imu_d["yMagneto"], imu_d["zMagneto"],
        imu_d["xStdAcc"], imu_d["yStdAcc"], imu_d["zStdAcc"],
        imu_d["xStdGyro"], imu_d["yStdGyro"], imu_d["zStdGyro"],
        imu_d["xStdMag"], imu_d["yStdMag"], imu_d["zStdMag"],
    )
    pres = PRESSURE_STATIC_OFFSET(
        pres_d.get("pressure", 1013.25),
        pres_d.get("stdPressure", 0.0),
        pres_d.get("temperature", 15.0),
        pres_d.get("stdTemperature", 0.0),
    )
    return imu, pres


def track(imu_stat: IMU_STATIC_OFFSETS, pressure_stat: PRESSURE_STATIC_OFFSET,
          post_url: str = LOCATION_UI_URL, post_interval: float = 0.2):
    pos = Position(imu_stat, pressure_stat)
    session = requests.Session()
    last_post = 0.0
    last_print = 0.0
    last_warn = 0.0

    print(f"Streaming attitude + altitude -> {post_url}")
    try:
        while True:
            state = pos.getPosition()
            now = time.monotonic()

            if now - last_post >= post_interval:
                try:
                    r = session.post(post_url, json=state, timeout=2)
                    if r.status_code != 200 and now - last_warn >= 5.0:
                        print(f"POST status {r.status_code}: {r.text[:80]}")
                        last_warn = now
                except requests.exceptions.RequestException as e:
                    if now - last_warn >= 5.0:
                        print(f"POST failed: {e}")
                        last_warn = now
                last_post = now

            if now - last_print >= 1.0:
                print(f"roll {state['roll']:7.1f}  pitch {state['pitch']:7.1f}  yaw {state['yaw']:7.1f}  |  "
                      f"alt {state['alt_m']:+7.1f} m  |  {state['pressure_hpa']:.1f} hPa")
                last_print = now

            time.sleep(0.005)
    except KeyboardInterrupt:
        print("Tracking stopped.")
