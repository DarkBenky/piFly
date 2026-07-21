import socket
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from flask import Flask, request, jsonify, render_template
import plotly.graph_objects as go

HOST = "0.0.0.0"
PORT = 5444
FLASK_PORT = 5000

app = Flask(__name__)


def _lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


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


def _make_figures(data):
    times = [datetime.fromtimestamp(r["timestamp"], tz=timezone.utc) for r in data]
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

    fig_temp = go.Figure(layout=layout_base)
    fig_temp.add_trace(go.Scatter(
        x=times, y=[r["bme_temp_c"] for r in data],
        mode="lines", name="BME280", line=dict(color="#4da6ff", width=1.8),
    ))
    fig_temp.add_trace(go.Scatter(
        x=times, y=[r["cpu_temp_c"] for r in data],
        mode="lines", name="CPU", line=dict(color="#ff6666", width=1.4),
    ))
    fig_temp.update_layout(
        yaxis=dict(title="°C", gridcolor="#2a2a2a", zeroline=False),
        showlegend=False,
    )

    fig_hum = go.Figure(layout=layout_base)
    fig_hum.add_trace(go.Scatter(
        x=times, y=[r["bme_humidity_pct"] for r in data],
        mode="lines", name="Humidity", line=dict(color="#66cc66", width=1.8),
        fill="tozeroy", fillcolor="rgba(102,204,102,0.08)",
    ))
    fig_hum.update_layout(
        yaxis=dict(title="%", gridcolor="#2a2a2a", range=[0, 100], zeroline=False),
    )

    fig_pres = go.Figure(layout=layout_base)
    fig_pres.add_trace(go.Scatter(
        x=times, y=[r["bme_pressure_hpa"] for r in data],
        mode="lines", name="Pressure", line=dict(color="#ffaa33", width=1.8),
        fill="tozeroy", fillcolor="rgba(255,170,51,0.08)",
    ))
    fig_pres.update_layout(
        yaxis=dict(title="hPa", gridcolor="#2a2a2a", zeroline=False),
    )

    return fig_temp, fig_hum, fig_pres


@app.route("/api/temperature")
def api_temperature():
    hours = request.args.get("hours", type=float)
    start = request.args.get("start", type=float)
    end = request.args.get("end", type=float)
    data = _query(hours=hours, start=start, end=end)
    return jsonify(data)


@app.route("/")
def dashboard():
    hours = request.args.get("hours", type=float)
    start = request.args.get("start", type=float)
    end = request.args.get("end", type=float)

    if start and end:
        data = _query(start=start, end=end)
        hours = None
    elif hours:
        data = _query(hours=hours)
    else:
        hours = 24
        data = _query(hours=24)

    fig_temp, fig_hum, fig_pres = _make_figures(data)

    return render_template(
        "dashboard.html",
        graph_temp=fig_temp.to_json(),
        graph_hum=fig_hum.to_json(),
        graph_pres=fig_pres.to_json(),
        hours=hours,
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
