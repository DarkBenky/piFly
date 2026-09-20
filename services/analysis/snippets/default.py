def process(x):
    t = x["t"]
    ax, ay, az = x["ax"], x["ay"], x["az"]
    gx, gy, gz = x["gx"], x["gy"], x["gz"]
    gps = x["gps"]
    fs = x["fs"]

    accel_norm = np.sqrt(ax ** 2 + ay ** 2 + az ** 2)
    gyro_norm = np.sqrt(gx ** 2 + gy ** 2 + gz ** 2)

    out = {
        "series": {
            "|accel| m/s2": (t, lowpass(accel_norm, fs, 5.0)),
            "|gyro| rad/s": (t, lowpass(gyro_norm, fs, 5.0)),
        },
        "stats": {
            "samples": float(len(t)),
            "duration_s": float(t[-1] - t[0]) if len(t) else 0.0,
            "accel_mean": float(accel_norm.mean()),
            "accel_std": float(accel_norm.std()),
        },
    }

    if gps is not None and len(gps["t"]):
        lat0, lon0 = gps["lat"][0], gps["lon"][0]
        east, north = gps_to_enu(gps["lat"], gps["lon"], lat0, lon0)
        up = gps["alt"] - gps["alt"][0]

        out["path"] = {"t": gps["t"], "lat": gps["lat"], "lon": gps["lon"], "alt": gps["alt"]}
        out["position"] = {"t": gps["t"], "east": east, "north": north, "up": up}

        if len(gps["t"]) > 2:
            vx = np.gradient(east, gps["t"])
            vy = np.gradient(north, gps["t"])
            out["velocity"] = {"t": gps["t"], "vx": vx, "vy": vy, "vz": np.zeros_like(vx)}

        out["stats"]["gps_fixes"] = float(len(gps["t"]))
        out["stats"]["gps_span_m"] = float(np.hypot(east.max() - east.min(), north.max() - north.min()))

    if x["bme"] is not None and len(x["bme"]["t"]):
        alt = pressure_to_alt(x["bme"]["pressure_hpa"], x["bme"]["pressure_hpa"][0])
        out["series"]["baro alt m"] = (x["bme"]["t"], alt)

    return out
