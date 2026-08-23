import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import navigation as nav


class _Stat:
    def __init__(self, xGyro=0.0, yGyro=0.0, zGyro=0.0):
        self.xGyro = xGyro
        self.yGyro = yGyro
        self.zGyro = zGyro


def _norm(q):
    return math.sqrt(sum(c * c for c in q))


class TestQuatToEuler(unittest.TestCase):
    def test_identity(self):
        r, p, y = nav.quat_to_euler([1.0, 0.0, 0.0, 0.0])
        self.assertAlmostEqual(r, 0.0, places=9)
        self.assertAlmostEqual(p, 0.0, places=9)
        self.assertAlmostEqual(y, 0.0, places=9)

    def test_yaw_90(self):
        c = math.cos(math.pi / 4)
        s = math.sin(math.pi / 4)
        r, p, y = nav.quat_to_euler([c, 0.0, 0.0, s])
        self.assertAlmostEqual(r, 0.0, places=6)
        self.assertAlmostEqual(p, 0.0, places=6)
        self.assertAlmostEqual(y, 90.0, places=6)

    def test_roll_90(self):
        c = math.cos(math.pi / 4)
        s = math.sin(math.pi / 4)
        r, p, y = nav.quat_to_euler([c, s, 0.0, 0.0])
        self.assertAlmostEqual(r, 90.0, places=6)
        self.assertAlmostEqual(p, 0.0, places=6)
        self.assertAlmostEqual(y, 0.0, places=6)

    def test_pitch_clamped(self):
        c = math.cos(math.pi / 4)
        s = math.sin(math.pi / 4)
        r, p, y = nav.quat_to_euler([c, 0.0, s, 0.0])
        self.assertAlmostEqual(p, 90.0, places=6)
        self.assertTrue(-90.0 <= p <= 90.0)

    def test_pitch_never_nan_extreme(self):
        r, p, y = nav.quat_to_euler([0.7071067811865476, 0.0, 0.7071067811865476, 0.0])
        self.assertFalse(math.isnan(p))


class TestRotateVector(unittest.TestCase):
    def test_identity(self):
        v = nav.rotate_vector([1.0, 0.0, 0.0, 0.0], (1.0, 2.0, 3.0))
        for a, b in zip(v, (1.0, 2.0, 3.0)):
            self.assertAlmostEqual(a, b, places=9)

    def test_yaw_90_x_to_y(self):
        c = math.cos(math.pi / 4)
        s = math.sin(math.pi / 4)
        v = nav.rotate_vector([c, 0.0, 0.0, s], (1.0, 0.0, 0.0))
        self.assertAlmostEqual(v[0], 0.0, places=9)
        self.assertAlmostEqual(v[1], 1.0, places=9)
        self.assertAlmostEqual(v[2], 0.0, places=9)

    def test_roll_90_y_to_z(self):
        c = math.cos(math.pi / 4)
        s = math.sin(math.pi / 4)
        v = nav.rotate_vector([c, s, 0.0, 0.0], (0.0, 1.0, 0.0))
        self.assertAlmostEqual(v[0], 0.0, places=9)
        self.assertAlmostEqual(v[1], 0.0, places=9)
        self.assertAlmostEqual(v[2], 1.0, places=9)

    def test_preserves_length(self):
        c = math.cos(0.3)
        s = math.sin(0.3)
        q = [c, 0.0, s, 0.0]
        v = (1.0, -2.0, 0.5)
        out = nav.rotate_vector(q, v)
        self.assertAlmostEqual(_norm(out), _norm(v), places=9)


class TestIntegrateGyro(unittest.TestCase):
    def test_zero_rate_identity(self):
        q = nav.integrate_gyro([1.0, 0.0, 0.0, 0.0], 0.0, 0.0, 0.0, 0.01)
        self.assertAlmostEqual(q[0], 1.0, places=9)
        self.assertAlmostEqual(q[1], 0.0, places=9)
        self.assertAlmostEqual(q[2], 0.0, places=9)
        self.assertAlmostEqual(q[3], 0.0, places=9)

    def test_unit_norm(self):
        q = nav.integrate_gyro([1.0, 0.0, 0.0, 0.0], 0.5, -0.3, 0.2, 0.01)
        self.assertAlmostEqual(_norm(q), 1.0, places=9)

    def test_stays_unit_norm_over_time(self):
        q = [1.0, 0.0, 0.0, 0.0]
        for _ in range(1000):
            q = nav.integrate_gyro(q, 0.4, -0.2, 0.1, 0.01)
        self.assertAlmostEqual(_norm(q), 1.0, places=6)

    def test_full_revolution_returns_home(self):
        q = [1.0, 0.0, 0.0, 0.0]
        rate = 2 * math.pi
        dt = 1.0 / 2000.0
        for _ in range(2000):
            q = nav.integrate_gyro(q, 0.0, 0.0, rate, dt)
        self.assertAlmostEqual(abs(q[0]), 1.0, places=3)


class TestPressureToAltitude(unittest.TestCase):
    def test_baseline_zero(self):
        a = nav.pressure_to_altitude(1013.25, 1013.25, temp_c=15.0)
        self.assertAlmostEqual(a, 0.0, places=6)

    def test_lower_pressure_positive(self):
        a = nav.pressure_to_altitude(1000.0, 1013.25, temp_c=15.0)
        self.assertGreater(a, 0.0)

    def test_higher_pressure_negative(self):
        a = nav.pressure_to_altitude(1020.0, 1013.25, temp_c=15.0)
        self.assertLess(a, 0.0)

    def test_monotonic(self):
        a_high = nav.pressure_to_altitude(950.0, 1013.25, temp_c=15.0)
        a_low = nav.pressure_to_altitude(1000.0, 1013.25, temp_c=15.0)
        self.assertGreater(a_high, a_low)

    def test_finite(self):
        a = nav.pressure_to_altitude(500.0, 1013.25, temp_c=15.0)
        self.assertTrue(math.isfinite(a))


class TestMahonyFilter(unittest.TestCase):
    def test_initial_state(self):
        f = nav.MahonyFilter(_Stat())
        self.assertEqual(f.q, [1.0, 0.0, 0.0, 0.0])

    def test_unit_norm_after_updates(self):
        f = nav.MahonyFilter(_Stat())
        for _ in range(200):
            f.update(0, 0, 0, 0, 0, 9.80665, 20, 0, 40, 0.01)
        self.assertAlmostEqual(_norm(f.q), 1.0, places=6)

    def test_finite_after_updates(self):
        f = nav.MahonyFilter(_Stat())
        for _ in range(200):
            f.update(0.1, -0.1, 0.05, 0.2, 0.1, 9.7, 18, 2, 41, 0.01)
        self.assertTrue(all(math.isfinite(c) for c in f.q))

    def test_zero_input_stays_identity(self):
        f = nav.MahonyFilter(_Stat())
        for _ in range(50):
            f.update(0, 0, 0, 0, 0, 9.80665, 20, 0, 40, 0.01)
        self.assertAlmostEqual(f.q[0], 1.0, places=3)

    def test_handles_zero_accel_mag(self):
        f = nav.MahonyFilter(_Stat())
        f.update(0, 0, 0, 0, 0, 0, 0, 0, 0, 0.01)
        self.assertTrue(all(math.isfinite(c) for c in f.q))
        self.assertAlmostEqual(_norm(f.q), 1.0, places=6)

    def test_gyro_bias_subtracted(self):
        f = nav.MahonyFilter(_Stat(xGyro=0.5, yGyro=0.5, zGyro=0.5))
        for _ in range(50):
            f.update(0.5, 0.5, 0.5, 0, 0, 9.80665, 20, 0, 40, 0.01)
        self.assertAlmostEqual(f.q[0], 1.0, places=2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
