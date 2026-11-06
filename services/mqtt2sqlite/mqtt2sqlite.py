#!/usr/bin/env python3
"""
mqtt2sqlite - store MQTT telemetry in a SQLite database.

Subscribes to telemetry/# and stores each JSON message as one row in a table
named after the message's "type" field:

    telemetry/shed/battery  {"hub":"shed00","sensor":"battery","type":"power",
                             "interval_s":300,"samples":60,
                             "bus_voltage":13.31,"bus_voltage_min":13.29, ...,
                             "epoch":1793556900}
      -> one row in table power_5m

* Every numeric field becomes a REAL column. New fields are added to the table
  automatically the first time they appear, so firmware changes need no
  database changes.
* Hourly summaries (<type>_1h) are updated as data arrives: averages weighted
  by interval_s, the lowest *_min and highest *_max, and energy (Wh) and
  charge (Ah) totals from the power and current averages.
* Rows in the 5-minute tables older than raw_retention_days are deleted once a
  day. Hourly summaries are kept forever.
* Inserts are batched (one transaction every flush_interval_s) to keep writes
  to the eMMC low.
* State history: it also subscribes to sensor/# (the retained alert-state
  messages) and records every change of a topic's "state" in state_events
  (door OPEN/CLOSED, device ONLINE/OFFLINE, LOW, COLD, ...), and every boot
  (an ONLINE message with reason "Started") in boots, with the firmware
  version and reset reason. Both are kept forever; they are small.

Runs under Python 3.10+ with paho-mqtt 2.x. See README.md for installation.
"""

import argparse
import configparser
import json
import logging
import math
import queue
import re
import signal
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple

LOG = logging.getLogger("mqtt2sqlite")

# Column and table names come from the messages, so only allow safe names.
FIELD_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
TYPE_RE = re.compile(r"^[a-z][a-z0-9_]{0,30}$")

# Fields with a fixed meaning: never stored as measurement columns.
RESERVED_FIELDS = {
    "hub", "sensor", "type", "epoch", "uptime_ms", "timestamp", "reason", "state",
    "ts", "received", "interval_s", "samples", "points",
}

# Fixed columns of the 5-minute (raw) and hourly tables.
RAW_FIXED = ("ts", "hub", "sensor", "received", "interval_s", "samples")
HOURLY_FIXED = ("ts", "hub", "sensor", "points", "samples", "interval_s")

EPOCH_MIN = 1_600_000_000   # Sept 2020; an earlier epoch means the device clock is not set
MAX_FUTURE_S = 300          # Allow a little device clock drift into the future
HOUR = 3600
MAX_INTERVAL_S = 86400      # Longest interval_s used to bound hourly queries
BATCH_MAX = 1000            # Flush early if this many messages are waiting

DEFAULTS = {
    "mqtt": {
        "host": "localhost",
        "port": "1883",
        "username": "",
        "password": "",
        "topic": "telemetry/#",
        # Alert-state messages for state_events and boots; empty = off
        "state_topic": "sensor/#",
        "client_id": "mqtt2sqlite",
        "tls": "false",
        "ca_file": "",
    },
    "database": {
        "path": "/var/lib/telemetry/telemetry.db",
        "flush_interval_s": "60",
        "raw_retention_days": "730",
        "default_interval_s": "300",
    },
    "log": {
        "level": "INFO",
    },
}


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

@dataclass
class Reading:
    """One telemetry message, ready to store."""
    mtype: str
    hub: str
    sensor: str
    ts: int                 # Unix time of the point (end of the interval)
    received: int           # Unix time the message arrived
    interval_s: Optional[int]
    samples: Optional[int]
    values: Dict[str, float]


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _int_or_none(value) -> Optional[int]:
    return int(value) if _is_number(value) and math.isfinite(value) else None


def parse_message(topic: str, payload: bytes, received: float) -> Optional[Reading]:
    """Turn an MQTT message into a Reading, or return None (and log why)."""
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        LOG.warning("%s: not valid JSON, ignored", topic)
        return None
    if not isinstance(data, dict):
        LOG.warning("%s: JSON is not an object, ignored", topic)
        return None

    mtype = data.get("type")
    if not isinstance(mtype, str) or not TYPE_RE.match(mtype):
        LOG.warning("%s: missing or invalid \"type\", ignored", topic)
        return None

    hub = data.get("hub")
    if not isinstance(hub, str) or not hub or len(hub) > 64:
        LOG.warning("%s: missing or invalid \"hub\", ignored", topic)
        return None

    # The sensor name defaults to the last part of the topic (telemetry/shed/battery -> battery)
    sensor = data.get("sensor") or topic.rsplit("/", 1)[-1]
    if not isinstance(sensor, str) or not sensor or len(sensor) > 64:
        LOG.warning("%s: missing or invalid \"sensor\", ignored", topic)
        return None

    # Use the device's time if it is plausible, otherwise the time of arrival
    # (the device sends uptime_ms instead of epoch until NTP is synced).
    received_s = int(received)
    epoch = data.get("epoch")
    if _is_number(epoch) and EPOCH_MIN <= epoch <= received_s + MAX_FUTURE_S:
        ts = int(epoch)
    else:
        ts = received_s

    values = {}
    for key, value in data.items():
        if key in RESERVED_FIELDS or not _is_number(value):
            continue
        if not FIELD_RE.match(key):
            LOG.debug("%s: field %r has an invalid name, ignored", topic, key)
            continue
        if not math.isfinite(value):
            continue
        values[key] = float(value)

    if not values:
        LOG.warning("%s: no numeric fields, ignored", topic)
        return None

    return Reading(
        mtype=mtype,
        hub=hub,
        sensor=sensor,
        ts=ts,
        received=received_s,
        interval_s=_int_or_none(data.get("interval_s")),
        samples=_int_or_none(data.get("samples")),
        values=values,
    )


def hour_of(ts: int, interval_s: Optional[int], default_interval_s: int) -> int:
    """Start of the hour a point belongs to, using the middle of its interval.

    A point stamped 10:00:03 covers 09:55-10:00, so it belongs to the 09:00 hour.
    """
    interval = interval_s if interval_s is not None else default_interval_s
    middle = ts - interval // 2
    return middle - middle % HOUR


def topic_matches(sub: str, topic: str) -> bool:
    """MQTT topic filter match with + and # wildcards."""
    if not sub:
        return False
    sp, tp = sub.split("/"), topic.split("/")
    for i, part in enumerate(sp):
        if part == "#":
            return True
        if i >= len(tp) or (part != "+" and part != tp[i]):
            return False
    return len(sp) == len(tp)


@dataclass
class StateMsg:
    """One sensor/... message: the topic's state at time ts."""
    topic: str
    hub: str
    sensor: str
    state: str
    reason: str
    ts: int
    received: int
    firmware: Optional[str] = None
    reset_reason: Optional[str] = None


def _text(value, limit: int = 128) -> Optional[str]:
    return value[:limit] if isinstance(value, str) and value else None


def parse_state_message(topic: str, payload: bytes, received: float) -> Optional[StateMsg]:
    """Turn a sensor/... message into a StateMsg, or return None if it has no state."""
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    state = _text(data.get("state"), 32)
    if state is None:
        return None
    received_s = int(received)
    epoch = data.get("epoch")
    ts = int(epoch) if _is_number(epoch) and EPOCH_MIN <= epoch <= received_s + MAX_FUTURE_S else received_s
    return StateMsg(
        topic=topic,
        hub=_text(data.get("hub"), 64) or topic.rsplit("/", 1)[0],
        sensor=_text(data.get("sensor"), 64) or topic.rsplit("/", 1)[-1],
        state=state.upper(),
        reason=_text(data.get("reason")) or "",
        ts=ts,
        received=received_s,
        firmware=_text(data.get("firmware"), 32),
        reset_reason=_text(data.get("reset_reason"), 32),
    )


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _q(name: str) -> str:
    """Quote an identifier. Names are already restricted by FIELD_RE / TYPE_RE."""
    return '"' + name.replace('"', '""') + '"'


class Store:
    """SQLite storage: <type>_5m tables for the points, <type>_1h for summaries."""

    def __init__(self, path: str, default_interval_s: int = 300):
        self.default_interval_s = default_interval_s
        # isolation_level=None: autocommit, transactions are started explicitly
        self.db = sqlite3.connect(path, isolation_level=None, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")      # readers (Grafana) don't block the writer
        self.db.execute("PRAGMA synchronous=NORMAL")    # safe with WAL, fewer flushes to eMMC
        self.db.execute("PRAGMA busy_timeout=30000")
        self._columns: Dict[str, Set[str]] = {}
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS state_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, hub TEXT, sensor TEXT,
                topic TEXT NOT NULL, state TEXT NOT NULL, previous TEXT, reason TEXT,
                received INTEGER);
            CREATE INDEX IF NOT EXISTS state_events_ts ON state_events (ts);
            CREATE INDEX IF NOT EXISTS state_events_topic_ts ON state_events (topic, ts);
            CREATE TABLE IF NOT EXISTS last_state (
                topic TEXT PRIMARY KEY, state TEXT NOT NULL, ts INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS boots (
                ts INTEGER NOT NULL, hub TEXT NOT NULL, firmware TEXT, reset_reason TEXT,
                received INTEGER, PRIMARY KEY (hub, ts)) WITHOUT ROWID;
        """)

    def close(self) -> None:
        self.db.close()

    # -- schema --

    def _table_columns(self, table: str) -> Set[str]:
        if table not in self._columns:
            rows = self.db.execute(f"PRAGMA table_info({_q(table)})").fetchall()
            self._columns[table] = {row[1] for row in rows}
        return self._columns[table]

    def _add_columns(self, table: str, names: Iterable[str]) -> None:
        existing = self._table_columns(table)
        for name in sorted(set(names) - existing):
            LOG.info("Adding column %s.%s", table, name)
            self.db.execute(f"ALTER TABLE {_q(table)} ADD COLUMN {_q(name)} REAL")
            existing.add(name)

    def _ensure_raw_table(self, mtype: str, names: Iterable[str]) -> str:
        table = f"{mtype}_5m"
        if not self._table_columns(table):
            LOG.info("Creating table %s", table)
            self.db.execute(
                f"CREATE TABLE IF NOT EXISTS {_q(table)} ("
                " ts INTEGER NOT NULL, hub TEXT NOT NULL, sensor TEXT NOT NULL,"
                " received INTEGER NOT NULL, interval_s INTEGER, samples INTEGER,"
                " PRIMARY KEY (hub, sensor, ts)) WITHOUT ROWID")
            self.db.execute(f"CREATE INDEX IF NOT EXISTS {_q(table + '_ts')} ON {_q(table)} (ts)")
            self._columns.pop(table, None)
        self._add_columns(table, names)
        return table

    def _ensure_hourly_table(self, mtype: str, names: Iterable[str]) -> str:
        table = f"{mtype}_1h"
        if not self._table_columns(table):
            LOG.info("Creating table %s", table)
            self.db.execute(
                f"CREATE TABLE IF NOT EXISTS {_q(table)} ("
                " ts INTEGER NOT NULL, hub TEXT NOT NULL, sensor TEXT NOT NULL,"
                " points INTEGER, samples INTEGER, interval_s INTEGER,"
                " PRIMARY KEY (hub, sensor, ts)) WITHOUT ROWID")
            self._columns.pop(table, None)
        self._add_columns(table, names)
        return table

    def raw_types(self) -> List[str]:
        rows = self.db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE '%\\_5m' ESCAPE '\\'"
        ).fetchall()
        return sorted(row[0][:-3] for row in rows)

    # -- inserting --

    def insert(self, readings: List[Reading]) -> Tuple[int, Set[Tuple[str, str, str, int]]]:
        """Store readings in one transaction.

        Returns (rows inserted, set of (type, hub, sensor, hour) that changed).
        Duplicates (same hub, sensor and ts) are ignored.
        """
        inserted = 0
        touched: Set[Tuple[str, str, str, int]] = set()
        if not readings:
            return inserted, touched

        self.db.execute("BEGIN")
        try:
            for r in readings:
                table = self._ensure_raw_table(r.mtype, r.values)
                names = list(RAW_FIXED) + sorted(r.values)
                values = [r.ts, r.hub, r.sensor, r.received, r.interval_s, r.samples]
                values += [r.values[name] for name in sorted(r.values)]
                cur = self.db.execute(
                    f"INSERT OR IGNORE INTO {_q(table)} ({', '.join(_q(n) for n in names)})"
                    f" VALUES ({', '.join('?' * len(names))})", values)
                if cur.rowcount > 0:
                    inserted += 1
                    touched.add((r.mtype, r.hub, r.sensor,
                                 hour_of(r.ts, r.interval_s, self.default_interval_s)))
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            self._columns.clear()   # an ALTER TABLE may have been rolled back
            raise
        return inserted, touched

    # -- hourly summaries --

    def _value_columns(self, raw_table: str) -> List[str]:
        return sorted(self._table_columns(raw_table) - set(RAW_FIXED))

    def _rollup_sql(self, mtype: str) -> str:
        """INSERT ... SELECT that (re)computes one hour for one hub/sensor."""
        raw = f"{mtype}_5m"
        cols = self._value_columns(raw)
        weight = f"COALESCE(interval_s, {int(self.default_interval_s)})"
        hour_expr = f"(((ts - {weight} / 2) / {HOUR}) * {HOUR})"

        out_names = list(HOURLY_FIXED)
        exprs = [hour_expr, "hub", "sensor", "COUNT(*)", "SUM(samples)", f"SUM({weight})"]
        for c in cols:
            qc = _q(c)
            if c.endswith("_min"):
                exprs.append(f"MIN({qc})")
            elif c.endswith("_max"):
                exprs.append(f"MAX({qc})")
            else:
                # Average weighted by the length of each interval
                exprs.append(f"SUM({qc} * {weight}) / SUM(CASE WHEN {qc} IS NOT NULL THEN {weight} END)")
            out_names.append(c)

        # Energy (Wh) and charge (Ah): average x seconds / 3600. "in" counts the
        # positive part (battery charging, solar producing), "out" the negative part.
        totals = []
        if "power" in cols:
            totals += [
                ("energy_wh", f"SUM(power * {weight}) / 3600.0"),
                ("energy_in_wh", f"SUM(MAX(power, 0) * {weight}) / 3600.0"),
                ("energy_out_wh", f"SUM(MAX(-power, 0) * {weight}) / 3600.0"),
            ]
        if "current" in cols:
            totals += [
                ("charge_ah", f"SUM(current * {weight}) / 3600.0"),
                ("charge_in_ah", f"SUM(MAX(current, 0) * {weight}) / 3600.0"),
                ("charge_out_ah", f"SUM(MAX(-current, 0) * {weight}) / 3600.0"),
            ]
        for name, expr in totals:
            out_names.append(name)
            exprs.append(expr)

        hourly = self._ensure_hourly_table(mtype, out_names[len(HOURLY_FIXED):])
        updates = ", ".join(f"{_q(n)} = excluded.{_q(n)}" for n in out_names[3:])
        select = ", ".join(f"{e} AS {_q(n)}" for e, n in zip(exprs, out_names))
        return (
            f"INSERT INTO {_q(hourly)} ({', '.join(_q(n) for n in out_names)})"
            f" SELECT {select} FROM {_q(raw)}"
            f" WHERE hub = :hub AND sensor = :sensor"
            f" AND ts >= :hour AND ts < :hour + {HOUR + MAX_INTERVAL_S}"
            f" AND {hour_expr} = :hour"
            f" GROUP BY hub, sensor"
            f" ON CONFLICT (hub, sensor, ts) DO UPDATE SET {updates}"
        )

    def rollup(self, touched: Iterable[Tuple[str, str, str, int]]) -> int:
        """Recompute the hourly summaries for the given (type, hub, sensor, hour)."""
        touched = sorted(touched)
        if not touched:
            return 0
        sql_cache: Dict[str, str] = {}
        self.db.execute("BEGIN")
        try:
            for mtype, hub, sensor, hour in touched:
                if mtype not in sql_cache:
                    sql_cache[mtype] = self._rollup_sql(mtype)
                self.db.execute(sql_cache[mtype], {"hub": hub, "sensor": sensor, "hour": hour})
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            self._columns.clear()
            raise
        return len(touched)

    def rebuild_hourly(self) -> int:
        """Recompute every hourly summary from the 5-minute tables."""
        touched = set()
        for mtype in self.raw_types():
            rows = self.db.execute(
                f"SELECT hub, sensor, ts, interval_s FROM {_q(mtype + '_5m')}").fetchall()
            for hub, sensor, ts, interval_s in rows:
                touched.add((mtype, hub, sensor, hour_of(ts, interval_s, self.default_interval_s)))
        return self.rollup(touched)

    # -- maintenance --

    def apply_retention(self, raw_retention_days: int, now: Optional[float] = None) -> int:
        """Delete 5-minute rows older than raw_retention_days. Hourly rows are kept."""
        if raw_retention_days <= 0:
            return 0
        cutoff = int(now if now is not None else time.time()) - raw_retention_days * 86400
        deleted = 0
        self.db.execute("BEGIN")
        try:
            for mtype in self.raw_types():
                cur = self.db.execute(f"DELETE FROM {_q(mtype + '_5m')} WHERE ts < ?", (cutoff,))
                deleted += max(cur.rowcount, 0)
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return deleted

    def insert_states(self, msgs: List[StateMsg]) -> Tuple[int, int]:
        """Record state changes and boots in one transaction.

        A row goes into state_events only when a topic's state differs from the
        last one recorded (kept in last_state, so a restart or a retained replay
        adds nothing). Returns (state changes, boots) inserted.
        """
        changes = boots = 0
        if not msgs:
            return changes, boots
        self.db.execute("BEGIN")
        try:
            for m in msgs:
                row = self.db.execute("SELECT state, ts FROM last_state WHERE topic = ?",
                                      (m.topic,)).fetchone()
                if row is None or row[0] != m.state:
                    self.db.execute(
                        "INSERT INTO state_events (ts, hub, sensor, topic, state, previous, reason, received)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (m.ts, m.hub, m.sensor, m.topic, m.state, row[0] if row else None,
                         m.reason, m.received))
                    self.db.execute("INSERT OR REPLACE INTO last_state VALUES (?, ?, ?)",
                                    (m.topic, m.state, m.ts))
                    changes += 1
                if m.state == "ONLINE" and m.reason == "Started":
                    cur = self.db.execute(
                        "INSERT OR IGNORE INTO boots (ts, hub, firmware, reset_reason, received)"
                        " VALUES (?, ?, ?, ?, ?)",
                        (m.ts, m.hub, m.firmware, m.reset_reason, m.received))
                    boots += max(cur.rowcount, 0)
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return changes, boots

    def optimize(self) -> None:
        self.db.execute("PRAGMA optimize")
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def store_batch(store: Store, readings: List[Reading]) -> int:
    """Insert readings and update the hourly summaries they affect."""
    inserted, touched = store.insert(readings)
    store.rollup(touched)
    return inserted


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

def load_config(path: Optional[str]) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read_dict(DEFAULTS)
    if path:
        read = cfg.read(path)
        if not read:
            raise SystemExit(f"Cannot read config file {path}")
    return cfg


class Ingester:
    """MQTT subscriber (paho network thread) feeding a queue; the main thread writes."""

    def __init__(self, cfg: configparser.ConfigParser):
        self.cfg = cfg
        self.messages: "queue.Queue[Tuple[str, bytes, float]]" = queue.Queue()
        self.stop_event = threading.Event()
        db = cfg["database"]
        self.store = Store(db["path"], db.getint("default_interval_s"))
        self.flush_interval = db.getint("flush_interval_s")
        self.retention_days = db.getint("raw_retention_days")
        self.state_topic = cfg["mqtt"]["state_topic"].strip()
        self.stats = {"messages": 0, "stored": 0, "ignored": 0, "state_changes": 0, "boots": 0}

    # -- MQTT callbacks (run in the paho thread) --

    def on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code.is_failure:
            LOG.error("MQTT connect failed: %s", reason_code)
            return
        topics = [self.cfg["mqtt"]["topic"]] + ([self.state_topic] if self.state_topic else [])
        LOG.info("MQTT connected, subscribing to %s", ", ".join(topics))
        for topic in topics:
            client.subscribe(topic, qos=1)

    def on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        if not self.stop_event.is_set():
            LOG.warning("MQTT disconnected (%s), reconnecting", reason_code)

    def on_message(self, client, userdata, msg):
        self.messages.put((msg.topic, msg.payload, time.time()))

    # -- main loop --

    def _mqtt_client(self):
        import paho.mqtt.client as mqtt   # imported here so the tests don't need paho

        m = self.cfg["mqtt"]
        client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                             client_id=m["client_id"], clean_session=True)
        if m["username"]:
            client.username_pw_set(m["username"], m["password"] or None)
        if m.getboolean("tls"):
            client.tls_set(ca_certs=m["ca_file"] or None)
        client.on_connect = self.on_connect
        client.on_disconnect = self.on_disconnect
        client.on_message = self.on_message
        client.reconnect_delay_set(min_delay=1, max_delay=60)
        client.connect_async(m["host"], m.getint("port"), keepalive=60)
        return client

    def _handle(self, topic: str, payload: bytes, received: float,
                readings: List[Reading], states: List[StateMsg]) -> None:
        """Route one message: state topics to states, everything else to readings."""
        if topic_matches(self.state_topic, topic):
            msg = parse_state_message(topic, payload, received)
            if msg is not None:
                states.append(msg)
            return
        reading = parse_message(topic, payload, received)
        if reading is None:
            self.stats["ignored"] += 1
        else:
            readings.append(reading)

    def _flush(self, readings: List[Reading], states: List[StateMsg]) -> None:
        if readings:
            stored = store_batch(self.store, readings)
            self.stats["stored"] += stored
            LOG.debug("Stored %d of %d readings", stored, len(readings))
            readings.clear()
        if states:
            changes, boots = self.store.insert_states(states)
            self.stats["state_changes"] += changes
            self.stats["boots"] += boots
            if changes or boots:
                LOG.debug("Recorded %d state changes, %d boots", changes, boots)
            states.clear()

    def _maintenance(self) -> None:
        deleted = self.store.apply_retention(self.retention_days)
        self.store.optimize()
        LOG.info("Maintenance: deleted %d rows older than %d days", deleted, self.retention_days)

    def run(self) -> None:
        client = self._mqtt_client()
        client.loop_start()
        readings: List[Reading] = []
        states: List[StateMsg] = []
        now = time.monotonic()
        next_flush = now + self.flush_interval
        next_stats = now + HOUR
        next_maintenance = now + 60          # first run shortly after start, then daily
        try:
            while not self.stop_event.is_set():
                try:
                    topic, payload, received = self.messages.get(timeout=1.0)
                    self.stats["messages"] += 1
                    self._handle(topic, payload, received, readings, states)
                except queue.Empty:
                    pass

                now = time.monotonic()
                if now >= next_flush or len(readings) + len(states) >= BATCH_MAX:
                    self._flush(readings, states)
                    next_flush = now + self.flush_interval
                if now >= next_stats:
                    LOG.info("Last hour: %(messages)d messages, %(stored)d stored, "
                             "%(ignored)d ignored, %(state_changes)d state changes, "
                             "%(boots)d boots", self.stats)
                    self.stats = dict.fromkeys(self.stats, 0)
                    next_stats = now + HOUR
                if now >= next_maintenance:
                    self._maintenance()
                    next_maintenance = now + 86400
        finally:
            client.disconnect()
            client.loop_stop()
            # Store anything still queued or pending
            while True:
                try:
                    topic, payload, received = self.messages.get_nowait()
                except queue.Empty:
                    break
                self._handle(topic, payload, received, readings, states)
            self._flush(readings, states)
            self.store.close()
            LOG.info("Stopped")

    def stop(self, *_args) -> None:
        self.stop_event.set()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Store MQTT telemetry in SQLite.")
    parser.add_argument("--config", "-c", help="INI config file (see mqtt2sqlite.ini.example)")
    parser.add_argument("--rebuild-hourly", action="store_true",
                        help="recompute all hourly summaries from the 5-minute data, then exit")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    logging.basicConfig(level=cfg["log"]["level"].upper(),
                        format="%(levelname)s %(message)s", stream=sys.stdout)

    if args.rebuild_hourly:
        db = cfg["database"]
        store = Store(db["path"], db.getint("default_interval_s"))
        count = store.rebuild_hourly()
        store.close()
        LOG.info("Rebuilt %d hourly summaries", count)
        return 0

    ingester = Ingester(cfg)
    signal.signal(signal.SIGTERM, ingester.stop)
    signal.signal(signal.SIGINT, ingester.stop)
    LOG.info("Starting: %s -> %s", cfg["mqtt"]["topic"], cfg["database"]["path"])
    ingester.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
