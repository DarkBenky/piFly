import os
import sys
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _install_stubs():
    for name in ("board", "digitalio", "adafruit_icm20x"):
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)
    if "adafruit_bme280" not in sys.modules:
        pkg = types.ModuleType("adafruit_bme280")
        basic = types.ModuleType("adafruit_bme280.basic")
        pkg.basic = basic
        sys.modules["adafruit_bme280"] = pkg
        sys.modules["adafruit_bme280.basic"] = basic
    try:
        import numpy  # noqa: F401
    except ModuleNotFoundError:
        sys.modules["numpy"] = types.ModuleType("numpy")


_install_stubs()

from location import IMU_STATIC_OFFSETS, validateIMUBaseline, CalibrationError  # noqa: E402


def _good():
    return IMU_STATIC_OFFSETS(
        0.0, 0.0, 9.80665,
        0.0, 0.0, 0.0,
        20.0, 0.0, 40.0,
        0.01, 0.01, 0.01,
        0.001, 0.001, 0.001,
        0.1, 0.1, 0.1,
    )


class TestValidateIMUBaseline(unittest.TestCase):
    def test_good_returns_same_object(self):
        s = _good()
        self.assertIs(validateIMUBaseline(s), s)

    def test_negative_std_rejected(self):
        s = _good()
        s.xStdGyro = -31.0
        with self.assertRaises(CalibrationError):
            validateIMUBaseline(s)

    def test_gravity_in_gyro_slot_rejected(self):
        s = _good()
        s.yGyro = 9.81
        with self.assertRaises(CalibrationError):
            validateIMUBaseline(s)

    def test_nan_rejected(self):
        s = _good()
        s.xAcceleration = float("nan")
        with self.assertRaises(CalibrationError):
            validateIMUBaseline(s)

    def test_inf_rejected(self):
        s = _good()
        s.zMagneto = float("inf")
        with self.assertRaises(CalibrationError):
            validateIMUBaseline(s)

    def test_moving_gyro_noise_rejected(self):
        s = _good()
        s.yStdGyro = 0.75
        with self.assertRaises(CalibrationError):
            validateIMUBaseline(s)

    def test_moving_accel_noise_rejected(self):
        s = _good()
        s.zStdAcc = 1.5
        with self.assertRaises(CalibrationError):
            validateIMUBaseline(s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
