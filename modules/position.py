import time
from dataclasses import dataclass

from imu import IMU
from temperature import BME280
from navigation import MahonyFilter, quat_to_euler, pressure_to_altitude, rotate_vector
from location import IMU_STATIC_OFFSETS, PRESSURE_STATIC_OFFSET


@dataclass
class PosDir:
    x: float
    y: float
    z: float
    dx: float
    dy: float
    dz: float
    upX: float
    upY: float
    upZ: float


def stateToPosDir(state: dict) -> PosDir:
    q = (state["qw"], state["qx"], state["qy"], state["qz"])
    fwd = rotate_vector(q, (1.0, 0.0, 0.0))
    up = rotate_vector(q, (0.0, 0.0, 1.0))
    return PosDir(state["x_m"], state["y_m"], state["z_m"], *fwd, *up)


class Position:
    def __init__(self, imu_stat: IMU_STATIC_OFFSETS, pressure_stat: PRESSURE_STATIC_OFFSET,
                 imu: IMU = None, bme: BME280 = None,
                 alt_smoothing: float = 0.15, bme_interval: float = 0.5):
        self.imu_stat = imu_stat
        self.pressure_stat = pressure_stat
        self.imu = imu if imu is not None else IMU()
        self.bme = bme if bme is not None else BME280()
        self.alt_smoothing = alt_smoothing
        self.bme_interval = bme_interval

        self.filt = MahonyFilter(imu_stat)
        self.acc_bias = (imu_stat.xAcceleration, imu_stat.yAcceleration,
                         imu_stat.zAcceleration - 9.80665)
        self.mag_bias = (imu_stat.xMagneto, imu_stat.yMagneto, imu_stat.zMagneto)

        self._last_t = None
        self._last_bme = None
        self._alt = None
        self._bme = None

    def reset(self):
        self.filt = MahonyFilter(self.imu_stat)
        self._last_t = None
        self._last_bme = None
        self._alt = None
        self._bme = None

    def update(self) -> dict:
        rec = self.imu.get_record()
        now = time.monotonic()
        if self._last_t is None:
            dt = 0.01
        else:
            dt = min(max(now - self._last_t, 1e-4), 0.05)
        self._last_t = now

        self.filt.update(
            rec.xGyro, rec.yGyro, rec.zGyro,
            rec.xAcceleration - self.acc_bias[0],
            rec.yAcceleration - self.acc_bias[1],
            rec.zAcceleration - self.acc_bias[2],
            rec.xMagneto - self.mag_bias[0],
            rec.yMagneto - self.mag_bias[1],
            rec.zMagneto - self.mag_bias[2],
            dt,
        )
        roll, pitch, yaw = quat_to_euler(self.filt.q)

        if self._last_bme is None or now - self._last_bme >= self.bme_interval:
            b = self.bme.get_record()
            p = b["bme_pressure_hpa"]
            if 300.0 < p < 1100.0:
                alt_raw = pressure_to_altitude(p, self.pressure_stat.pressure,
                                               temp_c=b["bme_temp_c"])
                if self._alt is None:
                    self._alt = alt_raw
                elif abs(alt_raw - self._alt) < 5.0:
                    self._alt += self.alt_smoothing * (alt_raw - self._alt)
                self._bme = b
            self._last_bme = now

        q = self.filt.q
        alt = self._alt if self._alt is not None else 0.0
        return {
            "timestamp": time.time(),
            "qw": q[0], "qx": q[1], "qy": q[2], "qz": q[3],
            "roll": roll, "pitch": pitch, "yaw": yaw,
            "x_m": 0.0, "y_m": 0.0, "z_m": alt,
            "alt_m": alt,
            "pressure_hpa": self._bme["bme_pressure_hpa"] if self._bme else 0.0,
            "temp_c": self._bme["bme_temp_c"] if self._bme else 0.0,
            "humidity_pct": self._bme["bme_humidity_pct"] if self._bme else 0.0,
        }

    def getPosition(self) -> dict:
        return self.update()

    def getPosDir(self) -> PosDir:
        return stateToPosDir(self.update())


if __name__ == "__main__":
    from location import getBaselineIMU, getBaselinePressure, BASELINE_SAMPLES

    imu = IMU()
    bme = BME280()

    pressure_stat = getBaselinePressure(BASELINE_SAMPLES, bme)
    imu_stat = getBaselineIMU(BASELINE_SAMPLES, imu)

    pos = Position(imu_stat, pressure_stat, imu, bme)
    try:
        while True:
            state = pos.getPosition()
            pd = pos.getPosDir()
            print(f"roll {state['roll']:7.1f}  pitch {state['pitch']:7.1f}  yaw {state['yaw']:7.1f}  |  "
                  f"pos ({pd.x:+.1f}, {pd.y:+.1f}, {pd.z:+.1f})  "
                  f"dir ({pd.dx:+.2f}, {pd.dy:+.2f}, {pd.dz:+.2f})")
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("Stopped.")
