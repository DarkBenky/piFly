from location import getBaselinePressure, getBaselineIMU

from temperature import BME280
from imu import IMU
from gps import GPS

import json
import os
import numpy as np
import time
import math
import heapq
import asyncio
from datetime import datetime

from filterpy.kalman import ExtendedKalmanFilter as EKF
from dataclasses import dataclass, asdict

BAROMETRIC_M_PER_HPA = 8.43
EARTH_RADIUS_M = 6371000.0

@dataclass
class Position:
    latitude: float
    longitude: float
    altitude: float

@dataclass
class SensorEvent:
    t: float
    kind: str          # "imu" | "gps" | "bme"
    data: dict

class Model:
    def __init__(self, imu: IMU, bme: BME280, gps: GPS):
        self.imu = imu
        self.imu_tps = 1_000
        self.imu_last_reading = None

        self.bme = bme
        self.bme_tps = 10
        self.bme_last_reading = None

        self.gps = gps
        self.gps_tps = 10
        self.gps_last_reading = None
        self.basePosition = self.GetBasePosition(samples=10)

        self.event_queue = asyncio.Queue()

        self.imu_stat = getBaselineIMU(5_000, imu)
        self.pressure_stat = getBaselinePressure(5_000, bme)

        n_states = 12
        self.state_vector = np.zeros(n_states)
        # [px, py, pz,            - position (local ENU meters)
        #  vx, vy, vz,            - velocity
        #  bax, bay, baz,         - accelerometer bias
        #  bgx, bgy, bgz]         - gyroscope bias
        self.P = np.eye(n_states) * 10.0
        self.P[6:9, 6:9] *= 5   # accel bias starts more uncertain
        self.P[9:12, 9:12] *= 5 # gyro bias starts more uncertain

        # Q: process noise. imu_stat gives you per-axis measurement noise (std),
        # which sets a floor on accel/gyro process noise -- NOT bias random-walk
        # rate (that's a separate quantity this baseline doesn't measure; a fixed
        # small constant is a reasonable placeholder until you tune against real
        # GPS-corrected trajectories).
        self.Q = np.eye(n_states) * 0.01
        self.Q[0:3, 0:3] = np.eye(3) * (self.imu_stat.xStdAcc ** 2)  # rough: position process noise from accel noise
        self.Q[3:6, 3:6] = np.diag([
            self.imu_stat.xStdAcc ** 2,
            self.imu_stat.yStdAcc ** 2,
            self.imu_stat.zStdAcc ** 2,
        ])  # velocity process noise from accel noise
        self.Q[6:9, 6:9] = np.eye(3) * 1e-4   # accel bias walk rate -- placeholder, tune empirically
        self.Q[9:12, 9:12] = np.eye(3) * 1e-4  # gyro bias walk rate -- placeholder, tune empirically

        # R_gps: GPS position measurement noise -- from your GPS module's datasheet, not from these baselines
        self.R_gps = np.eye(3) * 4.0

        # R_baro: convert pressure std (hPa) to altitude std (m) via barometric
        # formula sensitivity, then square for variance
        alt_std = self.pressure_stat.stdPressure * BAROMETRIC_M_PER_HPA  # see note below
        self.R_baro = np.array([[alt_std ** 2]])

        # 12-state EKF: position(3), velocity(3), accel bias(3), gyro bias(3).
        # Measurement vector is always 3-D (local position); GPS drives all three
        # axes, baro only drives z (its x/y are de-weighted via a huge R).
        self.ekf = EKF(dim_x=n_states, dim_z=3)
        self.ekf.x = np.array(self.state_vector, dtype=float)
        self.ekf.P = np.array(self.P, dtype=float)
        self.ekf.Q = np.array(self.Q, dtype=float)
        self.ekf.R = np.array(self.R_gps, dtype=float)
        self.ekf.hx = self._measurement

        self._H_gps = np.zeros((3, n_states))
        self._H_gps[0, 0] = self._H_gps[1, 1] = self._H_gps[2, 2] = 1.0
        self.ekf.H = self._H_gps.copy()

        self._origin_set = self.basePosition is not None
        if self._origin_set:
            self.origin_lat = self.basePosition.latitude
            self.origin_lon = self.basePosition.longitude
            self.origin_alt = self.basePosition.altitude

        self.last_predict_t = None
        self.initialized = False

    def GetBasePosition(self, samples = 10):
        altitudes = []
        latitudes = []
        longitudes = []
        for i in range(samples):
            data = self.gps.read()
            if data is None:
                continue
            lat = data.get("lat")
            lon = data.get("lon")
            alt = data.get("alt_m")
            if lat is None or lon is None or alt is None:
                continue
            altitudes.append(alt)
            latitudes.append(lat)
            longitudes.append(lon)

        if not latitudes:
            return None

        return Position(
            latitude = sum(latitudes) / len(latitudes),
            longitude = sum(longitudes) / len(longitudes),
            altitude = sum(altitudes) / len(altitudes)
        )

    def _log(self, data, path):
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        # IMU returns a dataclass (not JSON-serializable) -> convert to dict
        if hasattr(data, "__dataclass_fields__"):
            data = asdict(data)
        record = {"timestamp": datetime.now().isoformat()}
        record.update(data)
        with open(path, "a") as f:
            f.write(json.dumps(record) + "\n")

    async def _imu_loop(self, log=False):
        period = 1.0 / self.imu_tps
        while True:
            reading = self.imu.get_record()
            await self.event_queue.put(SensorEvent(time.monotonic(), "imu", reading))
            if log:
                self._log(reading, "./logs/imu.json")
            await asyncio.sleep(period)

    async def _bme_loop(self, log=False):
        period = 1.0 / self.bme_tps
        while True:
            reading = self.bme.get_record()
            await self.event_queue.put(SensorEvent(time.monotonic(), "bme", reading))
            if log:
                self._log(reading, "./logs/baro.json")
            await asyncio.sleep(period)

    async def _gps_loop(self, log=False):
        period = 1.0 / self.gps_tps
        while True:
            reading = self.gps.read()
            if reading is not None:
                await self.event_queue.put(SensorEvent(time.monotonic(), "gps", reading))
                if log:
                    self._log(reading, "./logs/gps.json")
            await asyncio.sleep(period)

    async def _fusion_loop(self):
        while True:
            event = await self.event_queue.get()

            # state prediction is driven by IMU only (highest rate);
            # gps/baro only trigger updates
            if event.kind == "imu":
                if self.last_predict_t is not None:
                    dt = event.t - self.last_predict_t
                    if dt > 0:
                        self.predictStep(dt, event)
                self.last_predict_t = event.t
            self.initialized = True

            if event.kind == "gps":
                self.gpsUpdate(event.data)
            elif event.kind == "bme":
                self.baroUpdate(event.data)


    def _measurement(self, x):
        # measurement = local position [px, py, pz]
        return x[0:3]

    def _geodetic_to_enu(self, lat: float, lon: float, alt: float):
        dlat = math.radians(lat - self.origin_lat)
        dlon = math.radians(lon - self.origin_lon)
        east = dlon * EARTH_RADIUS_M * math.cos(math.radians(self.origin_lat))
        north = dlat * EARTH_RADIUS_M
        up = alt - self.origin_alt
        return east, north, up

    def predictStep(self, dt: float, event: SensorEvent):
        data = event.data

        # specific force in the local frame; subtract the gravity vector that
        # the stationary baseline captured (assumes a level platform -- a real
        # INS would rotate gravity by the current attitude)
        gravity = np.array([
            self.imu_stat.xAcceleration,
            self.imu_stat.yAcceleration,
            self.imu_stat.zAcceleration,
        ])
        a = np.array([
            data.xAcceleration,
            data.yAcceleration,
            data.zAcceleration,
        ]) - gravity
        a -= self.ekf.x[6:9]  # remove estimated accelerometer bias

        # F: p += v*dt - 0.5*b_a*dt^2 ; v += -b_a*dt   (bias random-walks)
        F = np.eye(12)
        F[0:3, 3:6] = dt * np.eye(3)
        F[0:3, 6:9] = -0.5 * dt * dt * np.eye(3)
        F[3:6, 6:9] = -dt * np.eye(3)

        # B: control input is the (bias-corrected) measured acceleration
        B = np.zeros((12, 3))
        B[0:3, 0:3] = 0.5 * dt * dt * np.eye(3)
        B[3:6, 0:3] = dt * np.eye(3)

        self.ekf.predict(u=a, B=B, F=F)

    def gpsUpdate(self, gps_data: dict):
        lat = gps_data.get("lat")
        lon = gps_data.get("lon")
        alt = gps_data.get("alt_m")
        if lat is None or lon is None or alt is None:
            return

        # first valid fix becomes the local origin if we never found one
        if not self._origin_set:
            self.origin_lat = lat
            self.origin_lon = lon
            self.origin_alt = alt
            self._origin_set = True

        east, north, up = self._geodetic_to_enu(lat, lon, alt)
        z = np.array([east, north, up])
        self.ekf.update(z, self.R_gps, self._H_gps)

    def baroUpdate(self, bme_data: dict):
        # local altitude relative to the base pressure (positive up)
        alt_local = (self.pressure_stat.pressure - bme_data["bme_pressure_hpa"]) * BAROMETRIC_M_PER_HPA

        H_baro = np.zeros((3, 12))
        H_baro[2, 2] = 1.0
        z = np.array([self.ekf.x[0], self.ekf.x[1], alt_local])
        R = np.diag([1e6, 1e6, self.R_baro[0, 0]])
        self.ekf.update(z, R, H_baro)

    async def _console_loop(self, rate: float = 10.0):
        period = 1.0 / rate
        print("piFly nav -- press Ctrl+C to stop")
        print("  pos=[x, y, z] (m, local ENU)   vel=[vx, vy, vz] (m/s)")
        while True:
            x = self.ekf.x
            print(
                f"pos=[{x[0]:7.2f}, {x[1]:7.2f}, {x[2]:7.2f}]  "
                f"vel=[{x[3]:6.2f}, {x[4]:6.2f}, {x[5]:6.2f}]  "
                f"init={self.initialized}"
            )
            await asyncio.sleep(period)

    async def run(self, log: bool = False):
        tasks = [
            asyncio.create_task(self._imu_loop(log=log)),
            asyncio.create_task(self._bme_loop(log=log)),
            asyncio.create_task(self._gps_loop(log=log)),
            asyncio.create_task(self._fusion_loop()),
            asyncio.create_task(self._console_loop()),
        ]
        await asyncio.gather(*tasks)

if __name__ == "__main__":
    model = Model(IMU(), BME280(), GPS())
    asyncio.run(model.run())