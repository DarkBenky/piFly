import time
from collections import deque


class RateMeter:
    def __init__(self, window=5.0):
        self.window = window
        self._events = deque()
        self.total = 0

    def add(self, count=1, now=None):
        now = time.monotonic() if now is None else now
        self._events.append((now, count))
        self.total += count
        self._trim(now)

    def _trim(self, now):
        limit = now - self.window
        while self._events and self._events[0][0] < limit:
            self._events.popleft()

    def rate(self, now=None):
        now = time.monotonic() if now is None else now
        self._trim(now)
        if not self._events:
            return 0.0
        span = now - self._events[0][0]
        if span <= 0:
            return 0.0
        return sum(count for _, count in self._events) / span


class Counter:
    def __init__(self):
        self.value = 0

    def inc(self, count=1):
        self.value += count
        return self.value


class BucketSeries:
    def __init__(self, fields, bucket_s=0.5, max_buckets=600):
        self.fields = tuple(fields)
        self.bucket_s = bucket_s
        self._buckets = deque(maxlen=max_buckets)
        self._index = None
        self._current = None

    def add(self, t, values):
        index = int(t / self.bucket_s)
        if index != self._index:
            self._index = index
            self._current = {name: 0.0 for name in self.fields}
            self._current["_t"] = index * self.bucket_s
            self._current["_n"] = 0
            self._buckets.append(self._current)
        for name, value in zip(self.fields, values):
            self._current[name] += value
        self._current["_n"] += 1

    def snapshot(self, limit=None):
        buckets = list(self._buckets)
        if limit:
            buckets = buckets[-limit:]
        rows = []
        for bucket in buckets:
            count = bucket["_n"] or 1
            row = {"t": round(bucket["_t"], 3), "n": bucket["_n"]}
            for name in self.fields:
                row[name] = round(bucket[name] / count, 5)
            rows.append(row)
        return rows

    def latest(self, count=1):
        rows = self.snapshot(limit=count)
        return rows[-1] if rows else None

    def clear(self):
        self._buckets.clear()
        self._current = None
        self._index = None
