import argparse
import time
from typing import NamedTuple

from smbus2 import SMBus, i2c_msg

REG_BANK_SEL = 0x7F
BANK_0 = 0x00
BANK_2 = 0x20
BANK_3 = 0x60

B0_USER_CTRL = 0x03
B0_PWR_MGMT_1 = 0x05
B0_PWR_MGMT_2 = 0x06
B0_ACCEL_XOUT_H = 0x2D
B0_TEMP_OUT_H = 0x39
B0_EXT_SLV_DATA = 0x3B
B0_FIFO_EN_1 = 0x66
B0_FIFO_EN_2 = 0x67
B0_FIFO_RST = 0x68
B0_FIFO_MODE = 0x69
B0_FIFO_COUNTH = 0x70
B0_FIFO_COUNTL = 0x71
B0_FIFO_R_W = 0x72

B2_GYRO_SMPLRT_DIV = 0x00
B2_GYRO_CONFIG_1 = 0x01
B2_ODR_ALIGN_EN = 0x09
B2_ACCEL_SMPLRT_DIV_1 = 0x10
B2_ACCEL_SMPLRT_DIV_2 = 0x11
B2_ACCEL_CONFIG = 0x14

B3_I2C_MST_CTRL = 0x01
B3_I2C_SLV0_ADDR = 0x03
B3_I2C_SLV0_REG = 0x04
B3_I2C_SLV0_CTRL = 0x05
B3_I2C_SLV0_DO = 0x06

MAG_ADDRESS = 0x0C
MAG_CNTL2 = 0x31
MAG_CNTL3 = 0x32
MAG_DATA_START = 0x11
MAG_MODE_100HZ = 0x08
MAG_READ_BYTES = 9
MAG_SLV_CTRL = 0x80 | MAG_READ_BYTES

FIFO_CAPACITY = 512
FIFO_PACKET = 12
COUNT_BITS_PER_BYTE = 8
FIFO_ENABLE_CANDIDATES = (0x1E, 0x0F, 0x1F, 0x3F, 0x06, 0x18, 0x3C)

ACCEL_LSB = {2: 16384.0, 4: 8192.0, 8: 4096.0, 16: 2048.0}
GYRO_LSB = {250: 131.0, 500: 65.5, 1000: 32.8, 2000: 16.4}
ACCEL_FS_SEL = {2: 0, 4: 1, 8: 2, 16: 3}
GYRO_FS_SEL = {250: 0, 500: 1, 1000: 2, 2000: 3}
ACCEL_DLPF = {0: 246.0, 1: 246.0, 2: 111.4, 3: 50.4, 4: 23.9, 5: 11.6, 6: 5.7, 7: 473.0}
GYRO_DLPF = {0: 196.6, 1: 151.8, 2: 119.5, 3: 51.2, 4: 23.9, 5: 11.6, 6: 5.7, 7: 361.4}
GYRO_BASE_ODR = 1125.0
ACCEL_BASE_ODR = 1125.0
GRAVITY = 9.80665
RAD_PER_DEG = 0.017453293
UT_PER_LSB = 0.15
TEMP_SCALE = 333.87
TEMP_OFFSET = 21.0


class IcmSample(NamedTuple):
    t_mono_ns: int
    t_epoch_ns: int
    ax: float
    ay: float
    az: float
    gx: float
    gy: float
    gz: float


class MagSample(NamedTuple):
    t_mono_ns: int
    t_epoch_ns: int
    mx: float
    my: float
    mz: float


def _int16(pair):
    return pair[0] << 8 | pair[1]


def _to_signed(value):
    return value - 65536 if value > 32767 else value


class IcmFifo:
    def __init__(self, bus=1, address=0x69, odr=1125, accel_fs=2, gyro_fs=250,
                 accel_dlpf=1, gyro_dlpf=1, fifo_mask=0x1E, mag=False,
                 mag_rate=MAG_MODE_100HZ):
        self.bus = SMBus(bus)
        self.address = address
        self.odr = odr
        self.fifo_mask = fifo_mask
        self.accel_scale = GRAVITY / ACCEL_LSB[accel_fs]
        self.gyro_scale = RAD_PER_DEG / GYRO_LSB[gyro_fs]
        self.accel_fs_bits = ACCEL_FS_SEL[accel_fs] << 1
        self.gyro_fs_bits = GYRO_FS_SEL[gyro_fs] << 1
        self.accel_dlpf = accel_dlpf
        self.gyro_dlpf = gyro_dlpf
        self.mag_enabled = mag
        self.mag_rate = mag_rate
        self.samples = 0
        self.mag_samples = 0
        self.mag_errors = 0
        self.fifo_full_events = 0
        self.expired_events = 0
        self.bytes_read = 0
        self.last_epoch_ns = 0
        self.last_mono_ns = 0
        self.mag_last_epoch_ns = 0
        self.mag_last_mono_ns = 0
        self._configure()

    def _write(self, register, value):
        self.bus.i2c_rdwr(i2c_msg.write(self.address, [register, value]))

    def _read(self, register, length):
        read = i2c_msg.read(self.address, length)
        self.bus.i2c_rdwr(i2c_msg.write(self.address, [register]), read)
        return bytes(read)

    def _bank(self, bank):
        self._write(REG_BANK_SEL, bank)

    def _configure(self):
        self._bank(BANK_0)
        self._write(B0_PWR_MGMT_1, 0x01)
        self._write(B0_PWR_MGMT_2, 0x00)
        time.sleep(0.05)
        self._bank(BANK_2)
        self._write(B2_ODR_ALIGN_EN, 0x01)
        self._write(B2_GYRO_SMPLRT_DIV, 0x00)
        self._write(B2_GYRO_CONFIG_1, self.gyro_dlpf << 3 | self.gyro_fs_bits)
        self._write(B2_ACCEL_SMPLRT_DIV_1, 0x00)
        self._write(B2_ACCEL_SMPLRT_DIV_2, 0x00)
        self._write(B2_ACCEL_CONFIG, self.accel_dlpf << 3 | self.accel_fs_bits)
        self._bank(BANK_0)
        self._write(B0_USER_CTRL, 0x40)
        self._write(B0_FIFO_MODE, 0x00)
        self._reset_fifo()
        self._write(B0_FIFO_EN_2, self.fifo_mask)
        if self.mag_enabled:
            self._setup_mag()

    def _reset_fifo(self):
        self._write(B0_FIFO_RST, 0x1F)
        time.sleep(0.002)
        self._write(B0_FIFO_RST, 0x00)

    def _setup_mag(self):
        self._bank(BANK_3)
        self._write(B3_I2C_MST_CTRL, 0x07)
        self._write(B3_I2C_SLV0_ADDR, MAG_ADDRESS)
        self._write(B3_I2C_SLV0_REG, MAG_CNTL3)
        self._write(B3_I2C_SLV0_DO, 0x01)
        self._write(B3_I2C_SLV0_CTRL, 0x81)
        time.sleep(0.01)
        self._write(B3_I2C_SLV0_REG, MAG_CNTL2)
        self._write(B3_I2C_SLV0_DO, self.mag_rate)
        self._write(B3_I2C_SLV0_CTRL, 0x81)
        time.sleep(0.01)
        self._write(B3_I2C_SLV0_ADDR, MAG_ADDRESS | 0x80)
        self._write(B3_I2C_SLV0_REG, MAG_DATA_START)
        self._write(B3_I2C_SLV0_CTRL, MAG_SLV_CTRL)
        self.bank0_user_ctrl_master()
        time.sleep(0.01)

    def bank0_user_ctrl_master(self):
        self._bank(BANK_0)
        self._write(B0_USER_CTRL, 0x40 | 0x20)
        self._write(B0_FIFO_EN_2, self.fifo_mask)

    def fifo_count(self):
        self._bank(BANK_0)
        data = self._read(B0_FIFO_COUNTH, 2)
        return data[0] << 8 | data[1]

    def _read_fifo(self, count):
        self._bank(BANK_0)
        return self._read(B0_FIFO_R_W, count)

    def read_direct(self):
        self._bank(BANK_0)
        data = self._read(B0_ACCEL_XOUT_H, 12)
        values = [_to_signed(_int16(data[i:i + 2])) for i in range(0, 12, 2)]
        accel = [values[i] * self.accel_scale for i in range(3)]
        gyro = [values[i] * self.gyro_scale for i in range(3, 6)]
        return accel, gyro

    def read_sample(self):
        mono_ns = time.monotonic_ns()
        epoch_ns = time.time_ns()
        accel, gyro = self.read_direct()
        self.samples += 1
        self.last_mono_ns, self.last_epoch_ns = mono_ns, epoch_ns
        return IcmSample(mono_ns, epoch_ns, accel[0], accel[1], accel[2],
                         gyro[0], gyro[1], gyro[2])

    def read_temperature(self):
        self._bank(BANK_0)
        data = self._read(B0_TEMP_OUT_H, 2)
        return _to_signed(_int16(data)) / TEMP_SCALE + TEMP_OFFSET

    def _parse(self, payload):
        values = [_to_signed(_int16(payload[i:i + 2])) for i in range(0, len(payload), 2)]
        return values

    def _stamp(self, index, count, mono_ns, epoch_ns):
        step = 1_000_000_000 // self.odr
        offset = (count - 1 - index) * step
        return mono_ns - offset, epoch_ns - offset

    def poll(self):
        count = self.fifo_count() // COUNT_BITS_PER_BYTE
        if count >= FIFO_CAPACITY:
            self.fifo_full_events += 1
        usable = count - count % FIFO_PACKET
        if usable <= 0:
            return []
        payload = self._read_fifo(usable)
        self.bytes_read += len(payload)
        mono_ns = time.monotonic_ns()
        epoch_ns = time.time_ns()
        packets = len(payload) // FIFO_PACKET
        out = []
        for index in range(packets):
            raw = self._parse(payload[index * FIFO_PACKET:(index + 1) * FIFO_PACKET])
            t_mono, t_epoch = self._stamp(index, packets, mono_ns, epoch_ns)
            out.append(IcmSample(t_mono, t_epoch,
                                 raw[0] * self.accel_scale, raw[1] * self.accel_scale,
                                 raw[2] * self.accel_scale, raw[3] * self.gyro_scale,
                                 raw[4] * self.gyro_scale, raw[5] * self.gyro_scale))
        if out:
            self.last_mono_ns, self.last_epoch_ns = out[-1].t_mono_ns, out[-1].t_epoch_ns
            self.samples += len(out)
        return out

    def read_mag(self):
        if not self.mag_enabled:
            return None
        self._bank(BANK_0)
        data = self._read(B0_EXT_SLV_DATA, 8)
        status2 = data[7]
        if status2 & 0x08:
            self.mag_errors += 1
        values = [_to_signed(_int16(data[i:i + 2])) for i in range(0, 6, 2)]
        mono_ns = time.monotonic_ns()
        epoch_ns = time.time_ns()
        self.mag_last_mono_ns, self.mag_last_epoch_ns = mono_ns, epoch_ns
        self.mag_samples += 1
        return MagSample(mono_ns, epoch_ns,
                         values[0] * UT_PER_LSB, values[1] * UT_PER_LSB,
                         values[2] * UT_PER_LSB)

    def rate_hz(self, seconds):
        return self.samples / seconds if seconds else 0.0

    def measure_rate(self, seconds=3.0, use_fifo=False, sleep=0.0):
        start = time.monotonic()
        self.samples = 0
        errors = 0
        last = None
        while time.monotonic() - start < seconds:
            try:
                if use_fifo:
                    batch = self.poll()
                    if batch:
                        last = batch[-1]
                else:
                    last = self.read_sample()
            except OSError:
                errors += 1
            if sleep:
                time.sleep(sleep)
        elapsed = time.monotonic() - start
        return {"hz": self.samples / elapsed if elapsed else 0.0,
                "samples": self.samples, "errors": errors, "last": last,
                "fifo_full": self.fifo_full_events}

    def alignment_report(self, samples=5):
        rows = []
        for _ in range(samples):
            batch = self.poll()
            accel, gyro = self.read_direct()
            if batch:
                last = batch[-1]
                rows.append({"fifo": (last.ax, last.ay, last.az, last.gx, last.gy, last.gz),
                             "direct": (accel[0], accel[1], accel[2], gyro[0], gyro[1], gyro[2])})
            time.sleep(0.02)
        return rows

    def close(self):
        self.bus.close()
    def scan_fifo_masks(self, candidates=FIFO_ENABLE_CANDIDATES):
        accel, gyro = self.read_direct()
        target = sorted(round(v, 2) for v in accel)
        target_gyro = sorted(round(v, 2) for v in gyro)
        results = []
        for mask in candidates:
            self.fifo_mask = mask
            self._write(B0_FIFO_EN_2, mask)
            self._reset_fifo()
            time.sleep(0.05)
            packets = self.poll()
            if not packets:
                results.append((mask, "no packets"))
                continue
            last = packets[-1]
            got_accel = sorted([round(last.ax, 2), round(last.ay, 2), round(last.az, 2)])
            got_gyro = sorted([round(last.gx, 2), round(last.gy, 2), round(last.gz, 2)])
            accel_ok = max(abs(a - b) for a, b in zip(got_accel, target)) < 0.05
            gyro_ok = max(abs(a - b) for a, b in zip(got_gyro, target_gyro)) < 0.02
            results.append((mask, f"accel {'ok' if accel_ok else 'no'} gyro {'ok' if gyro_ok else 'no'}"))
        return results


def _self_test(args):
    reader = IcmFifo(bus=args.bus, address=args.address, odr=args.odr,
                     accel_fs=args.accel_fs, gyro_fs=args.gyro_fs,
                     fifo_mask=args.fifo_mask, mag=args.mag)
    try:
        accel, gyro = reader.read_direct()
        print(f"accel_fs=+/-{args.accel_fs}g gyro_fs=+/-{args.gyro_fs}dps odr={args.odr} "
              f"fifo_mask=0x{reader.fifo_mask:02X} mag={'on' if args.mag else 'off'}")
        print(f"direct accel {accel[0]:8.3f} {accel[1]:8.3f} {accel[2]:8.3f}   |a|="
              f"{(accel[0] ** 2 + accel[1] ** 2 + accel[2] ** 2) ** 0.5:6.3f}")
        print(f"direct gyro  {gyro[0]:8.4f} {gyro[1]:8.4f} {gyro[2]:8.4f}")
        print(f"temperature  {reader.read_temperature():.2f} C")

        if args.scan:
            print("\nfifo packet vs direct read (values should track):")
            for row in reader.alignment_report():
                fifo = " ".join(f"{v:7.3f}" for v in row["fifo"][:3])
                direct = " ".join(f"{v:7.3f}" for v in row["direct"][:3])
                print(f"  fifo a=({fifo})   direct a=({direct})")

        if args.fifo:
            stats = reader.measure_rate(args.seconds, use_fifo=True)
            print(f"\nFIFO path:   {stats['hz']:7.1f} Hz   errors={stats['errors']} "
                  f"fifo_full={stats['fifo_full']}")
        burst = reader.measure_rate(args.seconds, use_fifo=False)
        print(f"burst path:  {burst['hz']:7.1f} Hz   errors={burst['errors']}")
        if burst["last"]:
            last = burst["last"]
            print(f"last burst sample: a=({last.ax:7.3f},{last.ay:7.3f},{last.az:7.3f}) "
                  f"g=({last.gx:8.4f},{last.gy:8.4f},{last.gz:8.4f})")
        if args.mag:
            mag = reader.read_mag()
            print(f"mag: {mag.mx if mag else 0:.2f} {mag.my if mag else 0:.2f} "
                  f"{mag.mz if mag else 0:.2f} uT   overflow flags={reader.mag_errors}")
    finally:
        reader.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bus", type=int, default=1)
    parser.add_argument("--address", type=lambda v: int(v, 0), default=0x69)
    parser.add_argument("--odr", type=int, default=1125)
    parser.add_argument("--accel-fs", type=int, default=2)
    parser.add_argument("--gyro-fs", type=int, default=250)
    parser.add_argument("--fifo-mask", type=lambda v: int(v, 0), default=0x1E)
    parser.add_argument("--mag", action="store_true")
    parser.add_argument("--fifo", action="store_true")
    parser.add_argument("--scan", action="store_true")
    parser.add_argument("--seconds", type=float, default=3.0)
    _self_test(parser.parse_args())
