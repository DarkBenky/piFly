import socket
import sqlite3

HOST = "127.0.0.1"
PORT = 5444

def initDB():
    conn = sqlite3.connect("temperature.db")
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS temperature (timestamp REAL UNIQUE, bme_temp_c REAL, bme_pressure_hpa REAL, bme_humidity_pct REAL, cpu_temp_c REAL)")
    conn.commit()
    conn.close()

def addRecord(rec):
    conn = sqlite3.connect("temperature.db")
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO temperature VALUES (?, ?, ?, ?, ?)", (rec["timestamp"], rec["bme_temp_c"], rec["bme_pressure_hpa"], rec["bme_humidity_pct"], rec["cpu_temp_c"]))
    conn.commit()
    conn.close()
    print(f"Added -> {rec}")

def migrateOldLogFiles(path: str):
    print(f"Migrating -> {path}")
    with open(path, "r") as f:
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


if __name__ == "__main__":
    initDB()
    migrateOldLogFiles("../../logs/temperature.log")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, PORT))
        s.listen()
        print(f"Listening on {HOST}:{PORT}")
        conn, addr = s.accept()
        with conn:
            print(f"Connected by {addr}")
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
