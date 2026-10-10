#!/usr/bin/env python3
"""Bounded, read-only Mihomo telemetry and loopback dashboard (Python >= 3.9)."""
import argparse
import collections
import csv
import datetime as dt
import http.client
import http.server
import io
import json
import logging
import logging.handlers
import os
from pathlib import Path
import re
import signal
import socket
import sqlite3
import threading
import time
from urllib.parse import parse_qs, urlsplit

VERSION = "1.0.0"
TZ = dt.timezone(dt.timedelta(hours=8))
RETENTION = 35
LIMITS = {"domain": 2000, "node": 64, "process": 128, "route": 4}
ROUTES = ("proxy", "direct", "blocked", "unknown")
ACTIVE_LIMIT = 8192
MAX_PAGES = 12288  # 48 MiB DB + at most about 48 MiB DELETE-mode rollback journal.
MAX_RESPONSE = 4 * 1024 * 1024
BASE = Path.home() / "Library/Application Support/clash-audit"
DEFAULT_SOCKET = Path("/var/run/clash-verge-service/users/%d/verge-mihomo.sock" % os.getuid())


def date_at(ts):
    return dt.datetime.fromtimestamp(ts, TZ).date().isoformat()


def iso(ts):
    return dt.datetime.fromtimestamp(ts, TZ).isoformat(timespec="seconds")


def clipped(value, size):
    return str(value or "").encode("utf-8")[:size].decode("utf-8", "ignore")


def domain_name(metadata):
    value = metadata.get("host") or metadata.get("sniffHost") or metadata.get("destinationIP")
    if not value:
        return "__unknown__"
    try:
        return str(value).lower().rstrip(".").encode("idna").decode("ascii")[:253]
    except (UnicodeError, ValueError):
        return "__invalid_host__"


def dimensions(item):
    m = item.get("metadata") or {}
    chain = item.get("chains") or []
    node = clipped(chain[0] if chain else "__unknown__", 128)
    upper = node.upper()
    route = "direct" if upper == "DIRECT" else "blocked" if upper.startswith("REJECT") else "proxy" if chain else "unknown"
    process = clipped(m.get("process") or os.path.basename(m.get("processPath") or "") or "__unknown__", 128)
    return route, {"domain": domain_name(m), "node": node, "process": process, "route": route}


def byte_pair(item, total=False):
    keys = ("uploadTotal", "downloadTotal") if total else ("upload", "download")
    values = tuple(int(item[k]) for k in keys)
    if any(x < 0 or x > (1 << 62) for x in values):
        raise ValueError("Invalid byte counters")
    return values


def connection_start(value):
    # Go RFC3339Nano may have nine fractional digits; keep Python 3.9 compatibility.
    value = str(value or "").replace("Z", "+00:00")
    value = re.sub(r"(\.\d{6})\d+", r"\1", value)
    try:
        parsed = dt.datetime.fromisoformat(value)
        return parsed.timestamp() if parsed.tzinfo else None
    except ValueError:
        return None


class Mihomo:
    def __init__(self, path):
        self.path = path

    def get(self, path):
        conn = http.client.HTTPConnection("localhost", timeout=5)
        conn.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.sock.settimeout(5)
        try:
            conn.sock.connect(self.path)
            conn.request("GET", path)
            response = conn.getresponse()
            data = response.read(MAX_RESPONSE + 1)
            if response.status != 200 or len(data) > MAX_RESPONSE:
                raise ValueError("Controller response rejected: HTTP %s, bytes %s" % (response.status, len(data)))
            return json.loads(data)
        finally:
            conn.close()

    def identity(self):
        s = os.stat(self.path)
        return "%s:%s:%s" % (s.st_dev, s.st_ino, s.st_ctime_ns)


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.db = sqlite3.connect(str(path), timeout=10)
        self.db.execute("PRAGMA page_size=4096")
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA temp_store=MEMORY")
        self.db.execute("PRAGMA cache_size=-1024")
        self.db.execute("PRAGMA max_page_count=%d" % MAX_PAGES)
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS daily (
          day TEXT, kind TEXT, route TEXT, label TEXT, tx INTEGER, rx INTEGER,
          PRIMARY KEY(day,kind,route,label)) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS hourly (
          hour TEXT, kind TEXT, tx INTEGER, rx INTEGER,
          PRIMARY KEY(hour,kind)) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY,value TEXT) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS active (id TEXT PRIMARY KEY,tx INTEGER,rx INTEGER) WITHOUT ROWID;
        """)
        self.db.commit()

    def meta(self, name, default=None):
        row = self.db.execute("SELECT value FROM metadata WHERE name=?", (name,)).fetchone()
        return json.loads(row[0]) if row else default

    def close(self):
        self.db.close()


class Accounting:
    def __init__(self, store, now=None):
        self.store = store
        now = time.time() if now is None else now
        self.health = store.meta("health", {"first_started": now, "gaps": [], "errors": 0, "core_resets": 0, "overflow_records": 0})
        self.health.update(version=VERSION, started=now, pid=os.getpid())
        self.last = store.meta("last")
        self.active = {cid: (tx, rx) for cid, tx, rx in store.db.execute("SELECT id,tx,rx FROM active")}
        self.pending = collections.defaultdict(lambda: [0, 0])
        self.hours = collections.defaultdict(lambda: [0, 0])
        self.known = set()
        self.cardinality = collections.Counter()
        self.current_day = None
        self.last_flush = now

    def change_day(self, day, now):
        if self.current_day == day:
            return
        if self.current_day is not None:
            self.flush(now)
        self.current_day = day
        self.known = {(kind, route, label) for kind, route, label in self.store.db.execute(
            "SELECT kind,route,label FROM daily WHERE day=?", (day,))}
        self.cardinality = collections.Counter(kind for kind, route, label in self.known if label != "__other__")

    def add(self, day, kind, route, label, values):
        key = (kind, route, label)
        if kind in LIMITS and key not in self.known:
            if self.cardinality[kind] >= LIMITS[kind]:
                label = "__other__"
                key = (kind, route, label)
                self.health["overflow_records"] += 1
            else:
                self.cardinality[kind] += 1
            self.known.add(key)
        bucket = self.pending[(day, kind, route, label)]
        for i in (0, 1):
            bucket[i] += values[i]

    def gap(self, start, end, reason):
        self.health["gaps"] = (self.health.get("gaps", []) + [{"from": start, "to": end, "reason": reason}])[-32:]

    def ingest(self, snapshot, identity, now=None):
        now = time.time() if now is None else now
        day = date_at(now)
        self.change_day(day, now)
        totals = byte_pair(snapshot, True)
        rows = snapshot.get("connections")
        if not isinstance(rows, list):
            raise ValueError("Missing connection list")
        # Validate before updating any accumulator.
        parsed = []
        for item in rows[:ACTIVE_LIMIT]:
            cid = str(item["id"])
            if len(cid) > 80:
                continue
            parsed.append((cid, byte_pair(item), dimensions(item), connection_start(item.get("start"))))
        reset = self.last is not None and (identity != self.last["identity"] or any(totals[i] < self.last["totals"][i] for i in (0, 1)))
        baseline = self.last is None or reset
        if reset:
            self.health["core_resets"] += 1
            self.gap(self.last["time"], now, "kernel_restart_baseline")
        elif self.last is not None and now - self.last["time"] > 3.5:
            self.gap(self.last["time"], now, "sampling_gap")
        global_delta = (0, 0) if baseline else tuple(totals[i] - self.last["totals"][i] for i in (0, 1))
        self.add(day, "total", "all", "all", global_delta)
        observed = [0, 0]
        new_active = {}
        for cid, current, (route, dims), born in parsed:
            if baseline:
                previous = current
            elif cid in self.active:
                previous = self.active[cid]
            elif born is not None and born >= self.last["time"]:
                previous = (0, 0)
            else:
                # A pre-existing connection first seen after an overflow/gap cannot
                # safely contribute its lifetime bytes to the current interval.
                previous = current
                self.health["late_seen_connections"] = self.health.get("late_seen_connections", 0) + 1
            delta = tuple(max(0, current[i] - previous[i]) for i in (0, 1))
            new_active[cid] = current
            for i in (0, 1):
                observed[i] += delta[i]
            if delta != (0, 0):
                for kind, label in dims.items():
                    self.add(day, kind, route, label, delta)
        self.add(day, "observed", "all", "all", observed)
        if len(rows) > ACTIVE_LIMIT:
            self.health["active_limit_events"] = self.health.get("active_limit_events", 0) + 1
        hour = dt.datetime.fromtimestamp(now, TZ).strftime("%Y-%m-%d %H:00")
        for kind, delta in (("total", global_delta), ("observed", observed)):
            for i in (0, 1):
                self.hours[(hour, kind)][i] += delta[i]
        self.active = new_active
        self.last = {"identity": identity, "totals": totals, "time": now}
        self.health.update(last_success=now, active_connections=len(rows), current_error=None)

    def error(self, exc):
        self.health["errors"] += 1
        self.health["current_error"] = clipped(type(exc).__name__ + ": " + str(exc), 240)

    def flush(self, now=None):
        now = time.time() if now is None else now
        cutoff = (dt.datetime.fromtimestamp(now, TZ).date() - dt.timedelta(days=RETENTION - 1)).isoformat()
        hour_cutoff = (dt.datetime.fromtimestamp(now, TZ) - dt.timedelta(days=7)).strftime("%Y-%m-%d %H:00")
        # One atomic checkpoint: counters and active baselines cannot be replayed twice.
        with self.store.db:
            self.store.db.execute("DELETE FROM daily WHERE day<?", (cutoff,))
            self.store.db.execute("DELETE FROM hourly WHERE hour<?", (hour_cutoff,))
            for (day, kind, route, label), (tx, rx) in self.pending.items():
                self.store.db.execute("""INSERT INTO daily VALUES (?,?,?,?,?,?)
                  ON CONFLICT(day,kind,route,label) DO UPDATE SET tx=tx+excluded.tx,rx=rx+excluded.rx""",
                                      (day, kind, route, label, tx, rx))
            for (hour, kind), (tx, rx) in self.hours.items():
                self.store.db.execute("""INSERT INTO hourly VALUES (?,?,?,?)
                  ON CONFLICT(hour,kind) DO UPDATE SET tx=tx+excluded.tx,rx=rx+excluded.rx""", (hour, kind, tx, rx))
            self.store.db.execute("DELETE FROM active")
            self.store.db.executemany("INSERT INTO active VALUES (?,?,?)", ((cid, *counts) for cid, counts in self.active.items()))
            self.health["last_flush"] = now
            for name, value in (("health", self.health), ("last", self.last)):
                self.store.db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (name, json.dumps(value)))
        self.pending.clear()
        self.hours.clear()
        self.last_flush = now


def bounds(query):
    today = dt.datetime.now(TZ).date()
    period = query.get("period", ["today"])[0]
    if "from" in query and "to" in query:
        start, end = (dt.date.fromisoformat(query[x][0]) for x in ("from", "to"))
    elif period == "yesterday":
        start = end = today - dt.timedelta(days=1)
    elif period in ("today", "7d", "30d", "35d"):
        end = today
        start = today - dt.timedelta(days={"today": 0, "7d": 6, "30d": 29, "35d": 34}[period])
    else:
        raise ValueError("Invalid date range")
    if start > end or (end - start).days > 366:
        raise ValueError("Invalid date range")
    return start.isoformat(), end.isoformat()


def report(path, query):
    start, end = bounds(query)
    kind = query.get("kind", ["domain"])[0]
    route = query.get("route", ["proxy"])[0]
    if kind not in LIMITS or route not in ROUTES + ("all",):
        raise ValueError("Invalid grouping")
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA query_only=ON")
        totals = {r["kind"]: {"tx": r["tx"], "rx": r["rx"]} for r in db.execute(
            "SELECT kind,SUM(tx) tx,SUM(rx) rx FROM daily WHERE day BETWEEN ? AND ? AND kind IN ('total','observed') GROUP BY kind", (start, end))}
        for k in ("total", "observed"):
            totals.setdefault(k, {"tx": 0, "rx": 0})
        sql = "SELECT label,SUM(tx) tx,SUM(rx) rx FROM daily WHERE day BETWEEN ? AND ? AND kind=?"
        args = [start, end, kind]
        if route != "all":
            sql += " AND route=?"
            args.append(route)
        aggregate = db.execute("SELECT COALESCE(SUM(tx),0),COALESCE(SUM(rx),0)" + sql[sql.index(" FROM "):], args).fetchone()
        matched_totals = {"tx": aggregate[0], "rx": aggregate[1]}
        sql += " GROUP BY label ORDER BY SUM(tx)+SUM(rx) DESC LIMIT 2000"
        rows = [dict(r) for r in db.execute(sql, args)]
        routes = [dict(r) for r in db.execute("SELECT route,SUM(tx) tx,SUM(rx) rx FROM daily WHERE day BETWEEN ? AND ? AND kind='route' GROUP BY route", (start, end))]
        health_row = db.execute("SELECT value FROM metadata WHERE name='health'").fetchone()
        health = json.loads(health_row[0]) if health_row else {}
        span = db.execute("SELECT MIN(day),MAX(day) FROM daily").fetchone()
        days = [dict(r) for r in db.execute("SELECT day,kind,SUM(tx) tx,SUM(rx) rx FROM daily WHERE day BETWEEN ? AND ? AND kind IN ('total','observed') GROUP BY day,kind ORDER BY day", (start, end))]
        return {"version": VERSION, "from": start, "to": end, "kind": kind, "route": route, "totals": totals, "matched_totals": matched_totals, "rows": rows, "routes": routes, "days": days, "health": health, "retained_from": span[0], "retained_to": span[1], "database_bytes": Path(path).stat().st_size, "retention_days": RETENTION, "limits": LIMITS}
    finally:
        db.close()


def handler_for(base):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.headers.get("Host", "").split(":")[0] not in ("127.0.0.1", "localhost"):
                self.send_error(403)
                return
            try:
                url = urlsplit(self.path)
                if url.path in ("/api/report", "/export.csv"):
                    data = report(base / "data/audit.sqlite3", parse_qs(url.query))
                    if url.path == "/export.csv":
                        out = io.StringIO()
                        writer = csv.writer(out)
                        writer.writerow(["from", "to", "kind", "route", "label", "upload_bytes", "download_bytes"])
                        for row in data["rows"]:
                            label = row["label"]
                            if label.startswith(("=", "+", "-", "@")):
                                label = "'" + label
                            writer.writerow([data["from"], data["to"], data["kind"], data["route"], label, row["tx"], row["rx"]])
                        content = ("\ufeff" + out.getvalue()).encode("utf-8")
                        mime = "text/csv; charset=utf-8"
                    else:
                        content = json.dumps(data, ensure_ascii=False).encode("utf-8")
                        mime = "application/json; charset=utf-8"
                elif url.path in ("/", "/app.js", "/style.css"):
                    name = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}[url.path]
                    content = (base / name).read_bytes()
                    mime = {"/": "text/html", "/app.js": "text/javascript", "/style.css": "text/css"}[url.path] + "; charset=utf-8"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'")
                if url.path == "/export.csv":
                    self.send_header("Content-Disposition", 'attachment; filename="clash-traffic.csv"')
                self.end_headers()
                self.wfile.write(content)
            except (ValueError, KeyError):
                self.send_error(400, "Invalid query")
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                logging.exception("Dashboard read failed")
                self.send_error(503, "Statistics temporarily unavailable")
    return Handler


def serve(base, port, socket_path=DEFAULT_SOCKET):
    os.umask(0o077)
    (base / "data").mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger()
    logger.setLevel(logging.WARNING)
    handler = logging.handlers.RotatingFileHandler(str(base / "data/collector.log"), maxBytes=1048576, backupCount=1)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    store = Store(base / "data/audit.sqlite3")
    accounting = Accounting(store)
    client = Mihomo(str(socket_path))
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler_for(base))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    last_info = -float("inf")
    first = True
    last_warning = -float("inf")
    try:
        while not stop.is_set():
            started = time.monotonic()
            try:
                identity = client.identity()
                snapshot = client.get("/connections")
                if identity != client.identity():
                    raise ValueError("Controller restarted during snapshot")
                accounting.ingest(snapshot, identity)
                if time.monotonic() - last_info > 60:
                    try:
                        cfg = client.get("/configs")
                        accounting.health["core_version"] = client.get("/version").get("version")
                        accounting.health["process_mode"] = cfg.get("find-process-mode")
                        accounting.health["mixed_port"] = cfg.get("mixed-port")
                    except Exception:
                        pass
                    last_info = time.monotonic()
            except Exception as exc:
                accounting.error(exc)
                if time.monotonic() - last_warning > 60:
                    logging.warning("Collection unavailable: %s", accounting.health["current_error"])
                    last_warning = time.monotonic()
            if first or time.time() - accounting.last_flush >= 60:
                accounting.flush()
                first = False
            stop.wait(max(0, 1 - (time.monotonic() - started)))
    finally:
        accounting.flush()
        server.shutdown()
        server.server_close()
        store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=BASE)
    parser.add_argument("--port", type=int, default=18797)
    parser.add_argument("--socket", type=Path, default=DEFAULT_SOCKET, help="Mihomo Unix controller socket")
    parser.add_argument("--version", action="version", version=VERSION)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    serve(args.base.expanduser().resolve(), args.port, args.socket.expanduser().resolve())
