import json
import os
import time

import requests

from imu import IMU
from temperature import BME280
from navigation import MahonyFilter, quat_to_euler, pressure_to_altitude
from location import IMU_STATIC_OFFSETS, PRESSURE_STATIC_OFFSET

LOCATION_UI_URL = "http://91.98.145.193/api/ingest"


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
    imu = IMU()
    bme = BME280()
    filt = MahonyFilter(imu_stat)

    acc_bias = (imu_stat.xAcceleration, imu_stat.yAcceleration,
                imu_stat.zAcceleration - 9.80665)
    mag_bias = (imu_stat.xMagneto, imu_stat.yMagneto, imu_stat.zMagneto)

    session = requests.Session()
    state = None
    t_prev = time.monotonic()
    last_bme = 0.0
    last_post = 0.0
    last_print = 0.0

    print(f"Streaming attitude + altitude -> {post_url}")
    try:
        while True:
            rec = imu.get_record()
            now = time.monotonic()
            dt = min(max(now - t_prev, 1e-4), 0.05)
            t_prev = now

            filt.update(
                rec.xGyro, rec.yGyro, rec.zGyro,
                rec.xAcceleration - acc_bias[0],
                rec.yAcceleration - acc_bias[1],
                rec.zAcceleration - acc_bias[2],
                rec.xMagneto - mag_bias[0],
                rec.yMagneto - mag_bias[1],
                rec.zMagneto - mag_bias[2],
                dt,
            )
            roll, pitch, yaw = quat_to_euler(filt.q)

            if now - last_bme >= 0.5:
                b = bme.get_record()
                state = {
                    "timestamp": time.time(),
                    "roll": roll, "pitch": pitch, "yaw": yaw,
                    "qw": filt.q[0], "qx": filt.q[1], "qy": filt.q[2], "qz": filt.q[3],
                    "x_m": 0.0, "y_m": 0.0,
                    "alt_m": pressure_to_altitude(b["bme_pressure_hpa"], pressure_stat.pressure,
                                                  temp_c=b["bme_temp_c"]),
                    "pressure_hpa": b["bme_pressure_hpa"],
                    "temp_c": b["bme_temp_c"],
                }
                last_bme = now

            if state is not None and now - last_post >= post_interval:
                try:
                    session.post(post_url, json=state, timeout=2)
                except requests.exceptions.RequestException as e:
                    print(f"POST failed: {e}")
                last_post = now

            if state is not None and now - last_print >= 1.0:
                print(f"roll {roll:7.1f}  pitch {pitch:7.1f}  yaw {yaw:7.1f}  |  "
                      f"alt {state['alt_m']:+7.1f} m  |  {state['pressure_hpa']:.1f} hPa")
                last_print = now

            time.sleep(0.005)
    except KeyboardInterrupt:
        print("Tracking stopped.")
