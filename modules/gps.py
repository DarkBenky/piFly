"""Minimal GPS reader — reads NMEA from serial, returns a dict."""

import serial

PORT = "/dev/ttyS0"
BAUD = 115200


def _to_decimal(value: str, hemi: str) -> float:
    if not value:
        return 0.0
    dot = value.index(".")
    deg = float(value[:dot - 2])
    minutes = float(value[dot - 2:])
    dec = deg + minutes / 60.0
    return -dec if hemi in ("S", "W") else dec


def _checksum_ok(sentence: str) -> bool:
    if "*" not in sentence:
        return True
    data, cs = sentence.rsplit("*", 1)
    calc = 0
    for ch in data.lstrip("$"):
        calc ^= ord(ch)
    try:
        return calc == int(cs.strip(), 16)
    except ValueError:
        return False


def _parse(line: str, data: dict) -> None:
    line = line.strip()
    if not line.startswith("$") or not _checksum_ok(line):
        return
    fields = line.split("*")[0].lstrip("$").split(",")
    mid = fields[0].upper()
    try:
        if mid in ("GNRMC", "GPRMC"):
            t = fields[1]
            if len(t) >= 6:
                data["time"] = f"{t[0:2]}:{t[2:4]}:{t[4:]}"
            data["status"] = fields[2]
            if fields[3] and fields[4]:
                data["lat"] = _to_decimal(fields[3], fields[4])
            if fields[5] and fields[6]:
                data["lon"] = _to_decimal(fields[5], fields[6])
            if fields[7]:
                data["speed_kt"] = float(fields[7])
            if fields[8]:
                data["course"] = float(fields[8])
        elif mid in ("GNGGA", "GPGGA"):
            if fields[6]:
                data["fix"] = int(fields[6])
            if fields[7]:
                data["sats"] = int(fields[7])
            if fields[9]:
                data["alt_m"] = float(fields[9])
    except (IndexError, ValueError):
        pass


class GPS:
    def __init__(self, port: str = PORT, baud: int = BAUD):
        self._ser = serial.Serial(port, baud, timeout=2.0)
        self._last_time: str | None = None

    def read(self) -> dict | None:
        data: dict = {}
        while True:
            try:
                raw = self._ser.readline()
            except Exception:
                return None
            if not raw:
                continue
            line = raw.decode("ascii", errors="replace").strip()
            if not line:
                continue
            _parse(line, data)

            if line.startswith(("$GNRMC", "$GPRMC")):
                if self._last_time is not None and data.get("time") != self._last_time:
                    self._last_time = data.get("time")
                    return data
                self._last_time = data.get("time")

    def close(self) -> None:
        self._ser.close()


if __name__ == "__main__":
    gps = GPS()
    try:
        print("Reading GPS (Ctrl+C to stop)...")
        while True:
            d = gps.read()
            if d:
                lat = d.get("lat")
                lon = d.get("lon")
                print(f"Lat={f'{lat:.5f}' if lat else 'N/A'}  "
                      f"Lon={f'{lon:.5f}' if lon else 'N/A'}  "
                      f"Alt={d.get('alt_m')}m  Fix={d.get('fix')}  Sats={d.get('sats')}")
    except KeyboardInterrupt:
        print("Done.")
    finally:
        gps.close()
