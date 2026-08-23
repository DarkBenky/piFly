import math


class MahonyFilter:
    def __init__(self, stat, kp: float = 0.5, ki: float = 0.0):
        self.q = [1.0, 0.0, 0.0, 0.0]                       # w, x, y, z
        self.b_gyro = [stat.xGyro, stat.yGyro, stat.zGyro]  # static gyro bias
        self.kp, self.ki = kp, ki
        self.e_int = [0.0, 0.0, 0.0]

    def update(self, gx, gy, gz, ax, ay, az, mx, my, mz, dt):
        q0, q1, q2, q3 = self.q

        # normalize accel + mag
        na = math.sqrt(ax * ax + ay * ay + az * az) or 1.0
        ax, ay, az = ax / na, ay / na, az / na
        nm = math.sqrt(mx * mx + my * my + mz * mz) or 1.0
        mx, my, mz = mx / nm, my / nm, mz / nm

        # estimated gravity direction from current quaternion
        vx = 2 * (q1 * q3 - q0 * q2)
        vy = 2 * (q0 * q1 + q2 * q3)
        vz = q0 * q0 - q1 * q1 - q2 * q2 + q3 * q3
        exa = ay * vz - az * vy
        eya = az * vx - ax * vz
        eza = ax * vy - ay * vx

        # estimated magnetic field direction from current quaternion
        hx = 2 * mx * (0.5 - q2 * q2 - q3 * q3) + 2 * my * (q1 * q2 - q0 * q3) + 2 * mz * (q1 * q3 + q0 * q2)
        hy = 2 * mx * (q1 * q2 + q0 * q3) + 2 * my * (0.5 - q1 * q1 - q3 * q3) + 2 * mz * (q2 * q3 - q0 * q1)
        bx = math.sqrt(hx * hx + hy * hy)
        bz = 2 * mx * (q1 * q3 - q0 * q2) + 2 * my * (q2 * q3 + q0 * q1) + 2 * mz * (0.5 - q1 * q1 - q2 * q2)
        wx = 2 * bx * (0.5 - q2 * q2 - q3 * q3) + 2 * bz * (q1 * q3 - q0 * q2)
        wy = 2 * bx * (q1 * q2 - q0 * q3) + 2 * bz * (q0 * q1 + q2 * q3)
        wz = 2 * bx * (q0 * q2 + q1 * q3) + 2 * bz * (0.5 - q1 * q1 - q2 * q2)
        exm = my * wz - mz * wy
        eym = mz * wx - mx * wz
        ezm = mx * wy - my * wx

        # combined error -> integral term -> bias/PI-corrected gyro rates
        ex, ey, ez = exa + exm, eya + eym, eza + ezm
        self.e_int = [e_i + e * dt for e_i, e in zip(self.e_int, (ex, ey, ez))]
        gxc = gx - self.b_gyro[0] + self.kp * ex + self.ki * self.e_int[0]
        gyc = gy - self.b_gyro[1] + self.kp * ey + self.ki * self.e_int[1]
        gzc = gz - self.b_gyro[2] + self.kp * ez + self.ki * self.e_int[2]

        # Quaternion derivative dq/dt = 1/2 * q (x) omega.
        # All four terms MUST come from the same pre-update q0..q3 --
        # compute the deltas first, then apply them together, otherwise
        # later terms end up using already-updated components.
        dq0 = (-q1 * gxc - q2 * gyc - q3 * gzc) * dt / 2
        dq1 = (q0 * gxc + q2 * gzc - q3 * gyc) * dt / 2
        dq2 = (q0 * gyc - q1 * gzc + q3 * gxc) * dt / 2
        dq3 = (q0 * gzc + q1 * gyc - q2 * gxc) * dt / 2

        q0, q1, q2, q3 = q0 + dq0, q1 + dq1, q2 + dq2, q3 + dq3
        n = math.sqrt(q0 * q0 + q1 * q1 + q2 * q2 + q3 * q3) or 1.0
        self.q = [q0 / n, q1 / n, q2 / n, q3 / n]


def quat_to_euler(q):
    w, x, y, z = q
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    # clamp: fp error can push the asin argument slightly past +-1 near +-90 deg pitch
    pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return math.degrees(roll), math.degrees(pitch), math.degrees(yaw)


def pressure_to_altitude(p_hpa, baseline_hpa, temp_c=15.0):
    t_k = temp_c + 273.15
    return (t_k / 0.0065) * (1 - (p_hpa / baseline_hpa) ** (0.0065 * 287.05 / 9.80665))