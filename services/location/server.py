import socket
import sqlite3

from flask import Flask, request, jsonify, render_template

HOST = "0.0.0.0"
FLASK_PORT = 5001

app = Flask(__name__)

COLS = ["timestamp", "roll", "pitch", "yaw", "qw", "qx", "qy", "qz",
        "x_m", "y_m", "alt_m", "pressure_hpa", "temp_c"]


def _lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def _get_conn():
    conn = sqlite3.connect("location.db")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def initDB():
    conn = _get_conn()
    c = conn.cursor()
    c.execute(
        "CREATE TABLE IF NOT EXISTS location "
        "(timestamp REAL UNIQUE, roll REAL, pitch REAL, yaw REAL, "
        "qw REAL, qx REAL, qy REAL, qz REAL, "
        "x_m REAL, y_m REAL, alt_m REAL, pressure_hpa REAL, temp_c REAL)"
    )
    conn.commit()
    conn.close()


def addRecord(rec):
    conn = _get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO location VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (rec["timestamp"], rec["roll"], rec["pitch"], rec["yaw"],
         rec["qw"], rec["qx"], rec["qy"], rec["qz"],
         rec.get("x_m", 0.0), rec.get("y_m", 0.0),
         rec["alt_m"], rec["pressure_hpa"], rec["temp_c"]),
    )
    conn.commit()
    conn.close()


@app.route("/api/ingest", methods=["POST"])
def api_ingest():
    data = request.get_json()
    if not data:
        return jsonify({"error": "invalid JSON"}), 400
    required = ["timestamp", "roll", "pitch", "yaw",
                "qw", "qx", "qy", "qz", "alt_m", "pressure_hpa", "temp_c"]
    for key in required:
        if key not in data:
            return jsonify({"error": f"missing field: {key}"}), 400
    addRecord(data)
    return jsonify({"status": "ok"}), 200


@app.route("/api/state")
def api_state():
    n = max(1, min(request.args.get("n", type=int, default=300), 2000))
    conn = _get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM location ORDER BY timestamp DESC LIMIT ?", (n,))
    rows = c.fetchall()
    conn.close()
    if not rows:
        return jsonify({"latest": None, "history": []})
    history = [dict(zip(COLS, r)) for r in reversed(rows)]
    return jsonify({"latest": history[-1], "history": history})


@app.route("/")
def dashboard():
    return render_template("dashboard.html")


if __name__ == "__main__":
    initDB()
    ip = _lan_ip()
    print(f"Location dashboard -> http://{ip}:{FLASK_PORT}")
    app.run(host="0.0.0.0", port=FLASK_PORT, debug=False)
