import argparse
import json
import os
import platform
import signal
import socket
import struct
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from modules.aggregate import BucketSeries, Counter, RateMeter
from modules.icm_fifo import IcmFifo

IMU_STRUCT = struct.Struct("<QQ6f")
MAG_STRUCT = struct.Struct("<QQ3f")
IMU_FIELDS = ("ax", "ay", "az", "gx", "gy", "gz")
MARK_LABELS = {
    "s": "static",
    "m": "move",
    "r": "rotate",
    "b": "bump",
    "l": "lift",
    "p": "place",
    "w": "walk",
}
SCHEMA_VERSION = 1
STREAMS = ("imu", "mag", "gps", "bme", "marks")
MAX_PENDING = 200_000


def _coord(value, positive, negative):
    return f"{abs(value):.5f}{positive if value >= 0 else negative}"


def parse_args(argv):
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--static", action="store_true")
    group.add_argument("--move", action="store_true")
    parser.add_argument("--name", default="")
    parser.add_argument("--note", default="")
    parser.add_argument("--duration", type=float, default=0.0)
    parser.add_argument("--imu-rate", type=float, default=1000.0)
    parser.add_argument("--gps-rate", type=float, default=10.0)
    parser.add_argument("--bme-rate", type=float, default=0.2)
    parser.add_argument("--mag-rate", type=float, default=0.0)
    parser.add_argument("--imu-bus", type=int, default=1)
    parser.add_argument("--imu-address", type=lambda v: int(v, 0), default=0x69)
    parser.add_argument("--fifo", action="store_true")
    parser.add_argument("--out", default=os.path.join("logs", "sessions"))
    parser.add_argument("--no-log", action="store_true",
                        help="run without writing session files to disk")
    parser.add_argument("--display", action="store_true",
                        help="show live readings on the I2C OLED")
    parser.add_argument("--display-rate", type=float, default=1.0,
                        help="display refresh rate in Hz (default 1.0)")
    parser.add_argument("--display-rotate", action="store_true",
                        help="flip the OLED 180 degrees")
    parser.add_argument("--server", default="http://91.98.145.193:5001")
    parser.add_argument("--no-upload", action="store_true")
    parser.add_argument("--upload-interval", type=float, default=5.0)
    return parser.parse_args(argv)


def git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=False).stdout.strip()
    except OSError:
        return ""


class Collector:
    def __init__(self, args):
        self.args = args
        self.label = "static" if args.static else "move" if args.move else "unknown"
        self.session_id = f"{int(time.time())}-{self.label}"
        self.dir = os.path.join(args.out, self.session_id)
        self.stop = threading.Event()
        self.counts = {name: Counter() for name in STREAMS}
        self.errors = {"imu": Counter(), "gps": Counter(), "bme": Counter(),
                       "upload": Counter(), "display": Counter()}
        self.drops = Counter()
        self.rates = {
            "imu": RateMeter(),
            "gps": RateMeter(),
            "bme": RateMeter(),
            "mag": RateMeter(),
        }
        self.imu_series = BucketSeries(IMU_FIELDS, bucket_s=0.5, max_buckets=600)
        self.gps_series = BucketSeries(("lat", "lon", "alt_m", "sats"), bucket_s=5.0, max_buckets=240)
        self.bme_series = BucketSeries(("bme_temp_c", "bme_pressure_hpa", "bme_humidity_pct"),
                                       bucket_s=10.0, max_buckets=180)
        self.last = {"gps": None, "bme": None, "imu": None}
        self.marks = []
        self.started = time.time()
        self.stopped = None
        self.accepting = True
        self.rates_at_stop = {}
        self.buffer_lock = threading.Lock()
        self.reader = None
        self.bme = None
        self.gps = None
        self.files = {}
        self.buffers = {name: [] for name in ("imu", "mag", "gps", "bme", "marks", "fusion")}
        self.last_flush = time.time()
        self.save = not args.no_log
        self.disp = None
        self.mono = None
        self.display_errors = 0
        self.last_display = 0.0

    def open_files(self):
        if not self.save:
            return
        os.makedirs(self.dir, exist_ok=True)
        for name in ("imu.bin", "mag.bin", "gps.jsonl", "bme.jsonl", "marks.jsonl", "fusion.jsonl"):
            mode = "wb" if name.endswith(".bin") else "w"
            self.files[name] = open(os.path.join(self.dir, name), mode)

    def write(self, name, payload):
        if not self.save:
            return
        with self.buffer_lock:
            self.buffers[name].append(payload)

    def take(self, name):
        with self.buffer_lock:
            items, self.buffers[name] = self.buffers[name], []
        return items

    def flush(self):
        now = time.time()
        if now - self.last_flush < 1.0:
            return
        self.last_flush = now
        for name in ("imu", "mag"):
            items = self.take(name)
            if items:
                self.files[f"{name}.bin"].write(b"".join(items))
        for name in ("gps", "bme", "marks", "fusion"):
            items = self.take(name)
            if items:
                handle = self.files[f"{name}.jsonl"]
                for item in items:
                    handle.write(json.dumps(item) + "\n")
        for handle in self.files.values():
            handle.flush()

    def close_files(self, extra):
        if not self.save:
            return
        for name in ("imu", "mag"):
            items = self.take(name)
            if items:
                self.files[f"{name}.bin"].write(b"".join(items))
        for name in ("gps", "bme", "marks", "fusion"):
            items = self.take(name)
            if items:
                handle = self.files[f"{name}.jsonl"]
                for item in items:
                    handle.write(json.dumps(item) + "\n")
        meta = dict(extra)
        meta.update({
            "schema": SCHEMA_VERSION,
            "session": self.session_id,
            "label": self.label,
            "name": self.args.name,
            "note": self.args.note,
            "started": self.started,
            "ended": self.stopped or time.time(),
            "duration_s": round((self.stopped or time.time()) - self.started, 3),
            "teardown_s": round(time.time() - (self.stopped or time.time()), 3),
            "git_commit": git_commit(),
            "host": socket.gethostname(),
            "kernel": platform.release(),
            "python": platform.python_version(),
            "counts": {k: v.value for k, v in self.counts.items()},
            "errors": {k: v.value for k, v in self.errors.items()},
            "drops": self.drops.value,
            "imu_file_bytes": os.path.getsize(os.path.join(self.dir, "imu.bin")),
            "imu_mean_hz": round(self.counts["imu"].value /
                                max(0.001, (self.stopped or time.time()) - self.started), 2),
            "marks": self.marks,
        })
        with open(os.path.join(self.dir, "meta.json"), "w") as handle:
            json.dump(meta, handle, indent=2)
        for handle in self.files.values():
            handle.flush()
            handle.close()

    def pending(self, name):
        with self.buffer_lock:
            return len(self.buffers[name])

    def setup_display(self):
        try:
            from modules.display import DisplayUnavailable, open_display
        except Exception as exc:
            print(f"display: unavailable ({exc})")
            return
        try:
            self.disp = open_display(rotate=self.args.display_rotate)
        except DisplayUnavailable as exc:
            print(f"display: {exc}")
            self.disp = None
            return
        try:
            from PIL import ImageFont
            self.mono = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 9)
        except Exception:
            self.mono = None
        print(f"display: 0x{self.disp.address:02X} on i2c-{self.disp.bus}")

    def update_display(self):
        if self.disp is None:
            return
        now = time.time()
        if now - self.last_display < 1.0 / max(0.1, self.args.display_rate):
            return
        self.last_display = now
        disp = self.disp
        bme = self.last["bme"] or {}
        gps = self.last["gps"] or {}
        imu = self.last["imu"]
        try:
            disp.clear()
            clock = time.strftime("%H:%M:%S")
            disp.text(clock, 1, 0, size=10)
            label_w, _ = disp.text_size("piFly", size=10)
            disp.text("piFly", disp.width - label_w - 1, 0, size=10)
            disp.hline(0, disp.width - 1, 12)
            if bme:
                rows = [
                    "BME {:4.1f}C {:4.1f}% {:4.0f}h".format(
                        bme.get("bme_temp_c", 0.0), bme.get("bme_humidity_pct", 0.0),
                        bme.get("bme_pressure_hpa", 0.0)),
                    "CPU {:4.1f}C  IMU {:4.0f}Hz".format(
                        bme.get("cpu_temp_c", 0.0), self.rates["imu"].rate()),
                ]
            else:
                rows = [
                    "BME --",
                    "CPU --    IMU {:4.0f}Hz".format(self.rates["imu"].rate()),
                ]
            sats = gps.get("sats") or 0
            if gps.get("lat") is not None:
                rows.append("GPS {:2d}sat alt{:4.0f}m".format(sats, gps.get("alt_m") or 0.0))
                rows.append("{} {}".format(_coord(gps["lat"], "N", "S"),
                                           _coord(gps["lon"], "E", "W")))
            else:
                rows.append("GPS no fix {:2d}sat".format(sats))
                rows.append("--")
            for index, row in enumerate(rows):
                disp.text(row, 1, 15 + index * 11, size=9, font=self.mono)
            if imu is not None:
                bubble = int(disp.width / 2 + max(-1.0, min(1.0, imu.ax / 9.81)) * 26)
                disp.hline(disp.width // 2 - 30, disp.width // 2 + 30, 61)
                disp.fill_rect(bubble - 2, 58, bubble + 2, 63)
            disp.show()
            self.display_errors = 0
        except Exception:
            self.errors["display"].inc()
            self.display_errors += 1
            if self.display_errors >= 5:
                self.stop_display()

    def stop_display(self):
        if self.disp is None:
            return
        try:
            self.disp.clear()
            self.disp.show()
        except Exception:
            pass
        try:
            self.disp.close()
        except Exception:
            pass
        self.disp = None

    def push(self, name, payload):
        if not self.accepting:
            return
        if self.pending(name) >= MAX_PENDING:
            self.drops.inc()
            return
        self.counts[name].inc()
        self.write(name, payload)

    def imu_loop(self):
        period = 1.0 / self.args.imu_rate if self.args.imu_rate else 0.0
        next_at = time.monotonic()
        while not self.stop.is_set():
            try:
                if self.args.fifo:
                    batch = self.reader.poll()
                    samples = batch[-1:]
                else:
                    samples = [self.reader.read_sample()]
                    batch = samples
            except OSError:
                self.errors["imu"].inc()
                continue
            for sample in samples:
                payload = IMU_STRUCT.pack(sample.t_mono_ns, sample.t_epoch_ns,
                                          sample.ax, sample.ay, sample.az,
                                          sample.gx, sample.gy, sample.gz)
                self.push("imu", payload)
                self.rates["imu"].add()
                self.imu_series.add(sample.t_epoch_ns / 1e9,
                                    (sample.ax, sample.ay, sample.az, sample.gx, sample.gy, sample.gz))
                self.last["imu"] = sample
            if period:
                next_at += period
                delay = next_at - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                else:
                    next_at = time.monotonic()

    def mag_loop(self):
        period = 1.0 / self.args.mag_rate
        while not self.stop.is_set():
            try:
                sample = self.reader.read_mag()
            except OSError:
                sample = None
            if sample:
                self.push("mag", MAG_STRUCT.pack(sample.t_mono_ns, sample.t_epoch_ns,
                                                 sample.mx, sample.my, sample.mz))
                self.rates["mag"].add()
            time.sleep(period)

    def gps_loop(self):
        from modules.gps import GPS

        period = 1.0 / self.args.gps_rate if self.args.gps_rate else 0.5
        self.gps = GPS()
        while not self.stop.is_set():
            try:
                reading = self.gps.read()
            except Exception:
                self.errors["gps"].inc()
                time.sleep(1.0)
                continue
            if reading and reading.get("lat") is not None:
                record = {"t": time.time(), "mono_ns": time.monotonic_ns()}
                record.update(reading)
                raw = getattr(self.gps, "last_raw", None)
                if raw:
                    record["raw"] = raw
                self.push("gps", record)
                self.rates["gps"].add()
                self.last["gps"] = record
                self.gps_series.add(record["t"], (reading.get("lat") or 0.0,
                                                  reading.get("lon") or 0.0,
                                                  reading.get("alt_m") or 0.0,
                                                  reading.get("sats") or 0))
            time.sleep(period)

    def bme_loop(self):
        from modules.temperature import SensorUnavailable, open_bme280

        period = 1.0 / self.args.bme_rate if self.args.bme_rate else 2.0
        self.bme = open_bme280(quiet=True)
        while not self.stop.is_set():
            if self.bme is None:
                self.bme = open_bme280(retries=1, quiet=True)
                time.sleep(2.0)
                continue
            try:
                record = self.bme.get_record()
            except SensorUnavailable:
                self.errors["bme"].inc()
                self.bme = None
                continue
            self.push("bme", record)
            self.rates["bme"].add()
            self.last["bme"] = record
            self.bme_series.add(record["timestamp"], (record["bme_temp_c"],
                                                      record["bme_pressure_hpa"],
                                                      record["bme_humidity_pct"]))
            time.sleep(period)

    def mark_loop(self):
        for line in sys.stdin:
            if self.stop.is_set():
                break
            text = line.strip()
            if text in MARK_LABELS:
                label = MARK_LABELS[text]
            elif text:
                label = text
            else:
                label = "mark"
            record = {"t": time.time(), "mono_ns": time.monotonic_ns(),
                      "key": text, "label": label}
            self.marks.append(record)
            self.push("marks", record)

    def payload(self):
        return {
            "session": self.session_id,
            "label": self.label,
            "name": self.args.name,
            "note": self.args.note,
            "t": time.time(),
            "started": self.started,
            "duration_s": round(time.time() - self.started, 1),
            "rates": {k: round(v.rate(), 2) for k, v in self.rates.items()},
            "counts": {k: v.value for k, v in self.counts.items()},
            "errors": {k: v.value for k, v in self.errors.items()},
            "drops": self.drops.value,
            "imu_series": self.imu_series.snapshot(limit=240),
            "gps_series": self.gps_series.snapshot(limit=240),
            "bme_series": self.bme_series.snapshot(limit=120),
            "gps": self.last["gps"],
            "bme": self.last["bme"],
            "marks": self.marks[-50:],
        }

    def uplink_loop(self):
        import requests

        url = self.args.server.rstrip("/") + "/api/live"
        backoff = 1.0
        while not self.stop.is_set():
            body = self.payload()
            try:
                response = requests.post(url, json=body, timeout=10)
                if response.status_code != 200:
                    self.errors["upload"].inc()
            except Exception:
                self.errors["upload"].inc()
                backoff = min(backoff * 2, 30.0)
            else:
                backoff = 1.0
            slept = 0.0
            while slept < self.args.upload_interval and not self.stop.is_set():
                step = min(0.5, self.args.upload_interval - slept)
                time.sleep(step)
                slept += step
            if backoff > 1.0:
                time.sleep(backoff)

    def run(self):
        self.open_files()
        if self.args.display:
            self.setup_display()
        self.reader = IcmFifo(bus=self.args.imu_bus, address=self.args.imu_address,
                              odr=int(self.args.imu_rate) or 1125,
                              mag=self.args.mag_rate > 0)
        threads = [
            threading.Thread(target=self.imu_loop, daemon=True),
            threading.Thread(target=self.gps_loop, daemon=True),
            threading.Thread(target=self.bme_loop, daemon=True),
            threading.Thread(target=self.mark_loop, daemon=True),
        ]
        if self.args.mag_rate > 0:
            threads.append(threading.Thread(target=self.mag_loop, daemon=True))
        if not self.args.no_upload:
            threads.append(threading.Thread(target=self.uplink_loop, daemon=True))
        for thread in threads:
            thread.start()

        if self.save:
            print(f"session {self.session_id} -> {self.dir}")
        else:
            print(f"session {self.session_id} (log disabled, nothing written)")
        print(f"label={self.label} imu_rate={self.args.imu_rate} "
              f"gps_rate={self.args.gps_rate} bme_rate={self.args.bme_rate} "
              f"upload={'off' if self.args.no_upload else self.args.server} "
              f"display={'on' if self.disp is not None else 'off'}")
        print("type a mark and press enter (s=static m=move r=rotate b=bump l=lift p=place w=walk)")

        deadline = self.started + self.args.duration if self.args.duration else None
        try:
            while not self.stop.is_set():
                if deadline and time.time() >= deadline:
                    break
                time.sleep(0.2)
                self.flush()
                self.update_display()
        except KeyboardInterrupt:
            pass
        finally:
            self.stopped = time.time()
            self.accepting = False
            self.stop.set()
            self.rates_at_stop = {k: round(v.rate(), 2) for k, v in self.rates.items()}
            if self.gps is not None:
                try:
                    self.gps.close()
                except Exception:
                    pass
            for thread in threads:
                thread.join(timeout=0.5)
            self.flush()
            extra = {
                "rates_final": self.rates_at_stop,
                "imu_settings": {
                    "bus": self.args.imu_bus,
                    "address": hex(self.args.imu_address),
                    "fifo": self.args.fifo,
                    "accel_scale": self.reader.accel_scale,
                    "gyro_scale": self.reader.gyro_scale,
                    "odr_target": self.reader.odr,
                    "accel_dlpf": self.reader.accel_dlpf,
                    "gyro_dlpf": self.reader.gyro_dlpf,
                },
                "bme_backend": getattr(self.bme, "backend", None),
            }
            self.close_files(extra)
            self.stop_display()
            self.reader.close()
            if self.save:
                print(f"\nsaved {self.dir}")
            else:
                print("\nlog disabled, nothing saved")
            print(f"counts={ {k: v.value for k, v in self.counts.items()} } "
                  f"errors={ {k: v.value for k, v in self.errors.items()} } drops={self.drops.value}")


def main():
    args = parse_args(sys.argv[1:])
    collector = Collector(args)
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: collector.stop.set())
    collector.run()


if __name__ == "__main__":
    main()
