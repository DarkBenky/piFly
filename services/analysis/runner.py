import json
import os
import sys
import traceback

import numpy as np
import pandas as pd
from scipy import signal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers


def build_input(data):
    def maybe(prefix):
        keys = [k.split("__", 1)[1] for k in data.files if k.startswith(prefix + "__")]
        if not keys:
            return None
        out = {}
        for key in keys:
            value = data[f"{prefix}__{key}"]
            out[key] = [str(v) for v in value] if key == "label" else value
        return out

    bundle = {
        "t": data["t"], "t_epoch": data["t_epoch"],
        "ax": data["ax"], "ay": data["ay"], "az": data["az"],
        "gx": data["gx"], "gy": data["gy"], "gz": data["gz"],
        "gps": maybe("gps"), "bme": maybe("bme"), "marks": maybe("marks"),
        "fs": float(data["fs"]), "epoch0": float(data["epoch0"]),
        "session": str(data["session"]),
    }
    return bundle


def as_array(value):
    return np.asarray(value, dtype="f8").ravel()


def normalize(result, arrays, manifest):
    if not isinstance(result, dict):
        raise ValueError("process(x) must return a dict")

    for group, fields in (("path", ("lat", "lon", "alt", "t")),
                          ("position", ("east", "north", "up", "t")),
                          ("velocity", ("vx", "vy", "vz", "t"))):
        value = result.get(group)
        if value is None:
            continue
        if not isinstance(value, dict):
            raise ValueError(f"'{group}' must be a dict")
        present = []
        for field in fields:
            if field in value and value[field] is not None:
                arrays[f"{group}__{field}"] = as_array(value[field])
                present.append(field)
        if present:
            manifest[group] = present

    series = result.get("series")
    if isinstance(series, dict):
        manifest["series"] = {}
        for name, value in series.items():
            if isinstance(value, dict):
                t, v = value.get("t"), value.get("v")
            elif isinstance(value, (tuple, list)) and len(value) == 2:
                t, v = value
            else:
                t, v = None, value
            key = str(name)[:48]
            if t is not None:
                arrays[f"series__{key}__t"] = as_array(t)
            if v is not None:
                arrays[f"series__{key}__v"] = as_array(v)
                manifest["series"][key] = {"t": t is not None}

    stats = result.get("stats")
    if isinstance(stats, dict):
        manifest["stats"] = {}
        for name, value in stats.items():
            try:
                manifest["stats"][str(name)[:48]] = float(value)
            except (TypeError, ValueError):
                continue

    events = result.get("events")
    if events:
        times = []
        labels = []
        if isinstance(events, dict):
            times = as_array(events.get("t", []))
            labels = [str(v) for v in events.get("label", [])]
        else:
            for item in events:
                times.append(float(item[0]))
                labels.append(str(item[1]))
            times = np.asarray(times, dtype="f8")
        if len(times):
            arrays["events__t"] = times
            arrays["events__label"] = np.asarray(labels, dtype="<U48")
            manifest["events"] = ["t", "label"]

    manifest["ignored"] = sorted(set(result) - {"path", "position", "velocity", "series", "stats", "events"})


def main():
    window_path, code_path, result_path = sys.argv[1:4]
    arrays = {}
    manifest = {}
    with np.load(window_path, allow_pickle=False) as data:
        bundle = build_input(data)
    source = open(code_path).read()
    namespace = {
        "np": np, "pd": pd, "signal": signal, "helpers": helpers,
        "__name__": "user_code",
    }
    for name in dir(helpers):
        if not name.startswith("_"):
            namespace[name] = getattr(helpers, name)
    exec(compile(source, code_path, "exec"), namespace)
    process = namespace.get("process")
    if not callable(process):
        raise ValueError("your code must define: def process(x): ...")
    result = process(bundle)
    normalize(result, arrays, manifest)
    np.savez(result_path, manifest=np.asarray(json.dumps(manifest)), **arrays)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(traceback.format_exc(), file=sys.stderr)
        sys.exit(1)
