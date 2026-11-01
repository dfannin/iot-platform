#!/usr/bin/env python3
"""
mqtt2slack - Slack alerts for MQTT state changes (replaces the Node-RED flow).

Subscribes to sensor/# (the retained alert-state messages from esp32-monitor
and esp32-door) and posts to Slack when a topic's "state" changes:

    sensor/shed/battery  {"hub":"shed00","sensor":"Battery","state":"LOW",
                          "bus_voltage":12.71,"current":-1.42,...,
                          "reason":"Low battery","timestamp":"2026-11-01T22:15:03"}
      -> 🪫 *shed00 Battery: LOW* (was OK)
         12.71 V · -1.42 A · -18.0 W
         Low battery · 2026-11-01 22:15:03

Features:
* Change detection per topic, like Node-RED's RBE node, but the last state is
  saved to disk. After a restart the retained messages are compared with the
  saved states: unchanged topics are not posted again, and changes that
  happened while the service was down are posted.
* Device watchdog: STALE when a hub has sent nothing for stale_minutes, and
  REPORTING when it sends again. A hub whose last message is the broker's
  OFFLINE last will is not reported STALE (OFFLINE was already posted).
* Reminders: a state listed in [reminders] (e.g. COLD = 6) is posted again
  every N hours while it lasts.
* Outbox: alerts are saved to disk and sent in order, at most one per
  min_interval_s. If Slack or the internet is down they are retried with
  backoff; alerts older than max_age_hours are combined into one summary.
* History: every alert is also written to an "alerts" table in the telemetry
  database, for Grafana annotations. A history failure never blocks Slack.

Runs under Python 3.10+ with paho-mqtt 2.x. See README.md for installation.
"""

import argparse
import configparser
import json
import logging
import queue
import signal
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

LOG = logging.getLogger("mqtt2slack")

EPOCH_MIN = 1_600_000_000     # earlier epochs mean the device clock is not set
MAX_FUTURE_S = 300
LATE_NOTE_S = 120             # add "(delivered late)" when sent this long after queuing
REPORT_AGE_NOTE_S = 900       # reminders mention the last report when it is older than this
SUMMARY_LINES = 20

DEFAULTS = {
    "mqtt": {
        "host": "localhost",
        "port": "1883",
        "username": "",
        "password": "",
        "topic": "sensor/#",
        "client_id": "mqtt2slack",
        "tls": "false",
        "ca_file": "",
    },
    "slack": {
        "token": "",
        "channel": "",
        "api_url": "https://slack.com/api/chat.postMessage",
        "min_interval_s": "1",
        "max_age_hours": "24",
        "timeout_s": "10",
    },
    "alerts": {
        "normal_states": "OK, ONLINE, CLOSED, REPORTING",
        "stale_minutes": "15",
        "check_interval_s": "60",
    },
    "reminders": {
        "COLD": "6",
    },
    "database": {
        "state_path": "/var/lib/telemetry/mqtt2slack-state.db",
        "history_path": "/var/lib/telemetry/telemetry.db",
    },
    "log": {
        "level": "INFO",
    },
}

EMOJI = {
    "OK": "✅", "ONLINE": "🟢", "REPORTING": "✅", "CLOSED": "🔒",
    "OFFLINE": "🔴", "STALE": "⏰", "ERROR": "❌",
    "LOW": "🪫", "HIGH": "⚡", "OVERCURRENT": "⚡",
    "COLD": "🥶", "HOT": "🔥", "HUMID": "💧", "OPEN": "🚪",
}

# Slack errors that retrying will not fix until the configuration is changed
PERMANENT_SLACK_ERRORS = {
    "invalid_auth", "not_authed", "account_inactive", "token_revoked", "token_expired",
    "no_permission", "missing_scope", "channel_not_found", "not_in_channel",
    "is_archived", "msg_too_long", "no_text", "invalid_arguments",
}
PERMANENT_RETRY_S = 600


# ---------------------------------------------------------------------------
# Time and formatting
# ---------------------------------------------------------------------------

def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def device_time(payload: dict, received: float) -> Optional[float]:
    """When the device sent the message: epoch, else an ISO local timestamp, else None."""
    epoch = payload.get("epoch")
    if _is_number(epoch) and EPOCH_MIN <= epoch <= received + MAX_FUTURE_S:
        return float(epoch)
    stamp = payload.get("timestamp")
    if isinstance(stamp, str) and len(stamp) >= 19:
        try:
            t = datetime.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S").timestamp()
        except ValueError:
            return None
        if EPOCH_MIN <= t <= received + MAX_FUTURE_S:
            return t
    return None


def local_time(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")


def duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days} d {hours} h"
    if hours:
        return f"{hours} h {minutes} min"
    return f"{minutes} min"


def format_values(p: dict) -> str:
    """The readings in a message, e.g. '12.71 V · -1.42 A · -18.0 W'."""
    parts = []
    if _is_number(p.get("bus_voltage")):
        parts.append(f"{p['bus_voltage']:.2f} V")
    if _is_number(p.get("current")):
        parts.append(f"{p['current']:+.2f} A")
    if _is_number(p.get("power")):
        parts.append(f"{p['power']:+.1f} W")
    if _is_number(p.get("temperature_c")):
        c = p["temperature_c"]
        parts.append(f"{c:.1f} °C ({c * 9 / 5 + 32:.1f} °F)")
    if _is_number(p.get("humidity")):
        parts.append(f"{p['humidity']:.0f} %RH")
    return " · ".join(parts)


def format_alert(hub: str, sensor: str, state: str, topic: str, when: float,
                 previous: Optional[str] = None, reason: str = "", values: str = "",
                 prefix: str = "", suffix: str = "") -> str:
    """Slack text (mrkdwn) for one alert."""
    title = f"{prefix}{EMOJI.get(state, '⚠️')} *{hub} {sensor}: {state}*"
    if suffix:
        title += f" {suffix}"
    if previous:
        title += f" (was {previous})"
    lines = [title]
    if values:
        lines.append(values)
    lines.append(" · ".join(x for x in (reason, local_time(when)) if x))
    lines.append(f"`{topic}`")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------

class SlackError(Exception):
    def __init__(self, message: str, permanent: bool = False, retry_after: Optional[float] = None):
        super().__init__(message)
        self.permanent = permanent
        self.retry_after = retry_after


def post_slack(api_url: str, token: str, channel: str, text: str, timeout: float = 10) -> dict:
    """Post one message with chat.postMessage. Raises SlackError on failure."""
    body = json.dumps({"channel": channel, "text": text, "unfurl_links": False,
                       "unfurl_media": False}).encode("utf-8")
    request = urllib.request.Request(api_url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            retry_after = exc.headers.get("Retry-After", "30")
            raise SlackError("rate limited", retry_after=float(retry_after)) from exc
        raise SlackError(f"HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise SlackError(f"connection failed: {exc}") from exc

    if not data.get("ok"):
        error = str(data.get("error", "unknown_error"))
        raise SlackError(error, permanent=error in PERMANENT_SLACK_ERRORS)
    return data


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

@dataclass
class TopicState:
    topic: str
    hub: str
    sensor: str
    state: str
    since: float       # when the state started (device time if known)
    notified: float    # last Slack post for this topic (alert or reminder)


@dataclass
class HubState:
    hub: str
    last_seen: float
    stale: bool
    offline: bool


class StateStore:
    """Small SQLite file with the alert state and the Slack outbox."""

    def __init__(self, path: str):
        self.db = sqlite3.connect(path, isolation_level=None, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS topic_state (
                topic TEXT PRIMARY KEY, hub TEXT, sensor TEXT, state TEXT,
                since REAL, notified REAL);
            CREATE TABLE IF NOT EXISTS hub_state (
                hub TEXT PRIMARY KEY, last_seen REAL, stale INTEGER, offline INTEGER);
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL, text TEXT);
        """)

    def close(self) -> None:
        self.db.close()

    def topics(self) -> Dict[str, TopicState]:
        rows = self.db.execute("SELECT topic, hub, sensor, state, since, notified FROM topic_state")
        return {r[0]: TopicState(*r) for r in rows}

    def save_topic(self, t: TopicState) -> None:
        self.db.execute("INSERT OR REPLACE INTO topic_state VALUES (?, ?, ?, ?, ?, ?)",
                        (t.topic, t.hub, t.sensor, t.state, t.since, t.notified))

    def hubs(self) -> Dict[str, HubState]:
        rows = self.db.execute("SELECT hub, last_seen, stale, offline FROM hub_state")
        return {r[0]: HubState(r[0], r[1], bool(r[2]), bool(r[3])) for r in rows}

    def save_hub(self, h: HubState) -> None:
        self.db.execute("INSERT OR REPLACE INTO hub_state VALUES (?, ?, ?, ?)",
                        (h.hub, h.last_seen, int(h.stale), int(h.offline)))

    def delete_hubs(self) -> None:
        self.db.execute("DELETE FROM hub_state")

    def enqueue(self, created: float, text: str) -> None:
        self.db.execute("INSERT INTO outbox (created, text) VALUES (?, ?)", (created, text))

    def outbox(self) -> List[Tuple[int, float, str]]:
        return self.db.execute("SELECT id, created, text FROM outbox ORDER BY id").fetchall()

    def delete_outbox(self, ids: List[int]) -> None:
        self.db.executemany("DELETE FROM outbox WHERE id = ?", [(i,) for i in ids])


class History:
    """Alert history in the telemetry database (best effort: errors are only logged)."""

    def __init__(self, path: str):
        self.path = path
        self.db: Optional[sqlite3.Connection] = None
        self._connect()

    def _connect(self) -> bool:
        if self.db is not None:
            return True
        try:
            db = sqlite3.connect(self.path, isolation_level=None, timeout=10)
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("""CREATE TABLE IF NOT EXISTS alerts (
                ts INTEGER NOT NULL, hub TEXT, sensor TEXT, topic TEXT, state TEXT,
                previous TEXT, reason TEXT, kind TEXT, text TEXT)""")
            db.execute("CREATE INDEX IF NOT EXISTS alerts_ts ON alerts (ts)")
            self.db = db
            return True
        except sqlite3.Error as exc:
            LOG.warning("Alert history unavailable (%s): %s", self.path, exc)
            return False

    def record(self, ts: float, hub: str, sensor: str, topic: str, state: str,
               previous: Optional[str], reason: str, kind: str, text: str) -> None:
        if not self._connect():
            return
        try:
            self.db.execute("INSERT INTO alerts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (int(ts), hub, sensor, topic, state, previous, reason, kind, text))
        except sqlite3.Error as exc:
            LOG.warning("Could not write alert history: %s", exc)
            self.db = None   # reconnect next time

    def close(self) -> None:
        if self.db is not None:
            self.db.close()


# ---------------------------------------------------------------------------
# Alert logic
# ---------------------------------------------------------------------------

class Alerter:
    """Change detection, watchdog, reminders and the Slack outbox (no MQTT, no threads)."""

    def __init__(self, store: StateStore, history: Optional[History], *,
                 normal_states=("OK", "ONLINE", "CLOSED", "REPORTING"),
                 stale_minutes: float = 15, reminders: Optional[Dict[str, float]] = None,
                 min_interval_s: float = 1, max_age_hours: float = 24):
        self.store = store
        self.history = history
        self.normal_states = {s.strip().upper() for s in normal_states if s.strip()}
        self.stale_s = stale_minutes * 60
        self.reminders = {k.upper(): v for k, v in (reminders or {}).items() if v > 0}
        self.min_interval_s = min_interval_s
        self.max_age_s = max_age_hours * 3600
        self.topics = store.topics()
        self.hubs = store.hubs()
        self.latest: Dict[str, Tuple[float, dict]] = {}   # topic -> (received, payload)
        self.dirty_hubs = set()
        self.blocked_until = 0.0     # no Slack posts before this time (backoff, rate limit)
        self.failures = 0

    # -- input --

    def handle_message(self, topic: str, raw: bytes, received: float) -> None:
        try:
            p = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            LOG.debug("%s: not JSON, ignored", topic)
            return
        if not isinstance(p, dict):
            return

        hub = p.get("hub") if isinstance(p.get("hub"), str) and p.get("hub") else topic.rsplit("/", 1)[0]
        sensor = p.get("sensor") if isinstance(p.get("sensor"), str) and p.get("sensor") \
            else topic.rsplit("/", 1)[-1]
        state = p.get("state")
        sent = device_time(p, received)

        self._watchdog_message(hub, state, sent if sent is not None else received, received)

        if not isinstance(state, str) or not state:
            return
        state = state.upper()
        self.latest[topic] = (received, p)
        when = sent if sent is not None else received
        reason = p.get("reason") if isinstance(p.get("reason"), str) else ""
        previous = self.topics.get(topic)

        if previous is not None and previous.state == state:
            return                                   # no change (periodic update or replay)
        if previous is None and state in self.normal_states:
            LOG.info("New topic %s: %s (not posted)", topic, state)
            self.topics[topic] = TopicState(topic, hub, sensor, state, when, received)
            self.store.save_topic(self.topics[topic])
            return

        prev_state = previous.state if previous else None
        text = format_alert(hub, sensor, state, topic, when, previous=prev_state,
                            reason=reason, values=format_values(p))
        self._alert(received, hub, sensor, topic, state, prev_state, reason, "change", text)
        self.topics[topic] = TopicState(topic, hub, sensor, state, when, received)
        self.store.save_topic(self.topics[topic])

    def _watchdog_message(self, hub: str, state, sent: float, now: float) -> None:
        h = self.hubs.get(hub)
        if h is None:
            h = self.hubs[hub] = HubState(hub, 0.0, False, False)
        if state == "OFFLINE":
            if not h.offline:
                h.offline = True                     # broker's last will, not the device
                self.dirty_hubs.add(hub)
            return
        if sent > h.last_seen:
            h.last_seen = sent
            self.dirty_hubs.add(hub)
        if now - sent <= self.stale_s:               # fresh, not an old retained message
            if h.offline:
                h.offline = False
                self.dirty_hubs.add(hub)
            if h.stale:
                h.stale = False
                self.dirty_hubs.add(hub)
                topic = f"watchdog/{hub}"
                text = format_alert(hub, "Watchdog", "REPORTING", topic, now, previous="STALE",
                                    reason="Reporting again")
                self._alert(now, hub, "Watchdog", topic, "REPORTING", "STALE", "Reporting again",
                            "watchdog", text)

    # -- periodic checks --

    def tick(self, now: float) -> None:
        """Watchdog and reminders; call about once a minute."""
        for hub, h in self.hubs.items():
            if not h.stale and not h.offline and h.last_seen > 0 and now - h.last_seen > self.stale_s:
                h.stale = True
                self.dirty_hubs.add(hub)
                topic = f"watchdog/{hub}"
                reason = f"No report for {duration(now - h.last_seen)}"
                text = format_alert(hub, "Watchdog", "STALE", topic, now, reason=reason)
                self._alert(now, hub, "Watchdog", topic, "STALE", None, reason, "watchdog", text)

        for t in self.topics.values():
            hours = self.reminders.get(t.state)
            if hours and now - t.notified >= hours * 3600:
                received, p = self.latest.get(t.topic, (None, {}))
                reason = f"For {duration(now - t.since)}"
                if received is not None and now - received > REPORT_AGE_NOTE_S:
                    reason += f", last report {duration(now - received)} ago"
                text = format_alert(t.hub, t.sensor, t.state, t.topic, now, reason=reason,
                                    values=format_values(p), prefix="⏰ ", suffix="(reminder)")
                self._alert(now, t.hub, t.sensor, t.topic, t.state, None, reason, "reminder", text)
                t.notified = now
                self.store.save_topic(t)

        for hub in self.dirty_hubs:
            if hub in self.hubs:
                self.store.save_hub(self.hubs[hub])
        self.dirty_hubs.clear()

    def reset_watchdog(self) -> None:
        self.hubs.clear()
        self.dirty_hubs.clear()
        self.store.delete_hubs()

    # -- output --

    def _alert(self, now, hub, sensor, topic, state, previous, reason, kind, text) -> None:
        LOG.info("Alert: %s %s %s -> %s (%s)", kind, topic, previous or "-", state, reason)
        self.store.enqueue(now, text)
        if self.history is not None:
            self.history.record(now, hub, sensor, topic, state, previous, reason, kind, text)

    def send_due(self, send: Callable[[str], None], now: float) -> int:
        """Send queued messages in order. Returns the number sent."""
        if now < self.blocked_until:
            return 0
        items = self.store.outbox()
        if not items:
            return 0

        # Combine alerts that waited too long (long internet outage) into one summary
        old = [item for item in items if now - item[1] > self.max_age_s]
        if old:
            lines = [text.splitlines()[0] for _, _, text in old[:SUMMARY_LINES]]
            if len(old) > SUMMARY_LINES:
                lines.append(f"… and {len(old) - SUMMARY_LINES} more")
            summary = (f"📬 *{len(old)} alert(s) could not be sent while Slack was unreachable* "
                       f"(since {local_time(old[0][1])}):\n" + "\n".join(lines))
            self.store.delete_outbox([item[0] for item in old])
            self.store.enqueue(now, summary)
            items = self.store.outbox()

        sent = 0
        for item_id, created, text in items:
            if now < self.blocked_until:
                break
            if now - created > LATE_NOTE_S:
                text += f"\n_(delivered late, {local_time(now)})_"
            try:
                send(text)
            except SlackError as exc:
                self.failures += 1
                if exc.retry_after is not None:
                    delay = exc.retry_after
                elif exc.permanent:
                    delay = PERMANENT_RETRY_S
                    LOG.error("Slack refused the message: %s. Check the token and channel in the "
                              "config; retrying in %d s", exc, delay)
                else:
                    delay = min(600, 10 * 2 ** (self.failures - 1))
                if not exc.permanent:
                    LOG.warning("Slack post failed (%s); retrying in %d s", exc, delay)
                self.blocked_until = now + delay
                break
            self.store.delete_outbox([item_id])
            self.failures = 0
            sent += 1
            self.blocked_until = now + self.min_interval_s
        return sent


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

def load_config(path: Optional[str]) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.optionxform = str          # keep state names like COLD in [reminders] as written
    cfg.read_dict(DEFAULTS)
    if path and not cfg.read(path):
        raise SystemExit(f"Cannot read config file {path}")
    return cfg


def make_alerter(cfg: configparser.ConfigParser) -> Alerter:
    db = cfg["database"]
    history = History(db["history_path"]) if db["history_path"] else None
    a, s = cfg["alerts"], cfg["slack"]
    reminders = {state: float(hours) for state, hours in cfg["reminders"].items()}
    return Alerter(StateStore(db["state_path"]), history,
                   normal_states=a["normal_states"].split(","),
                   stale_minutes=a.getfloat("stale_minutes"),
                   reminders=reminders,
                   min_interval_s=s.getfloat("min_interval_s"),
                   max_age_hours=s.getfloat("max_age_hours"))


def slack_sender(cfg: configparser.ConfigParser) -> Callable[[str], None]:
    s = cfg["slack"]
    if not s["token"] or not s["channel"]:
        raise SystemExit("[slack] token and channel must be set in the config file")
    return lambda text: post_slack(s["api_url"], s["token"], s["channel"], text, s.getfloat("timeout_s"))


class Service:
    """MQTT subscriber (paho network thread) feeding a queue; the main thread does the rest."""

    def __init__(self, cfg: configparser.ConfigParser):
        self.cfg = cfg
        self.alerter = make_alerter(cfg)
        self.send = slack_sender(cfg)
        self.messages: "queue.Queue[Tuple[str, bytes, float]]" = queue.Queue()
        self.stop_event = threading.Event()

    def on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code.is_failure:
            LOG.error("MQTT connect failed: %s", reason_code)
            return
        topic = self.cfg["mqtt"]["topic"]
        LOG.info("MQTT connected, subscribing to %s", topic)
        client.subscribe(topic, qos=1)

    def on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        if not self.stop_event.is_set():
            LOG.warning("MQTT disconnected (%s), reconnecting", reason_code)

    def on_message(self, client, userdata, msg):
        self.messages.put((msg.topic, msg.payload, time.time()))

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

    def run(self) -> None:
        client = self._mqtt_client()
        client.loop_start()
        check_interval = self.cfg["alerts"].getfloat("check_interval_s")
        next_check = time.time() + check_interval   # retained messages arrive first
        try:
            while not self.stop_event.is_set():
                try:
                    topic, payload, received = self.messages.get(timeout=1.0)
                    self.alerter.handle_message(topic, payload, received)
                except queue.Empty:
                    pass
                now = time.time()
                if now >= next_check:
                    self.alerter.tick(now)
                    next_check = now + check_interval
                self.alerter.send_due(self.send, now)
        finally:
            client.disconnect()
            client.loop_stop()
            self.alerter.tick(time.time())    # saves the watchdog state
            LOG.info("Stopped (%d message(s) waiting in the outbox)", len(self.alerter.store.outbox()))

    def stop(self, *_args) -> None:
        self.stop_event.set()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Slack alerts for MQTT state changes.")
    parser.add_argument("--config", "-c", help="INI config file (see mqtt2slack.ini.example)")
    parser.add_argument("--test-slack", action="store_true",
                        help="post a test message to the configured channel, then exit")
    parser.add_argument("--reset-watchdog", action="store_true",
                        help="forget all hubs known to the watchdog, then exit")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    logging.basicConfig(level=cfg["log"]["level"].upper(),
                        format="%(levelname)s %(message)s", stream=sys.stdout)

    if args.test_slack:
        send = slack_sender(cfg)
        text = format_alert("mqtt2slack", "Test", "OK", "test", time.time(),
                            reason="Test message from mqtt2slack")
        try:
            send(text)
        except SlackError as exc:
            LOG.error("Slack test failed: %s", exc)
            return 1
        LOG.info("Slack test message sent to %s", cfg["slack"]["channel"])
        return 0

    if args.reset_watchdog:
        store = StateStore(cfg["database"]["state_path"])
        store.delete_hubs()
        store.close()
        LOG.info("Watchdog reset (restart the service if it is running)")
        return 0

    service = Service(cfg)
    signal.signal(signal.SIGTERM, service.stop)
    signal.signal(signal.SIGINT, service.stop)
    LOG.info("Starting: %s -> Slack %s", cfg["mqtt"]["topic"], cfg["slack"]["channel"])
    service.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
