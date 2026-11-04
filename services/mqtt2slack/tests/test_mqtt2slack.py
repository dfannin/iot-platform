"""Tests for mqtt2slack (no MQTT broker, paho or Slack needed).

Run from the server/ directory:
    python3 -m unittest discover -s tests -v
"""

import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mqtt2slack as s  # noqa: E402

T0 = 1_793_556_900.0          # 2026-11-01 ~ 10:15 PST
MIN = 60
HOUR = 3600


def msg(**fields) -> bytes:
    return json.dumps(fields).encode()


def battery(state, t, volts=13.3, reason="Periodic update"):
    return msg(hub="shed00", sensor="Battery", state=state, bus_voltage=volts, current=-1.42,
               power=-18.0, low_battery=state == "LOW", reason=reason, epoch=int(t))


def env(state, t, temp=10.0, reason="Periodic update"):
    return msg(hub="shed00", sensor="Shed Environment", state=state, temperature_c=temp,
               humidity=80.0, reason=reason, epoch=int(t))


class FakeSlack:
    """Collects posted texts; can be told to fail."""

    def __init__(self):
        self.posts = []
        self.fail_with = None

    def __call__(self, text):
        if self.fail_with is not None:
            raise self.fail_with
        self.posts.append(text)


class AlerterTestBase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_path = os.path.join(self.tmp.name, "state.db")
        self.history_path = os.path.join(self.tmp.name, "telemetry.db")
        self.slack = FakeSlack()
        self.alerter = self.new_alerter()

    def tearDown(self):
        self.alerter.store.close()
        self.tmp.cleanup()

    def new_alerter(self):
        return s.Alerter(s.StateStore(self.state_path), s.History(self.history_path),
                         stale_minutes=15, reminders={"COLD": 6}, min_interval_s=1, max_age_hours=24)

    def restart(self):
        self.alerter.tick(self.now)          # the service saves state on stop
        self.alerter.store.close()
        self.alerter = self.new_alerter()

    def feed(self, topic, raw, t):
        self.now = t
        self.alerter.handle_message(topic, raw, t)

    def flush(self, t=None):
        """Send everything in the outbox (advancing time past the rate limit)."""
        t = self.now if t is None else t
        out = []
        for i in range(100):
            before = len(self.slack.posts)
            self.alerter.send_due(self.slack, t + i)
            if len(self.slack.posts) == before and not self.alerter.store.outbox():
                break
        out = self.slack.posts[:]
        self.slack.posts.clear()
        return out


class ChangeDetectionTests(AlerterTestBase):

    def test_new_normal_topic_is_silent(self):
        self.feed("sensor/shed/battery", battery("OK", T0), T0)
        self.assertEqual(self.flush(), [])

    def test_new_abnormal_topic_is_posted(self):
        self.feed("sensor/shed/env", env("COLD", T0, temp=1.2), T0)
        posts = self.flush()
        self.assertEqual(len(posts), 1)
        self.assertIn("*shed00 Shed Environment: COLD*", posts[0])
        self.assertNotIn("(was", posts[0])

    def test_change_posts_rich_message(self):
        self.feed("sensor/shed/battery", battery("OK", T0), T0)
        self.feed("sensor/shed/battery", battery("LOW", T0 + 60, volts=12.71, reason="Low battery"), T0 + 61)
        posts = self.flush()
        self.assertEqual(len(posts), 1)
        text = posts[0]
        self.assertTrue(text.startswith("🪫 *shed00 Battery: LOW* (was OK)"), text)
        self.assertIn("12.71 V · -1.42 A · -18.0 W", text)
        self.assertIn("Low battery · " + s.local_time(T0 + 60), text)
        self.assertIn("`sensor/shed/battery`", text)

    def test_same_state_not_posted_again(self):
        self.feed("sensor/shed/battery", battery("OK", T0), T0)
        self.feed("sensor/shed/battery", battery("LOW", T0 + 60), T0 + 60)
        self.feed("sensor/shed/battery", battery("LOW", T0 + 360), T0 + 360)
        self.assertEqual(len(self.flush()), 1)

    def test_restart_does_not_repost(self):
        self.feed("sensor/shed/battery", battery("OK", T0), T0)
        self.feed("sensor/shed/battery", battery("LOW", T0 + 60), T0 + 60)
        self.flush()
        self.restart()
        # The broker replays the retained message
        self.feed("sensor/shed/battery", battery("LOW", T0 + 60), T0 + 600)
        self.assertEqual(self.flush(), [])

    def test_change_while_down_is_posted_after_restart(self):
        self.feed("sensor/shed/battery", battery("LOW", T0), T0)
        self.flush()
        self.restart()
        self.feed("sensor/shed/battery", battery("OK", T0 + 3000, reason="Battery recovered"), T0 + 3600)
        posts = self.flush()
        self.assertEqual(len(posts), 1)
        self.assertIn("OK* (was LOW)", posts[0])
        self.assertIn(s.local_time(T0 + 3000), posts[0])   # time of the event, not of the restart

    def test_offline_last_will_message(self):
        self.feed("sensor/shed/status", msg(hub="shed00", sensor="Shed Monitor", state="ONLINE",
                                            reason="Connected", epoch=int(T0)), T0)
        self.feed("sensor/shed/status", msg(hub="shed00", sensor="Shed Monitor", state="OFFLINE",
                                            reason="Connection lost", timestamp="unknown"), T0 + 600)
        posts = self.flush()
        self.assertEqual(len(posts), 1)
        self.assertIn("🔴 *shed00 Shed Monitor: OFFLINE* (was ONLINE)", posts[0])
        self.assertIn("Connection lost · " + s.local_time(T0 + 600), posts[0])

    def test_door_messages(self):
        # esp32-door: OPEN is not a normal state, CLOSED is
        self.feed("sensor/gate/door", msg(hub="door01", sensor="Gate", state="CLOSED"), T0)
        self.feed("sensor/gate/door", msg(hub="door01", sensor="Gate", state="OPEN"), T0 + 5)
        self.feed("sensor/gate/door", msg(hub="door01", sensor="Gate", state="CLOSED"), T0 + 65)
        posts = self.flush()
        self.assertEqual([p.splitlines()[0] for p in posts],
                         ["🚪 *door01 Gate: OPEN* (was CLOSED)", "🔒 *door01 Gate: CLOSED* (was OPEN)"])

    def test_ignored_messages(self):
        for raw in (b"not json", b"[1]", msg(hub="h", sensor="x"), msg(hub="h", state="")):
            self.feed("sensor/x/y", raw, T0)
        self.assertEqual(self.flush(), [])

    def test_missing_hub_and_sensor_use_topic(self):
        self.feed("sensor/foo/bar", msg(state="ERROR"), T0)
        self.assertIn("*sensor/foo bar: ERROR*", self.flush()[0])

    def test_history_written(self):
        self.feed("sensor/shed/battery", battery("OK", T0), T0)
        self.feed("sensor/shed/battery", battery("LOW", T0 + 60, reason="Low battery"), T0 + 60)
        db = sqlite3.connect(self.history_path)
        rows = db.execute("SELECT ts, hub, sensor, topic, state, previous, reason, kind FROM alerts").fetchall()
        db.close()
        self.assertEqual(rows, [(int(T0 + 60), "shed00", "Battery", "sensor/shed/battery", "LOW", "OK",
                                 "Low battery", "change")])


class ReminderTests(AlerterTestBase):
    """The tests skip hours without reports, so the watchdog is turned off here."""

    def new_alerter(self):
        return s.Alerter(s.StateStore(self.state_path), s.History(self.history_path),
                         stale_minutes=10 ** 6, reminders={"COLD": 6}, min_interval_s=1)

    def test_cold_reminders(self):
        self.feed("sensor/shed/env", env("OK", T0), T0)
        self.feed("sensor/shed/env", env("COLD", T0 + 60, temp=1.5, reason="Low temperature"), T0 + 60)
        self.assertEqual(len(self.flush()), 1)

        for t in range(int(T0 + 120), int(T0 + 6 * HOUR), 300):   # periodic updates, still COLD
            self.feed("sensor/shed/env", env("COLD", t, temp=0.8), t)
            self.alerter.tick(t)
        self.assertEqual(self.flush(), [])

        self.alerter.tick(T0 + 60 + 6 * HOUR)
        posts = self.flush(T0 + 60 + 6 * HOUR)
        self.assertEqual(len(posts), 1)
        self.assertTrue(posts[0].startswith("⏰ 🥶 *shed00 Shed Environment: COLD* (reminder)"), posts[0])
        self.assertIn("For 6 h 0 min", posts[0])
        self.assertIn("0.8 °C", posts[0])                 # latest reading

        self.alerter.tick(T0 + 60 + 9 * HOUR)
        self.assertEqual(self.flush(T0 + 60 + 9 * HOUR), [])
        self.alerter.tick(T0 + 60 + 12 * HOUR)
        self.assertEqual(len(self.flush(T0 + 60 + 12 * HOUR)), 1)

    def test_no_reminder_after_recovery(self):
        self.feed("sensor/shed/env", env("COLD", T0, temp=1.0), T0)
        self.feed("sensor/shed/env", env("OK", T0 + HOUR, temp=5.0), T0 + HOUR)
        self.flush()
        self.alerter.tick(T0 + 10 * HOUR)
        self.assertEqual(self.flush(T0 + 10 * HOUR), [])

    def test_reminder_schedule_survives_restart(self):
        self.feed("sensor/shed/env", env("COLD", T0, temp=1.0), T0)
        self.flush()
        self.now = T0 + 5 * HOUR
        self.restart()
        self.feed("sensor/shed/env", env("COLD", T0 + 5 * HOUR), T0 + 5 * HOUR)
        self.alerter.tick(T0 + 5.5 * HOUR)
        self.assertEqual(self.flush(T0 + 5.5 * HOUR), [])
        self.alerter.tick(T0 + 6 * HOUR)
        self.assertEqual(len(self.flush(T0 + 6 * HOUR)), 1)

    def test_reminder_mentions_old_last_report(self):
        self.feed("sensor/shed/env", env("COLD", T0, temp=1.0), T0)
        self.flush()
        self.alerter.tick(T0 + 6 * HOUR)
        self.assertIn("last report 6 h 0 min ago", self.flush(T0 + 6 * HOUR)[0])


class WatchdogTests(AlerterTestBase):

    def test_stale_and_reporting(self):
        self.feed("sensor/shed/battery", battery("OK", T0), T0)
        self.alerter.tick(T0 + 14 * MIN)
        self.assertEqual(self.flush(T0 + 14 * MIN), [])
        self.alerter.tick(T0 + 16 * MIN)
        posts = self.flush(T0 + 16 * MIN)
        self.assertEqual(len(posts), 1)
        self.assertIn("⏰ *shed00 Watchdog: STALE*", posts[0])
        self.assertIn("No report for 16 min", posts[0])
        self.alerter.tick(T0 + 30 * MIN)
        self.assertEqual(self.flush(T0 + 30 * MIN), [])        # no repeat
        self.feed("sensor/shed/battery", battery("OK", T0 + 31 * MIN), T0 + 31 * MIN)
        posts = self.flush()
        self.assertEqual(len(posts), 1)
        self.assertIn("✅ *shed00 Watchdog: REPORTING* (was STALE)", posts[0])

    def test_offline_suppresses_stale(self):
        self.feed("sensor/shed/battery", battery("OK", T0), T0)
        self.feed("sensor/shed/status", msg(hub="shed00", sensor="Shed Monitor", state="OFFLINE",
                                            timestamp="unknown"), T0 + 2 * MIN)
        self.flush()
        self.alerter.tick(T0 + 60 * MIN)
        self.assertEqual(self.flush(T0 + 60 * MIN), [])

    def test_old_retained_message_after_restart_is_stale(self):
        # Device died while the service was down: replayed message is 1 h old
        self.feed("sensor/old/x", msg(hub="dead01", sensor="X", state="OK", epoch=int(T0)), T0 + HOUR)
        self.alerter.tick(T0 + HOUR + 60)
        self.assertIn("dead01 Watchdog: STALE", self.flush(T0 + HOUR + 60)[0])

    def test_stale_not_reposted_after_restart(self):
        self.feed("sensor/shed/battery", battery("OK", T0), T0)
        self.alerter.tick(T0 + 20 * MIN)
        self.flush(T0 + 20 * MIN)
        self.now = T0 + 25 * MIN
        self.restart()
        self.feed("sensor/shed/battery", battery("OK", T0), T0 + 25 * MIN)   # replayed, old
        self.alerter.tick(T0 + 26 * MIN)
        self.assertEqual(self.flush(T0 + 26 * MIN), [])

    def test_reset(self):
        self.feed("sensor/shed/battery", battery("OK", T0), T0)
        self.alerter.reset_watchdog()
        self.alerter.tick(T0 + HOUR)
        self.assertEqual(self.flush(T0 + HOUR), [])


class OutboxTests(AlerterTestBase):

    def queue_alerts(self, n, t=T0):
        for i in range(n):
            self.feed(f"sensor/x/{i}", msg(hub="h", sensor=f"S{i}", state="ERROR"), t)

    def test_rate_limit_one_per_interval(self):
        self.queue_alerts(3)
        self.assertEqual(self.alerter.send_due(self.slack, T0), 1)
        self.assertEqual(self.alerter.send_due(self.slack, T0 + 0.5), 0)
        self.assertEqual(self.alerter.send_due(self.slack, T0 + 1), 1)

    def test_order_kept_and_backoff_on_failure(self):
        self.queue_alerts(2)
        self.slack.fail_with = s.SlackError("connection failed")
        with self.assertLogs("mqtt2slack", level="WARNING"):
            self.alerter.send_due(self.slack, T0)
        self.assertEqual(self.alerter.blocked_until, T0 + 10)
        with self.assertLogs("mqtt2slack", level="WARNING"):
            self.alerter.send_due(self.slack, T0 + 10)
        self.assertEqual(self.alerter.blocked_until, T0 + 30)    # 20 s backoff
        self.slack.fail_with = None
        self.alerter.send_due(self.slack, T0 + 29)
        self.assertEqual(self.slack.posts, [])
        self.alerter.send_due(self.slack, T0 + 30)
        self.alerter.send_due(self.slack, T0 + 31)
        self.assertEqual([p.splitlines()[0] for p in self.slack.posts],
                         ["❌ *h S0: ERROR*", "❌ *h S1: ERROR*"])
        self.assertEqual(self.alerter.failures, 0)

    def test_retry_after_and_permanent_errors(self):
        self.queue_alerts(1)
        self.slack.fail_with = s.SlackError("rate limited", retry_after=42)
        with self.assertLogs("mqtt2slack", level="WARNING"):
            self.alerter.send_due(self.slack, T0)
        self.assertEqual(self.alerter.blocked_until, T0 + 42)
        self.slack.fail_with = s.SlackError("not_in_channel", permanent=True)
        with self.assertLogs("mqtt2slack", level="ERROR"):
            self.alerter.send_due(self.slack, T0 + 42)
        self.assertEqual(self.alerter.blocked_until, T0 + 42 + s.PERMANENT_RETRY_S)
        self.assertEqual(len(self.alerter.store.outbox()), 1)     # kept, not dropped

    def test_late_delivery_note(self):
        self.queue_alerts(1)
        self.alerter.send_due(self.slack, T0 + 3 * HOUR)
        self.assertIn("_(delivered late, " + s.local_time(T0 + 3 * HOUR) + ")_", self.slack.posts[0])

    def test_old_alerts_summarized(self):
        self.queue_alerts(25, t=T0)
        self.alerter.send_due(self.slack, T0 + 25 * HOUR)
        self.assertEqual(len(self.slack.posts), 1)
        text = self.slack.posts[0]
        self.assertTrue(text.startswith("📬 *25 alert(s) could not be sent"), text)
        self.assertIn("❌ *h S0: ERROR*", text)
        self.assertIn("… and 5 more", text)
        self.assertEqual(self.alerter.store.outbox(), [])

    def test_outbox_survives_restart(self):
        self.queue_alerts(1)
        self.restart()
        self.alerter.send_due(self.slack, T0 + 5)
        self.assertEqual(len(self.slack.posts), 1)

    def test_history_failure_does_not_block_slack(self):
        with self.assertLogs("mqtt2slack", level="WARNING"):
            bad = s.History(os.path.join(self.tmp.name, "missing-dir", "x.db"))
            self.alerter.history = bad
            self.queue_alerts(1)
        self.alerter.send_due(self.slack, T0)
        self.assertEqual(len(self.slack.posts), 1)


class SlackHandler(BaseHTTPRequestHandler):
    """Fake chat.postMessage: the response is chosen by the message text."""
    requests = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SlackHandler.requests.append((self.headers["Authorization"], body))
        text = body["text"]
        if text == "rate":
            self.send_response(429)
            self.send_header("Retry-After", "17")
            self.end_headers()
            return
        if text == "boom":
            self.send_response(503)
            self.end_headers()
            return
        data = {"ok": True} if text == "ok" else {"ok": False, "error": text}
        out = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):
        pass


class PostSlackTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), SlackHandler)
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/api/chat.postMessage"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def post(self, text):
        return s.post_slack(self.url, "xoxb-test", "C0CE0HGN709", text, timeout=5)

    def test_ok(self):
        SlackHandler.requests.clear()
        self.assertTrue(self.post("ok")["ok"])
        auth, body = SlackHandler.requests[0]
        self.assertEqual(auth, "Bearer xoxb-test")
        self.assertEqual((body["channel"], body["text"]), ("C0CE0HGN709", "ok"))

    def test_permanent_error(self):
        with self.assertRaises(s.SlackError) as ctx:
            self.post("not_in_channel")
        self.assertTrue(ctx.exception.permanent)

    def test_temporary_error(self):
        with self.assertRaises(s.SlackError) as ctx:
            self.post("internal_error")
        self.assertFalse(ctx.exception.permanent)

    def test_rate_limited(self):
        with self.assertRaises(s.SlackError) as ctx:
            self.post("rate")
        self.assertEqual(ctx.exception.retry_after, 17)

    def test_server_error(self):
        with self.assertRaises(s.SlackError) as ctx:
            self.post("boom")
        self.assertFalse(ctx.exception.permanent)
        self.assertIsNone(ctx.exception.retry_after)

    def test_connection_refused(self):
        with self.assertRaises(s.SlackError) as ctx:
            s.post_slack("http://127.0.0.1:1/x", "t", "c", "ok", timeout=2)
        self.assertIn("connection failed", str(ctx.exception))


class ConfigTests(unittest.TestCase):

    def test_reminder_keys_keep_case_and_zero_disables(self):
        with tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False) as f:
            f.write("[reminders]\nCOLD = 0\nHOT = 2\n[database]\nstate_path = :memory:\nhistory_path =\n")
        try:
            cfg = s.load_config(f.name)
            a = s.make_alerter(cfg)
            self.assertEqual(a.reminders, {"HOT": 2.0})
            self.assertIsNone(a.history)
            a.store.close()
        finally:
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
