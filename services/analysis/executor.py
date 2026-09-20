import json
import os
import subprocess
import sys
import tempfile
import time

import numpy as np

RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runner.py")


def build_arrays(window, gps, bme, marks, fs, session_id, epoch0):
    arrays = {
        "t": np.asarray(window["t"], dtype="f8"),
        "t_epoch": np.asarray(window["t_epoch"], dtype="f8"),
        "fs": np.asarray(fs, dtype="f8"),
        "epoch0": np.asarray(epoch0, dtype="f8"),
        "session": np.asarray(session_id),
    }
    for key in ("ax", "ay", "az", "gx", "gy", "gz"):
        arrays[key] = np.asarray(window[key], dtype="f8")
    for prefix, bundle in (("gps", gps), ("bme", bme), ("marks", marks)):
        if not bundle:
            continue
        for key, value in bundle.items():
            if key == "label":
                arrays[f"{prefix}__label"] = np.asarray([str(v) for v in value], dtype="<U48")
            else:
                arrays[f"{prefix}__{key}"] = np.asarray(value, dtype="f8")
    return arrays


def run_user_code(code, arrays, timeout=10.0):
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="pifly_run_") as tmp:
        window_path = os.path.join(tmp, "window.npz")
        code_path = os.path.join(tmp, "user_code.py")
        result_path = os.path.join(tmp, "result.npz")
        np.savez(window_path, **arrays)
        with open(code_path, "w") as handle:
            handle.write(code)
        try:
            proc = subprocess.run(
                [sys.executable, RUNNER, window_path, code_path, result_path],
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"timed out after {timeout:.0f}s", "runtime": time.time() - started}
        runtime = time.time() - started
        if proc.returncode != 0 or not os.path.exists(result_path):
            message = (proc.stderr or proc.stdout or "unknown error").strip()
            return {"ok": False, "error": message[-4000:], "runtime": runtime, "stdout": proc.stdout[-1000:]}
        with np.load(result_path, allow_pickle=False) as data:
            manifest = json.loads(str(data["manifest"]))
            arrays_out = {key: data[key].copy() for key in data.files if key != "manifest"}
        return {"ok": True, "manifest": manifest, "arrays": arrays_out,
                "runtime": runtime, "stdout": proc.stdout[-2000:]}
