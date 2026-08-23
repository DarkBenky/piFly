import time
from dataclasses import dataclass

import board

import adafruit_icm20x


@dataclass
class IMU_OUT:
    xAcceleration: float
    yAcceleration: float
    zAcceleration: float

    xGyro: float
    yGyro: float
    zGyro: float

    xMagneto: float
    yMagneto: float
    zMagneto: float

    def __init__(self, 
                 xAcceleration: float, yAcceleration: float, zAcceleration: float,
                 xGyro: float, yGyro: float, zGyro: float,
                 xMagneto: float, yMagneto: float, zMagneto: float):
        self.xAcceleration = xAcceleration
        self.yAcceleration = yAcceleration
        self.zAcceleration = zAcceleration
        self.xGyro = xGyro
        self.yGyro = yGyro
        self.zGyro = zGyro
        self.xMagneto = xMagneto
        self.yMagneto = yMagneto
        self.zMagneto = zMagneto

class IMU:
    def __init__(self):
        self.i2c = board.I2C()
        self.icm = adafruit_icm20x.ICM20948(self.i2c)

    def get_record(self) -> IMU_OUT:
        return IMU_OUT(
            self.icm.acceleration[0], self.icm.acceleration[1], self.icm.acceleration[2],
            self.icm.gyro[0], self.icm.gyro[1], self.icm.gyro[2],
            self.icm.magnetic[0], self.icm.magnetic[1], self.icm.magnetic[2],
        )


if __name__ == "__main__":
    imu = IMU()
    while True:
        rec = imu.get_record()
        print(f"Acceleration: X:{rec.xAcceleration:.2f}, Y: {rec.yAcceleration:.2f}, Z: {rec.zAcceleration:.2f} m/s^2")
        print(f"Gyro X:{rec.xGyro:.2f}, Y: {rec.yGyro:.2f}, Z: {rec.zGyro:.2f} rads/s")
        print(f"Magnetometer X:{rec.xMagneto:.2f}, Y: {rec.yMagneto:.2f}, Z: {rec.zMagneto:.2f} uT")
        print("")
        time.sleep(0.5)