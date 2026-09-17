import argparse
import json
import os
import platform
import queue
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
    parser.add_argument("--bme-rate", type=float, default=0.5)
    parser.add_argument("--mag-rate", type=float, default=0.0)
    parser.add_argument("--imu-bus", type=int, default=1)
    parser.add_argument("--imu-address", type=lambda v: int(v, 0), default=0x69)
    parser.add_argument("--fifo", action="store_true")
    parser.add_argument("--out", default=os.path.join("logs", "sessions"))
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


def drain(target, batch=256):
    items = []
    for _ in range(batch):
        try:
            items.append(target.get_nowait())
        except queue.Empty:
            break
    return items


class Collector:
    def __init__(self, args):
        self.args = args
        self.label = "static" if args.static else "move" if args.move else "unknown"
        self.session_id = f"{int(time.time())}-{self.label}"
        self.dir = os.path.join(args.out, self.session_id)
        self.stop = threading.Event()
        self.queues = {
            "imu": queue.Queue(maxsize=200_000),
            "mag": queue.Queue(maxsize=20_000),
            "gps": queue.Queue(maxsize=1000),
            "bme": queue.Queue(maxsize=1000),
            "marks": queue.Queue(maxsize=1000),
        }
        self.counts = {name: Counter() for name in self.queues}
        self.errors = {"imu": Counter(), "gps": Counter(), "bme": Counter(), "upload": Counter()}
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
        self.rates_at_stop = {}
        self.reader = None
        self.bme = None
        self.gps = None
        self.files = {}
        self.buffers = {name: [] for name in ("imu", "mag", "gps", "bme", "marks", "fusion")}
        self.last_flush = time.time()

    def open_files(self):
        os.makedirs(self.dir, exist_ok=True)
        for name in ("imu.bin", "mag.bin", "gps.jsonl", "bme.jsonl", "marks.jsonl", "fusion.jsonl"):
            mode = "wb" if name.endswith(".bin") else "w"
            self.files[name] = open(os.path.join(self.dir, name), mode)

    def write(self, name, payload):
        self.buffers[name].append(payload)

    def flush(self):
        now = time.time()
        if now - self.last_flush < 1.0:
            return
        self.last_flush = now
        for name in ("imu", "mag"):
            items = self.buffers[name]
            if items:
                handle = self.files[f"{name}.bin"]
                handle.write(b"".join(items))
                items.clear()
        for name in ("gps", "bme", "marks", "fusion"):
            items = self.buffers[name]
            if items:
                handle = self.files[f"{name}.jsonl"]
                for item in items:
                    handle.write(json.dumps(item) + "\n")
                items.clear()
        for handle in self.files.values():
            handle.flush()

    def close_files(self, extra):
        for name in ("imu", "mag"):
            items = self.buffers[name]
            if items:
                self.files[f"{name}.bin"].write(b"".join(items))
                items.clear()
        for name in ("gps", "bme", "marks", "fusion"):
            items = self.buffers[name]
            if items:
                handle = self.files[f"{name}.jsonl"]
                for item in items:
                    handle.write(json.dumps(item) + "\n")
                items.clear()
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

    def push(self, name, item, payload):
        try:
            self.queues[name].put_nowait(item)
        except queue.Full:
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
                self.push("imu", sample, payload)
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
                self.push("mag", sample, MAG_STRUCT.pack(sample.t_mono_ns, sample.t_epoch_ns,
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
                self.push("gps", record, record)
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
            self.push("bme", record, record)
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
            self.push("marks", record, record)

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

        print(f"session {self.session_id} -> {self.dir}")
        print(f"label={self.label} imu_rate={self.args.imu_rate} "
              f"gps_rate={self.args.gps_rate} bme_rate={self.args.bme_rate} "
              f"upload={'off' if self.args.no_upload else self.args.server}")
        print("type a mark and press enter (s=static m=move r=rotate b=bump l=lift p=place w=walk)")

        deadline = self.started + self.args.duration if self.args.duration else None
        try:
            while not self.stop.is_set():
                if deadline and time.time() >= deadline:
                    break
                time.sleep(0.5)
                self.flush()
        except KeyboardInterrupt:
            pass
        finally:
            self.stopped = time.time()
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
            self.reader.close()
            print(f"\nsaved {self.dir}")
            print(f"counts={ {k: v.value for k, v in self.counts.items()} } "
                  f"errors={ {k: v.value for k, v in self.errors.items()} } drops={self.drops.value}")


def main():
    args = parse_args(sys.argv[1:])
    collector = Collector(args)
    signal.signal(signal.SIGTERM, lambda *_: collector.stop.set())
    collector.run()


if __name__ == "__main__":
    main()
