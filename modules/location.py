from imu import IMU, IMU_OUT
from temperature import BME280
from dataclasses import dataclass
from pprint import pprint
import numpy
import time
import json
import os

BASELINE_SAMPLES = 1_000
LOGS_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")

@dataclass
class IMU_STATIC_OFFSETS:
    xAcceleration: float
    xStdAcc: float
    yAcceleration: float
    yStdAcc: float
    zAcceleration: float
    zStdAcc: float

    xGyro: float
    xStdGyro: float
    yGyro: float
    yStdGyro: float
    zGyro: float
    zStdGyro: float

    xMagneto: float
    xStdMag: float
    yMagneto: float
    yStdMag: float
    zMagneto: float
    zStdMag: float

    def __init__(self,
                 xAcceleration: float, yAcceleration: float, zAcceleration: float,
                 xGyro: float, yGyro: float, zGyro: float,
                 xMagneto: float, yMagneto: float, zMagneto: float,

                 xStdAcc: float, yStdAcc: float, zStdAcc: float,
                 xStdGyro: float, yStdGyro: float, zStdGyro: float,
                 xStdMag: float, yStdMag: float, zStdMag: float
                 ):
        self.xAcceleration = xAcceleration
        self.yAcceleration = yAcceleration
        self.zAcceleration = zAcceleration
        self.xGyro = xGyro
        self.yGyro = yGyro
        self.zGyro = zGyro
        self.xMagneto = xMagneto
        self.yMagneto = yMagneto
        self.zMagneto = zMagneto

        self.xStdAcc = xStdAcc
        self.yStdAcc = yStdAcc
        self.zStdAcc = zStdAcc
        self.xStdGyro = xStdGyro
        self.yStdGyro = yStdGyro
        self.zStdGyro = zStdGyro
        self.xStdMag = xStdMag
        self.yStdMag = yStdMag
        self.zStdMag = zStdMag


@dataclass
class PRESSURE_STATIC_OFFSET:
    pressure: float
    stdPressure: float

    temperature: float
    stdTemperature: float

    def __init__(self, pressure: float, stdPressure: float, temperature: float, stdTemperature: float):
        self.pressure = pressure
        self.stdPressure = stdPressure
        self.temperature = temperature
        self.stdTemperature = stdTemperature

def getBaselineIMU(samples: int, imu: IMU) -> IMU_STATIC_OFFSETS:
    readings = []
    for i in range(samples):
        time.sleep(0.01)
        readings.append(imu.get_record())
        if i % 32 == 0:
            print(f"iter: {i} / {samples}")
            pprint(readings[-1])

    return IMU_STATIC_OFFSETS(
        numpy.mean([r.xAcceleration for r in readings]),
        numpy.mean([r.yAcceleration for r in readings]),
        numpy.mean([r.zAcceleration for r in readings]),
        numpy.mean([r.xGyro for r in readings]),
        numpy.mean([r.yGyro for r in readings]),
        numpy.mean([r.zGyro for r in readings]),
        numpy.mean([r.xMagneto for r in readings]),
        numpy.mean([r.yMagneto for r in readings]),
        numpy.mean([r.zMagneto for r in readings]),

        numpy.std([r.xAcceleration for r in readings]),
        numpy.std([r.yAcceleration for r in readings]),
        numpy.std([r.zAcceleration for r in readings]),
        numpy.std([r.xGyro for r in readings]),
        numpy.std([r.yGyro for r in readings]),
        numpy.std([r.zGyro for r in readings]),
        numpy.std([r.xMagneto for r in readings]),
        numpy.std([r.yMagneto for r in readings]),
        numpy.std([r.zMagneto for r in readings])
    )

def getBaselinePressure(samples: int, bme: BME280) -> PRESSURE_STATIC_OFFSET:
    readings = []
    rejects = 0
    while len(readings) < samples:
        time.sleep(0.05)
        rec = bme.get_record()
        val = rec["bme_pressure_hpa"]
        temp = rec["bme_temp_c"]
        if not 300.0 < val < 1100.0:
            rejects += 1
            continue
        if not 0.0 < temp < 50.0:
            rejects += 1
            continue

        readings.append({"pressure": val, "temperature": temp})
        if len(readings) % 32 == 0:
            print(f"Pressure: {val}, iter: {len(readings)} / {samples}, rejects: {rejects}")

    pressures = [r["pressure"] for r in readings]
    temps = [r["temperature"] for r in readings]

    medPressure = numpy.median(pressures)
    stdPressure = numpy.std(pressures)

    medTemp = numpy.median(temps)
    stdTemp = numpy.std(temps)

    SIGMA = 3.0
    if stdPressure > 0:
        keep = [
            i for i, p in enumerate(pressures)
            if abs(p - medPressure) <= SIGMA * stdPressure
        ]
        cleanPressure = [pressures[i] for i in keep]
        cleanTemp = [temps[i] for i in keep]
    else:
        cleanPressure = pressures
        cleanTemp = temps

    print(f"Baseline: {numpy.median(cleanPressure):.2f} hPa (std {numpy.std(cleanPressure):.3f}), "
          f"temp {numpy.median(cleanTemp):.1f}C (std {numpy.std(cleanTemp):.3f}), "
          f"{len(readings) - len(cleanPressure)} sigma-clipped, {rejects} range-rejected")

    return PRESSURE_STATIC_OFFSET(
        numpy.median(cleanPressure), numpy.std(cleanPressure),
        numpy.median(cleanTemp), numpy.std(cleanTemp),
    )


IMU_STATIC = None
PRESSURE_STATIC = None

if __name__ == "__main__":
    from tracking import track

    imu = IMU()
    bme = BME280()

    PRESSURE_STATIC = getBaselinePressure(BASELINE_SAMPLES, bme)
    IMU_STATIC = getBaselineIMU(BASELINE_SAMPLES, imu)

    pprint(IMU_STATIC)
    pprint(PRESSURE_STATIC)

    os.makedirs(LOGS_DIR, exist_ok=True)
    path = os.path.join(LOGS_DIR, f"baseline_{time.time()}.json")
    with open(path, "w") as f:
        f.write(json.dumps({
            "imu": IMU_STATIC.__dict__,
            "pressure": PRESSURE_STATIC.__dict__
        }))
    print(f"Saved -> {path}")

    track(IMU_STATIC, PRESSURE_STATIC)

    
    