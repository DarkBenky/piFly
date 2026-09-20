import numpy as np

EARTH_RADIUS_M = 6371000.0
GRAVITY = 9.80665


def bucket(t, v, dt, method="mean"):
    import pandas as pd

    if len(t) == 0:
        return np.array([]), np.array([])
    series = pd.Series(np.asarray(v, dtype="f8"),
                       index=pd.to_timedelta(np.asarray(t, dtype="f8"), unit="s"))
    resampled = series.resample(pd.Timedelta(dt, unit="s"))
    out = getattr(resampled, method)()
    out = out.dropna()
    return out.index.total_seconds().to_numpy(), out.to_numpy()


def movavg(v, n):
    v = np.asarray(v, dtype="f8")
    if n <= 1 or len(v) == 0:
        return v.copy()
    n = min(int(n), len(v))
    kernel = np.ones(n) / n
    return np.convolve(v, kernel, mode="same")


def ema(v, alpha):
    v = np.asarray(v, dtype="f8")
    if len(v) == 0:
        return v.copy()
    alpha = float(min(max(alpha, 1e-6), 1.0))
    weights = (1 - alpha) ** np.arange(len(v))[::-1]
    return alpha * np.convolve(v, weights, mode="full")[:len(v)]


def lowpass(v, fs, fc, order=2):
    from scipy import signal

    b, a = signal.butter(order, min(fc / (fs / 2.0), 0.99), btype="low")
    return signal.filtfilt(b, a, np.asarray(v, dtype="f8"))


def highpass(v, fs, fc, order=2):
    from scipy import signal

    b, a = signal.butter(order, min(fc / (fs / 2.0), 0.99), btype="high")
    return signal.filtfilt(b, a, np.asarray(v, dtype="f8"))


def integrate(t, v, v0=0.0):
    t = np.asarray(t, dtype="f8")
    v = np.asarray(v, dtype="f8")
    if len(t) < 2:
        return np.full(len(t), v0)
    out = np.concatenate(([v0], v0 + np.cumsum(np.diff(t) * (v[:-1] + v[1:]) / 2.0)))
    return out


def gps_to_enu(lat, lon, lat0, lon0):
    lat = np.asarray(lat, dtype="f8")
    lon = np.asarray(lon, dtype="f8")
    east = np.radians(lon - lon0) * np.cos(np.radians(lat0)) * EARTH_RADIUS_M
    north = np.radians(lat - lat0) * EARTH_RADIUS_M
    return east, north


def enu_to_gps(east, north, lat0, lon0):
    lat = lat0 + np.degrees(np.asarray(north, dtype="f8") / EARTH_RADIUS_M)
    lon = lon0 + np.degrees(np.asarray(east, dtype="f8") / (EARTH_RADIUS_M * np.cos(np.radians(lat0))))
    return lat, lon


def pressure_to_alt(pressure_hpa, p0_hpa):
    return 44330.0 * (1.0 - (np.asarray(pressure_hpa, dtype="f8") / p0_hpa) ** (1.0 / 5.255))


def tilt_from_accel(ax, ay, az):
    ax, ay, az = (np.asarray(v, dtype="f8") for v in (ax, ay, az))
    roll = np.arctan2(ay, az)
    pitch = np.arctan2(-ax, np.sqrt(ay * ay + az * az))
    return roll, pitch


def complementary(ax, ay, az, gx, gy, gz, t, alpha=0.98):
    roll, pitch = tilt_from_accel(ax, ay, az)
    gx, gy, gz = (np.asarray(v, dtype="f8") for v in (gx, gy, gz))
    t = np.asarray(t, dtype="f8")
    dt = np.diff(t, prepend=t[0] if len(t) else 0.0)
    r = np.zeros(len(t))
    p = np.zeros(len(t))
    y = np.zeros(len(t))
    for i in range(1, len(t)):
        r[i] = alpha * (r[i - 1] + gx[i] * dt[i]) + (1 - alpha) * roll[i]
        p[i] = alpha * (p[i - 1] + gy[i] * dt[i]) + (1 - alpha) * pitch[i]
        y[i] = y[i - 1] + gz[i] * dt[i]
    return r, p, y


def mahony(ax, ay, az, gx, gy, gz, t, kp=0.5, ki=0.05):
    ax, ay, az, gx, gy, gz = (np.asarray(v, dtype="f8") for v in (ax, ay, az, gx, gy, gz))
    t = np.asarray(t, dtype="f8")
    n = len(t)
    q = np.array([1.0, 0.0, 0.0, 0.0])
    integral = np.zeros(3)
    roll = np.zeros(n)
    pitch = np.zeros(n)
    yaw = np.zeros(n)
    for i in range(n):
        dt = t[i] - t[i - 1] if i else 0.0
        accel = np.array([ax[i], ay[i], az[i]])
        gyro = np.array([gx[i], gy[i], gz[i]])
        norm = np.linalg.norm(accel)
        if dt > 0 and norm > 0.1:
            accel = accel / norm
            v = np.array([
                2 * (q[1] * q[3] - q[0] * q[2]),
                2 * (q[0] * q[1] + q[2] * q[3]),
                q[0] * q[0] - q[1] * q[1] - q[2] * q[2] + q[3] * q[3],
            ])
            error = np.cross(accel, v)
            integral += ki * error * dt
            gyro = gyro + kp * error + integral
            q = q + 0.5 * np.array([
                -q[1] * gyro[0] - q[2] * gyro[1] - q[3] * gyro[2],
                q[0] * gyro[0] + q[2] * gyro[2] - q[3] * gyro[1],
                q[0] * gyro[1] - q[1] * gyro[2] + q[3] * gyro[0],
                q[0] * gyro[2] + q[1] * gyro[1] - q[2] * gyro[0],
            ]) * dt
            q = q / np.linalg.norm(q)
        roll[i], pitch[i], yaw[i] = quat_to_euler(q)
    return roll, pitch, yaw


def quat_to_euler(q):
    w, x, y, z = q
    roll = np.arctan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    pitch = np.arcsin(np.clip(2 * (w * y - z * x), -1.0, 1.0))
    yaw = np.arctan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return roll, pitch, yaw


def body_to_world(vx, vy, vz, roll, pitch, yaw):
    cr, sr = np.cos(roll), np.sin(roll)
    cp, sp = np.cos(pitch), np.sin(pitch)
    cy, sy = np.cos(yaw), np.sin(yaw)
    wx = cy * cp * vx + (cy * sp * sr - sy * cr) * vy + (cy * sp * cr + sy * sr) * vz
    wy = sy * cp * vx + (sy * sp * sr + cy * cr) * vy + (sy * sp * cr - cy * sr) * vz
    wz = -sp * vx + cp * sr * vy + cp * cr * vz
    return wx, wy, wz


def remove_gravity(ax, ay, az, roll, pitch):
    gx = -GRAVITY * np.sin(pitch)
    gy = GRAVITY * np.sin(roll) * np.cos(pitch)
    gz = GRAVITY * np.cos(roll) * np.cos(pitch)
    return ax - gx, ay - gy, az - gz


def zupt_mask(ax, ay, az, gx, gy, gz, accel_tol=0.15, gyro_tol=0.05):
    a = np.sqrt(np.asarray(ax) ** 2 + np.asarray(ay) ** 2 + np.asarray(az) ** 2)
    w = np.sqrt(np.asarray(gx) ** 2 + np.asarray(gy) ** 2 + np.asarray(gz) ** 2)
    return (np.abs(a - GRAVITY) < accel_tol) & (w < gyro_tol)
