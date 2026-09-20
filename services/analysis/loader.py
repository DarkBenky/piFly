import glob
import json
import os

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SESSIONS_DIR = os.path.abspath(os.path.join(ROOT, "..", "collector", "logs", "sessions"))
CACHE_DIR = os.path.join(ROOT, "cache")
SNIPPETS_DIR = os.path.join(ROOT, "snippets")

IMU_DTYPE = np.dtype([
    ("mono", "<u8"), ("epoch", "<u8"),
    ("ax", "<f4"), ("ay", "<f4"), ("az", "<f4"),
    ("gx", "<f4"), ("gy", "<f4"), ("gz", "<f4"),
])
IMU_BYTES = IMU_DTYPE.itemsize
MIN_EPOCH_NS = 1_500_000_000_000_000_000

JSONL_FILES = {"gps": "gps.jsonl", "bme": "bme.jsonl", "marks": "marks.jsonl"}


def ensure_dirs():
    for path in (CACHE_DIR, SNIPPETS_DIR):
        os.makedirs(path, exist_ok=True)


def _size(path):
    return os.path.getsize(path) if os.path.exists(path) else 0


def list_sessions(sessions_dir):
    rows = []
    for path in glob.glob(os.path.join(sessions_dir, "*")):
        if not os.path.isdir(path):
            continue
        session_id = os.path.basename(path)
        imu_path = os.path.join(path, "imu.bin")
        records = _size(imu_path) // IMU_BYTES
        meta = {}
        meta_path = os.path.join(path, "meta.json")
        if os.path.exists(meta_path):
            try:
                with open(meta_path) as handle:
                    meta = json.load(handle)
            except (OSError, ValueError):
                meta = {}
        rows.append({
            "id": session_id,
            "path": path,
            "records": records,
            "duration": meta.get("duration_s"),
            "schema": meta.get("schema"),
            "label": meta.get("label") or session_id.rsplit("-", 1)[-1],
            "name": meta.get("name") or "",
            "has_meta": bool(meta),
            "gps_bytes": _size(os.path.join(path, "gps.jsonl")),
            "bme_bytes": _size(os.path.join(path, "bme.jsonl")),
            "marks_bytes": _size(os.path.join(path, "marks.jsonl")),
            "mag_bytes": _size(os.path.join(path, "mag.bin")),
            "mtime": os.path.getmtime(path),
        })
    rows.sort(key=lambda row: row["mtime"], reverse=True)
    return rows


class Imu:
    def __init__(self, session_path):
        self.path = os.path.join(session_path, "imu.bin")
        self.records = _size(self.path) // IMU_BYTES
        self._mm = None
        self._valid = None

    @property
    def mm(self):
        if self._mm is None:
            self._mm = np.memmap(self.path, dtype=IMU_DTYPE, mode="r", shape=(self.records,))
        return self._mm

    def _scan(self, start, end, step, forward):
        index = start if forward else end
        while (index < end) if forward else (index > start):
            lo = index if forward else max(start, index - step)
            hi = min(end, index + step) if forward else index
            block = np.asarray(self.mm[lo:hi]["epoch"], dtype="u8")
            good = np.nonzero(block >= MIN_EPOCH_NS)[0]
            if len(good):
                return lo + int(good[0] if forward else good[-1])
            index = hi if forward else lo
        return None

    def valid_range(self):
        if self._valid is None:
            if self.records == 0:
                self._valid = (0, 0)
            else:
                first = self._scan(0, self.records, 50_000, True)
                last = self._scan(0, self.records, 50_000, False)
                if first is None or last is None:
                    self._valid = (0, 0)
                else:
                    self._valid = (first, last + 1)
        return self._valid

    def time_range(self):
        start, end = self.valid_range()
        if end <= start:
            return None
        return float(self.mm[start]["epoch"]) / 1e9, float(self.mm[end - 1]["epoch"]) / 1e9

    def index_at(self, t):
        start, end = self.valid_range()
        if end <= start:
            return start
        t0, t1 = self.time_range()
        if t <= t0:
            return start
        if t >= t1:
            return end
        estimate = start + int((t - t0) / max(t1 - t0, 1e-9) * (end - start - 1))
        lo = max(start, estimate - 300_000)
        hi = min(end, estimate + 300_000)
        index = int(np.searchsorted(np.asarray(self.mm[lo:hi]["epoch"], dtype="u8"), int(t * 1e9)))
        return min(max(lo + index, start), end)

    def window(self, t0, t1, max_points=2_000_000):
        start, end = self.valid_range()
        i0 = max(start, self.index_at(t0))
        i1 = max(i0, min(end, self.index_at(t1)))
        span = max(1, i1 - i0)
        stride = max(1, int(np.ceil(span / max_points)))
        block = self.mm[i0:i1:stride]
        n = len(block)
        if n == 0:
            return None
        t = block["epoch"].astype("f8") / 1e9
        return {
            "t": t - t[0],
            "t_epoch": t,
            "ax": block["ax"].astype("f8"), "ay": block["ay"].astype("f8"), "az": block["az"].astype("f8"),
            "gx": block["gx"].astype("f8"), "gy": block["gy"].astype("f8"), "gz": block["gz"].astype("f8"),
            "stride": stride,
            "index0": i0,
        }


def read_jsonl(session_path, stream, t0=None, t1=None, max_records=300_000):
    rows = []
    path = os.path.join(session_path, JSONL_FILES[stream])
    if not os.path.exists(path):
        return rows
    with open(path, "r", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if len(line) < 2 or line[0] != "{":
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            t = row.get("t")
            if t is None:
                t = row.get("timestamp")
            if t is None:
                continue
            if t0 is not None and t < t0:
                continue
            if t1 is not None and t > t1:
                break
            rows.append(row)
            if len(rows) >= max_records:
                break
    return rows


def _column(rows, key, default=np.nan):
    return np.array([default if row.get(key) is None else float(row[key]) for row in rows], dtype="f8")


def gps_bundle(rows, t0=None):
    if not rows:
        return None
    t = _column(rows, "t")
    return {
        "t": t - t[0] if t0 is None else t - t0,
        "lat": _column(rows, "lat"),
        "lon": _column(rows, "lon"),
        "alt": _column(rows, "alt_m"),
        "sats": _column(rows, "sats"),
        "speed_kt": _column(rows, "speed_kt"),
        "course": _column(rows, "course"),
    }


def bme_bundle(rows, t0=None):
    if not rows:
        return None
    t = _column(rows, "timestamp")
    return {
        "t": t - t[0] if t0 is None else t - t0,
        "temp_c": _column(rows, "bme_temp_c"),
        "pressure_hpa": _column(rows, "bme_pressure_hpa"),
        "humidity_pct": _column(rows, "bme_humidity_pct"),
        "cpu_temp_c": _column(rows, "cpu_temp_c"),
    }


def marks_bundle(rows, t0=None):
    if not rows:
        return None
    t = _column(rows, "t")
    labels = [str(row.get("label") or "mark") for row in rows]
    return {"t": t - t[0] if t0 is None else t - t0, "label": labels}


def cache_path(session_id):
    return os.path.join(CACHE_DIR, f"{session_id}.parquet")


def build_overview(session_id, session_path, target_hz=10.0, progress=None):
    import pandas as pd

    ensure_dirs()
    imu = Imu(session_path)
    if imu.records == 0:
        return None
    t0, t1 = imu.time_range()
    rate = imu.records / max(t1 - t0, 1e-9)
    bucket = max(1, int(round(rate / target_hz)))
    frames = []
    chunk = bucket * 20_000
    position = 0
    while position < imu.records:
        block = np.asarray(imu.mm[position:position + chunk])
        usable = (len(block) // bucket) * bucket
        if usable:
            view = block[:usable].reshape(-1, bucket)
            frame = pd.DataFrame({
                "t": view["epoch"][:, 0].astype("f8") / 1e9,
                "n": np.full(len(view), bucket, dtype="i4"),
            })
            for key in ("ax", "ay", "az", "gx", "gy", "gz"):
                frame[key] = view[key].astype("f8").mean(axis=1)
            frames.append(frame)
        position += usable if usable else len(block)
        if progress:
            progress(min(position / imu.records, 1.0))
    overview = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    overview.to_parquet(cache_path(session_id), index=False)
    return overview


def load_overview(session_id):
    path = cache_path(session_id)
    if not os.path.exists(path):
        return None
    import pandas as pd
    return pd.read_parquet(path)
