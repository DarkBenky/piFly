import json
import os
import socket
import sqlite3
import time
from datetime import datetime, timezone

from flask import Flask, request, jsonify, render_template
import plotly.graph_objects as go

HOST = "0.0.0.0"
FLASK_PORT = 5001
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "collector.db")
RETENTION_S = 3 * 3600
LIVE_WINDOW_S = 30.0
SERIES_KEYS = {"imu": "imu_series", "gps": "gps_series", "bme": "bme_series"}

app = Flask(__name__)

_last_sweep = 0.0


def _lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def _get_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def initDB():
    conn = _get_conn()
    c = conn.cursor()
    c.execute(
        "CREATE TABLE IF NOT EXISTS sessions "
        "(session_id TEXT PRIMARY KEY, label TEXT, name TEXT, note TEXT, "
        "started REAL, last_seen REAL)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS live (session_id TEXT, ts REAL, payload_json TEXT)"
    )
    c.execute("CREATE INDEX IF NOT EXISTS idx_live_session_ts ON live(session_id, ts)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_live_ts ON live(ts)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sessions_seen ON sessions(last_seen)")
    conn.commit()
    conn.close()


def _sweep():
    global _last_sweep
    now = time.time()
    if now - _last_sweep < 60.0:
        return
    _last_sweep = now
    conn = _get_conn()
    conn.execute("DELETE FROM live WHERE ts < ?", (now - RETENTION_S,))
    conn.commit()
    conn.close()


def addPayload(payload):
    session_id = payload["session"]
    ts = float(payload.get("t") or time.time())
    conn = _get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO live VALUES (?, ?, ?)", (session_id, ts, json.dumps(payload))
    )
    c.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(session_id) DO UPDATE SET label=excluded.label, "
        "name=excluded.name, note=excluded.note, last_seen=excluded.last_seen",
        (
            session_id,
            payload.get("label") or "unknown",
            payload.get("name") or "",
            payload.get("note") or "",
            float(payload.get("started") or ts),
            ts,
        ),
    )
    conn.commit()
    conn.close()
    _sweep()


def _sessionRow(row):
    return {
        "session_id": row[0],
        "label": row[1],
        "name": row[2],
        "note": row[3],
        "started": row[4],
        "last_seen": row[5],
    }


def sessionHistory(limit=40):
    conn = _get_conn()
    rows = conn.execute(
        "SELECT session_id, label, name, note, started, last_seen FROM sessions "
        "ORDER BY last_seen DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [_sessionRow(r) for r in rows]


def latestSession():
    sessions = sessionHistory(limit=1)
    return sessions[0] if sessions else None


def latestPayload(session_id=None):
    conn = _get_conn()
    if session_id:
        row = conn.execute(
            "SELECT payload_json FROM live WHERE session_id = ? ORDER BY ts DESC LIMIT 1",
            (session_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT payload_json FROM live ORDER BY ts DESC LIMIT 1"
        ).fetchone()
    conn.close()
    return json.loads(row[0]) if row else None


def liveView(payload):
    if not payload:
        return None
    return {k: v for k, v in payload.items() if not k.endswith("_series")}


def _layout(height=210):
    return dict(
        template="plotly_dark",
        paper_bgcolor="#1a1a1a",
        plot_bgcolor="#1a1a1a",
        margin=dict(l=24, r=24, t=20, b=10),
        hovermode="x unified",
        xaxis=dict(gridcolor="#2a2a2a", showgrid=False),
        yaxis=dict(gridcolor="#2a2a2a"),
        height=height,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.0,
            x=0,
            font=dict(size=9, color="#888"),
            bgcolor="rgba(0,0,0,0)",
        ),
        dragmode=False,
    )


def _scatter(x, y, color, name, yaxis="y", width=1.6):
    return go.Scatter(
        x=x,
        y=y,
        name=name,
        mode="lines",
        yaxis=yaxis,
        line=dict(color=color, width=width),
        connectgaps=True,
    )


def _times(rows):
    return [datetime.fromtimestamp(r["t"], tz=timezone.utc) for r in rows]


def _column(rows, key):
    return [r.get(key) for r in rows]


def _note(fig, text):
    fig.add_annotation(
        text=text,
        xref="paper",
        yref="paper",
        x=0.5,
        y=0.5,
        showarrow=False,
        font=dict(size=11, color="#555"),
    )


def _figures(payload):
    payload = payload or {}
    imu = payload.get("imu_series") or []
    gps = payload.get("gps_series") or []
    bme = payload.get("bme_series") or []

    fig_accel = go.Figure(layout=_layout())
    for key, color in (("ax", "#6cf"), ("ay", "#5fd6a8"), ("az", "#e8a33d")):
        fig_accel.add_trace(_scatter(_times(imu), _column(imu, key), color, key))
    fig_accel.update_layout(yaxis=dict(title="m/s²", gridcolor="#2a2a2a", zeroline=False))
    if not imu:
        _note(fig_accel, "waiting for data")

    fig_gyro = go.Figure(layout=_layout())
    for key, color in (("gx", "#b98ee8"), ("gy", "#ff8f6b"), ("gz", "#7fd1ff")):
        fig_gyro.add_trace(_scatter(_times(imu), _column(imu, key), color, key))
    fig_gyro.update_layout(yaxis=dict(title="rad/s", gridcolor="#2a2a2a", zeroline=False))
    if not imu:
        _note(fig_gyro, "waiting for data")

    fig_gps = go.Figure(layout=_layout())
    fig_gps.add_trace(_scatter(_times(gps), _column(gps, "alt_m"), "#6cf", "alt m"))
    fig_gps.add_trace(
        _scatter(_times(gps), _column(gps, "sats"), "#5fd6a8", "sats", yaxis="y2")
    )
    fig_gps.update_layout(
        yaxis=dict(title="alt m", gridcolor="#2a2a2a", zeroline=False),
        yaxis2=dict(
            title="sats", overlaying="y", side="right", showgrid=False, rangemode="tozero"
        ),
    )
    if not gps:
        _note(fig_gps, "no gps fixes")

    fig_baro = go.Figure(layout=_layout())
    fig_baro.add_trace(
        _scatter(_times(bme), _column(bme, "bme_pressure_hpa"), "#6cf", "hPa")
    )
    fig_baro.add_trace(
        _scatter(_times(bme), _column(bme, "bme_temp_c"), "#e8a33d", "°C", yaxis="y2")
    )
    fig_baro.update_layout(
        yaxis=dict(title="hPa", gridcolor="#2a2a2a", zeroline=False),
        yaxis2=dict(title="°C", overlaying="y", side="right", showgrid=False),
    )
    if not bme:
        _note(fig_baro, "waiting for data")

    return fig_accel, fig_gyro, fig_gps, fig_baro


@app.route("/api/live", methods=["POST"])
def api_live():
    payload = request.get_json(silent=True)
    if not payload or not payload.get("session"):
        return jsonify({"error": "invalid payload"}), 400
    addPayload(payload)
    return jsonify({"status": "ok"}), 200


@app.route("/api/live/latest")
def api_live_latest():
    session = latestSession()
    payload = latestPayload()
    age = None if not session else max(0.0, time.time() - (session["last_seen"] or 0))
    return jsonify({"session": session, "payload": payload, "age": age})


@app.route("/api/live/<session_id>")
def api_live_get(session_id):
    payload = latestPayload(session_id)
    if not payload:
        return jsonify({"error": "unknown session"}), 404
    return jsonify(payload)


@app.route("/api/live/<session_id>/csv")
def api_live_csv(session_id):
    stream = request.args.get("stream", "imu")
    payload = latestPayload(session_id)
    if not payload:
        return jsonify({"error": "unknown session"}), 404
    rows = payload.get(SERIES_KEYS.get(stream, "imu_series")) or []
    columns = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    lines = [",".join(columns)]
    for row in rows:
        lines.append(",".join("" if row.get(c) is None else str(row.get(c)) for c in columns))
    return (
        "\n".join(lines),
        200,
        {
            "Content-Type": "text/csv",
            "Content-Disposition": f"attachment; filename={session_id}-{stream}.csv",
        },
    )


@app.route("/api/sessions")
def api_sessions():
    now = time.time()
    sessions = sessionHistory()
    for s in sessions:
        s["live"] = (now - (s["last_seen"] or 0)) < LIVE_WINDOW_S
    return jsonify(sessions)


@app.route("/")
def dashboard():
    sessions = sessionHistory()
    requested = request.args.get("session", "")
    session = next((s for s in sessions if s["session_id"] == requested), None)
    follow = session is None
    if follow and sessions:
        session = sessions[0]
    payload = latestPayload(session["session_id"]) if session else None
    age = time.time() - session["last_seen"] if session else None
    fig_accel, fig_gyro, fig_gps, fig_baro = _figures(payload)
    return render_template(
        "dashboard.html",
        graph_accel=fig_accel.to_json(),
        graph_gyro=fig_gyro.to_json(),
        graph_gps=fig_gps.to_json(),
        graph_baro=fig_baro.to_json(),
        sessions=sessions,
        session=session,
        follow=follow,
        age=age,
        live=liveView(payload),
    )


if __name__ == "__main__":
    initDB()
    ip = _lan_ip()
    print(f"Web  listening on {ip}:{FLASK_PORT}")
    print(f"Pi upload target: http://{ip}:{FLASK_PORT}/api/live")
    app.run(host=HOST, port=FLASK_PORT, debug=False)
