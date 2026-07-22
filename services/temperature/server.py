import json
import os
import socket
import sqlite3
import threading
import urllib.request
from datetime import datetime, timedelta, timezone

from flask import Flask, request, jsonify, render_template
import plotly.graph_objects as go

HOST = "0.0.0.0"
PORT = 5444
FLASK_PORT = 5000
WEATHER_LAT = 48.208803
WEATHER_LON = 17.146854

app = Flask(__name__)

_weather_cache = None
_weather_cache_time = None


def _lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def _to_fahrenheit(c):
    return c * 9.0 / 5.0 + 32.0


def _convert_temp(val, unit):
    return _to_fahrenheit(val) if unit == "f" else val


def initDB():
    conn = sqlite3.connect("temperature.db")
    c = conn.cursor()
    c.execute(
        "CREATE TABLE IF NOT EXISTS temperature "
        "(timestamp REAL UNIQUE, bme_temp_c REAL, bme_pressure_hpa REAL, "
        "bme_humidity_pct REAL, cpu_temp_c REAL)"
    )
    conn.commit()
    conn.close()


def addRecord(rec):
    conn = sqlite3.connect("temperature.db")
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO temperature VALUES (?, ?, ?, ?, ?)",
        (rec["timestamp"], rec["bme_temp_c"], rec["bme_pressure_hpa"],
         rec["bme_humidity_pct"], rec["cpu_temp_c"]),
    )
    conn.commit()
    conn.close()


def migrateOldLogFiles(path: str):
    print(f"Migrating -> {path}")
    with open(path, "r") as f:
        next(f)
        for line in f:
            parts = line.strip().split(",")
            rec = {
                "timestamp": float(parts[0]),
                "bme_temp_c": float(parts[1]),
                "bme_pressure_hpa": float(parts[2]),
                "bme_humidity_pct": float(parts[3]),
                "cpu_temp_c": float(parts[4]),
            }
            addRecord(rec)


def _query(hours=None, start=None, end=None):
    conn = sqlite3.connect("temperature.db")
    c = conn.cursor()
    if start is not None and end is not None:
        c.execute(
            "SELECT * FROM temperature WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
            (start, end),
        )
    elif hours is not None:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp()
        c.execute(
            "SELECT * FROM temperature WHERE timestamp >= ? ORDER BY timestamp",
            (cutoff,),
        )
    else:
        c.execute("SELECT * FROM temperature ORDER BY timestamp")
    rows = c.fetchall()
    conn.close()
    return [
        {
            "timestamp": r[0],
            "bme_temp_c": r[1],
            "bme_pressure_hpa": r[2],
            "bme_humidity_pct": r[3],
            "cpu_temp_c": r[4],
        }
        for r in rows
    ]


_WMO_CODES = {
    0: ("☀️", "Clear"), 1: ("🌤️", "Mostly clear"), 2: ("⛅", "Partly cloudy"), 3: ("☁️", "Overcast"),
    45: ("🌫️", "Fog"), 48: ("🌫️", "Rime fog"),
    51: ("🌦️", "Light drizzle"), 53: ("🌦️", "Drizzle"), 55: ("🌦️", "Heavy drizzle"),
    61: ("🌧️", "Light rain"), 63: ("🌧️", "Rain"), 65: ("🌧️", "Heavy rain"),
    71: ("❄️", "Light snow"), 73: ("❄️", "Snow"), 75: ("❄️", "Heavy snow"),
    80: ("🌦️", "Rain showers"), 81: ("🌦️", "Moderate showers"), 82: ("🌦️", "Violent showers"),
    95: ("⛈️", "Thunderstorm"), 96: ("⛈️", "T-storm + hail"), 99: ("⛈️", "Severe T-storm"),
}


def _weather_code(code):
    return _WMO_CODES.get(code, ("🌡️", "Unknown"))


def _fetch_weather():
    global _weather_cache, _weather_cache_time
    now = datetime.now(timezone.utc)
    if _weather_cache and _weather_cache_time and (now - _weather_cache_time).seconds < 1800:
        return _weather_cache

    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={WEATHER_LAT}&longitude={WEATHER_LON}"
        f"&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m"
        f"&hourly=temperature_2m,relative_humidity_2m,pressure_msl"
        f"&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max"
        f"&timezone=auto&forecast_days=3"
    )
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            raw = json.loads(resp.read())

        hourly_data = []
        h = raw["hourly"]
        for i, t in enumerate(h["time"]):
            hourly_data.append({
                "timestamp": datetime.fromisoformat(t).replace(tzinfo=timezone.utc).timestamp(),
                "temp_c": h["temperature_2m"][i],
                "humidity_pct": h["relative_humidity_2m"][i],
                "pressure_hpa": h["pressure_msl"][i],
            })

        daily_data = []
        d = raw["daily"]
        for i, dt in enumerate(d["time"]):
            code = d["weather_code"][i]
            emoji, desc = _weather_code(code)
            daily_data.append({
                "date": dt,
                "emoji": emoji,
                "desc": desc,
                "temp_max": d["temperature_2m_max"][i],
                "temp_min": d["temperature_2m_min"][i],
                "rain_pct": d["precipitation_probability_max"][i],
                "wind_max": d["wind_speed_10m_max"][i],
            })

        cur = raw["current"]
        code = cur["weather_code"]
        emoji, desc = _weather_code(code)
        current_data = {
            "temp_c": cur["temperature_2m"],
            "humidity_pct": cur["relative_humidity_2m"],
            "apparent_temp_c": cur["apparent_temperature"],
            "wind_kmh": cur["wind_speed_10m"],
            "emoji": emoji,
            "desc": desc,
        }

        result = {"hourly": hourly_data, "daily": daily_data, "current": current_data}
        _weather_cache = result
        _weather_cache_time = now
        return result
    except Exception as e:
        print(f"Weather fetch failed: {e}")
        return _weather_cache or {"hourly": [], "daily": [], "current": None}


def _tight_range(values, pad=0.15):
    lo, hi = min(values), max(values)
    if lo == hi:
        return [lo - 1, hi + 1]
    span = hi - lo
    extra = max(span * pad, 0.5)
    return [lo - extra, hi + extra]


def _moving_average(values, window=3):
    """Simple centred moving average — trims `window//2` points from each end."""
    if len(values) < window:
        return values
    half = window // 2
    smoothed = []
    for i in range(half, len(values) - half):
        smoothed.append(sum(values[i - half:i + half + 1]) / window)
    return smoothed


def _make_scatter(x, y, color, width=1.8, dash=None, fill=None, fillcolor=None):
    trace = go.Scatter(
        x=x, y=y, mode="lines",
        line=dict(color=color, width=width, dash=dash, shape="spline"),
    )
    if fill:
        trace.fill = fill
        trace.fillcolor = fillcolor
    return trace

SMOOTHING_WINDOW = 5

def _make_figures(data, compare_yesterday=None, compare_week=None, weather=None, unit="c"):
    weather_hourly = weather["hourly"] if weather else None
    times = [datetime.fromtimestamp(r["timestamp"], tz=timezone.utc) for r in data]

    bme_vals = [_convert_temp(r["bme_temp_c"], unit) for r in data]
    cpu_vals = [_convert_temp(r["cpu_temp_c"], unit) for r in data]
    hum_vals = [r["bme_humidity_pct"] for r in data]
    pres_vals = [r["bme_pressure_hpa"] for r in data]

    # smooth the main traces — trim x to match
    half = SMOOTHING_WINDOW // 2
    bme_smooth = _moving_average(bme_vals, SMOOTHING_WINDOW) if len(bme_vals) >= SMOOTHING_WINDOW else bme_vals
    cpu_smooth = _moving_average(cpu_vals, SMOOTHING_WINDOW) if len(cpu_vals) >= SMOOTHING_WINDOW else cpu_vals
    hum_smooth = _moving_average(hum_vals, SMOOTHING_WINDOW) if len(hum_vals) >= SMOOTHING_WINDOW else hum_vals
    pres_smooth = _moving_average(pres_vals, SMOOTHING_WINDOW) if len(pres_vals) >= SMOOTHING_WINDOW else pres_vals
    times_smooth = times[half:len(times)-half] if len(times) >= SMOOTHING_WINDOW else times

    temp_unit = "°F" if unit == "f" else "°C"

    layout_base = dict(
        template="plotly_dark",
        paper_bgcolor="#1a1a1a",
        plot_bgcolor="#1a1a1a",
        margin=dict(l=20, r=20, t=10, b=10),
        hovermode="x unified",
        xaxis=dict(gridcolor="#2a2a2a", showgrid=False),
        yaxis=dict(gridcolor="#2a2a2a"),
        height=200,
        showlegend=False,
        dragmode=False,
    )

    def _add_compare(fig, key, shift_h, color):
        if compare_yesterday:
            ct = [datetime.fromtimestamp(r["timestamp"] + shift_h * 3600, tz=timezone.utc) for r in compare_yesterday]
            cv = [_convert_temp(r[key], unit) if key in ("bme_temp_c", "cpu_temp_c") else r[key] for r in compare_yesterday]
            fig.add_trace(_make_scatter(ct, cv, color, width=1.2, dash="dash"))
        if compare_week:
            ct = [datetime.fromtimestamp(r["timestamp"] + shift_h * 3600, tz=timezone.utc) for r in compare_week]
            cv = [_convert_temp(r[key], unit) if key in ("bme_temp_c", "cpu_temp_c") else r[key] for r in compare_week]
            fig.add_trace(_make_scatter(ct, cv, color, width=1.0, dash="dot"))

    fig_bme = go.Figure(layout=layout_base)
    fig_bme.add_trace(_make_scatter(times_smooth, bme_smooth, "#4da6ff"))
    _add_compare(fig_bme, "bme_temp_c", 24, "#4da6ff")
    all_bme = list(bme_vals)
    if compare_yesterday:
        all_bme += [_convert_temp(r["bme_temp_c"], unit) for r in compare_yesterday]
    if compare_week:
        all_bme += [_convert_temp(r["bme_temp_c"], unit) for r in compare_week]
    if weather_hourly:
        wt = [datetime.fromtimestamp(w["timestamp"], tz=timezone.utc) for w in weather_hourly]
        wv = [_convert_temp(w["temp_c"], unit) for w in weather_hourly]
        fig_bme.add_trace(_make_scatter(wt, wv, "#88ccff", width=1.0, dash="dot"))
        all_bme += wv
    fig_bme.update_layout(yaxis=dict(title=temp_unit, gridcolor="#2a2a2a", zeroline=False, range=_tight_range(all_bme)))

    fig_cpu = go.Figure(layout=layout_base)
    fig_cpu.add_trace(_make_scatter(times_smooth, cpu_smooth, "#ff6666"))
    _add_compare(fig_cpu, "cpu_temp_c", 24, "#ff6666")
    all_cpu = list(cpu_vals)
    if compare_yesterday:
        all_cpu += [_convert_temp(r["cpu_temp_c"], unit) for r in compare_yesterday]
    if compare_week:
        all_cpu += [_convert_temp(r["cpu_temp_c"], unit) for r in compare_week]
    fig_cpu.update_layout(yaxis=dict(title=temp_unit, gridcolor="#2a2a2a", zeroline=False, range=_tight_range(all_cpu)))

    fig_hum = go.Figure(layout=layout_base)
    fig_hum.add_trace(_make_scatter(times_smooth, hum_smooth, "#66cc66", fill="tozeroy", fillcolor="rgba(102,204,102,0.08)"))
    _add_compare(fig_hum, "bme_humidity_pct", 24, "#66cc66")
    all_hum = list(hum_vals)
    if compare_yesterday:
        all_hum += [r["bme_humidity_pct"] for r in compare_yesterday]
    if compare_week:
        all_hum += [r["bme_humidity_pct"] for r in compare_week]
    if weather_hourly:
        wt = [datetime.fromtimestamp(w["timestamp"], tz=timezone.utc) for w in weather_hourly]
        wv = [w["humidity_pct"] for w in weather_hourly]
        fig_hum.add_trace(_make_scatter(wt, wv, "#aaddaa", width=1.0, dash="dot"))
        all_hum += wv
    fig_hum.update_layout(yaxis=dict(title="%", gridcolor="#2a2a2a", zeroline=False, range=_tight_range(all_hum)))

    fig_pres = go.Figure(layout=layout_base)
    fig_pres.add_trace(_make_scatter(times_smooth, pres_smooth, "#ffaa33", fill="tozeroy", fillcolor="rgba(255,170,51,0.08)"))
    _add_compare(fig_pres, "bme_pressure_hpa", 24, "#ffaa33")
    all_pres = list(pres_vals)
    if compare_yesterday:
        all_pres += [r["bme_pressure_hpa"] for r in compare_yesterday]
    if compare_week:
        all_pres += [r["bme_pressure_hpa"] for r in compare_week]
    if weather_hourly:
        wt = [datetime.fromtimestamp(w["timestamp"], tz=timezone.utc) for w in weather_hourly]
        wv = [w["pressure_hpa"] for w in weather_hourly]
        fig_pres.add_trace(_make_scatter(wt, wv, "#ffcc88", width=1.0, dash="dot"))
        all_pres += wv
    fig_pres.update_layout(yaxis=dict(title="hPa", gridcolor="#2a2a2a", zeroline=False, range=_tight_range(all_pres)))

    return fig_bme, fig_cpu, fig_hum, fig_pres


@app.route("/api/temperature")
def api_temperature():
    hours = request.args.get("hours", type=float)
    start = request.args.get("start", type=float)
    end = request.args.get("end", type=float)
    unit = request.args.get("unit", "c")
    data = _query(hours=hours, start=start, end=end)
    if unit == "f":
        for r in data:
            r["bme_temp_c"] = _to_fahrenheit(r["bme_temp_c"])
            r["cpu_temp_c"] = _to_fahrenheit(r["cpu_temp_c"])
    return jsonify(data)


@app.route("/api/current")
def api_current():
    data = _query(hours=1)
    if not data:
        return jsonify({})
    return jsonify(data[-1])


@app.route("/api/temperature/csv")
def api_temperature_csv():
    hours = request.args.get("hours", type=float)
    start = request.args.get("start", type=float)
    end = request.args.get("end", type=float)
    unit = request.args.get("unit", "c")
    data = _query(hours=hours, start=start, end=end)

    lines = ["timestamp,bme_temp,pressure_hpa,humidity_pct,cpu_temp"]
    for r in data:
        bme_t = _convert_temp(r["bme_temp_c"], unit)
        cpu_t = _convert_temp(r["cpu_temp_c"], unit)
        lines.append(f"{r['timestamp']},{bme_t},{r['bme_pressure_hpa']},{r['bme_humidity_pct']},{cpu_t}")

    csv_content = "\n".join(lines)
    return (
        csv_content,
        200,
        {
            "Content-Type": "text/csv",
            "Content-Disposition": "attachment; filename=temperature.csv",
        },
    )


@app.route("/")
def dashboard():
    hours = request.args.get("hours", type=float)
    start = request.args.get("start", type=float)
    end = request.args.get("end", type=float)
    unit = request.args.get("unit", "c")
    compare = request.args.get("compare", "")
    show_weather = request.args.get("weather", "0")

    if start and end:
        data = _query(start=start, end=end)
        hours = None
    elif hours:
        data = _query(hours=hours)
    else:
        hours = 24
        data = _query(hours=24)

    compare_yesterday = None
    compare_week = None
    if compare == "yesterday":
        if start and end:
            compare_yesterday = _query(start=start - 86400, end=end - 86400)
        else:
            h = hours or 24
            compare_yesterday = _query(
                start=(datetime.now(timezone.utc) - timedelta(hours=h + 24)).timestamp(),
                end=(datetime.now(timezone.utc) - timedelta(hours=24)).timestamp(),
            )
    elif compare == "week":
        if start and end:
            compare_week = _query(start=start - 604800, end=end - 604800)
        else:
            h = hours or 24
            compare_week = _query(
                start=(datetime.now(timezone.utc) - timedelta(hours=h + 168)).timestamp(),
                end=(datetime.now(timezone.utc) - timedelta(hours=168)).timestamp(),
            )

    weather_data = _fetch_weather()
    weather = weather_data if show_weather == "1" else None

    fig_bme, fig_cpu, fig_hum, fig_pres = _make_figures(
        data,
        compare_yesterday=compare_yesterday,
        compare_week=compare_week,
        weather=weather,
        unit=unit,
    )

    latest = data[-1] if data else None
    return render_template(
        "dashboard.html",
        graph_bme=fig_bme.to_json(),
        graph_cpu=fig_cpu.to_json(),
        graph_hum=fig_hum.to_json(),
        graph_pres=fig_pres.to_json(),
        hours=hours,
        unit=unit,
        compare=compare,
        weather=show_weather,
        latest=latest,
        weather_data=weather_data,
    )


def _run_tcp_listener():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, PORT))
        s.listen()
        while True:
            conn, addr = s.accept()
            print(f"TCP connected by {addr}")
            with conn:
                while True:
                    data = conn.recv(1024)
                    if not data:
                        break
                    parts = data.decode().strip().split(",")
                    rec = {
                        "timestamp": float(parts[0]),
                        "bme_temp_c": float(parts[1]),
                        "bme_pressure_hpa": float(parts[2]),
                        "bme_humidity_pct": float(parts[3]),
                        "cpu_temp_c": float(parts[4]),
                    }
                    print(f"Received -> {rec}")
                    addRecord(rec)


if __name__ == "__main__":
    initDB()
    t = threading.Thread(target=_run_tcp_listener, daemon=True)
    t.start()
    ip = _lan_ip()
    print(f"TCP  listening on {ip}:{PORT}")
    print(f"Web  listening on {ip}:{FLASK_PORT}")
    app.run(host="0.0.0.0", port=FLASK_PORT, debug=False)
