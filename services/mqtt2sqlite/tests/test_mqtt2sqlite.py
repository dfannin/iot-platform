"""Tests for mqtt2sqlite (no MQTT broker or paho needed).

Run from the server/ directory:
    python3 -m unittest discover -s tests -v
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mqtt2sqlite as m  # noqa: E402

NOW = 1_793_556_900          # 2026-11-01 ~ 10:15 PST
HOUR = 3600
LATER = NOW + 2 * 86400      # arrival time after all test points (epochs in the future are rejected)


def payload(**fields) -> bytes:
    return json.dumps(fields).encode()


def power_msg(epoch, current, power, voltage=13.3, sensor="battery", interval_s=300, **extra):
    return payload(hub="shed00", sensor=sensor, type="power", interval_s=interval_s, samples=60,
                   bus_voltage=voltage, bus_voltage_min=voltage - 0.1, bus_voltage_max=voltage + 0.1,
                   current=current, current_min=current - 1, current_max=current + 1,
                   power=power, power_min=power - 10, power_max=power + 10, epoch=epoch, **extra)


class ParseTests(unittest.TestCase):

    def test_power_message(self):
        r = m.parse_message("telemetry/shed/battery", power_msg(NOW, -0.5, -6.6), NOW + 2)
        self.assertEqual((r.mtype, r.hub, r.sensor, r.ts, r.received), ("power", "shed00", "battery", NOW, NOW + 2))
        self.assertEqual((r.interval_s, r.samples), (300, 60))
        self.assertEqual(r.values["current"], -0.5)
        self.assertNotIn("epoch", r.values)
        self.assertNotIn("interval_s", r.values)

    def test_unsynced_clock_uses_received_time(self):
        msg = payload(hub="shed00", sensor="env", type="env", temperature_c=5.0, uptime_ms=12345)
        self.assertEqual(m.parse_message("telemetry/shed/env", msg, NOW).ts, NOW)

    def test_implausible_epoch_uses_received_time(self):
        for bad in (12345, NOW + 3600, "1793556900", True):
            msg = payload(hub="shed00", sensor="env", type="env", temperature_c=5.0, epoch=bad)
            self.assertEqual(m.parse_message("t/env", msg, NOW).ts, NOW, bad)

    def test_sensor_defaults_to_topic(self):
        msg = payload(hub="shed00", type="env", temperature_c=5.0, epoch=NOW)
        self.assertEqual(m.parse_message("telemetry/shed/env", msg, NOW).sensor, "env")

    def test_rejected_messages(self):
        cases = [
            b"not json",
            b"[1, 2]",
            payload(hub="shed00", sensor="x", temperature_c=1.0),               # no type
            payload(hub="shed00", sensor="x", type="Bad Type", t=1.0),          # bad type name
            payload(sensor="x", type="env", temperature_c=1.0),                  # no hub
            payload(hub="shed00", sensor="x", type="env", note="text only"),     # no numbers
        ]
        with self.assertLogs("mqtt2sqlite", level="WARNING"):
            for raw in cases:
                self.assertIsNone(m.parse_message("telemetry/x", raw, NOW), raw)

    def test_field_filtering(self):
        msg = payload(hub="h", sensor="s", type="env", epoch=NOW, ok=1.5, flag=True,
                      text="x", **{"Bad-Name": 2.0, "state": 3.0})
        self.assertEqual(m.parse_message("t/s", msg, NOW).values, {"ok": 1.5})

    def test_non_finite_values_dropped(self):
        raw = b'{"hub":"h","sensor":"s","type":"env","a":1.0,"b":NaN,"c":Infinity}'
        self.assertEqual(m.parse_message("t/s", raw, NOW).values, {"a": 1.0})

    def test_hour_of_uses_interval_middle(self):
        start = (NOW // HOUR) * HOUR
        # 10:00:03 covers 09:55-10:00 -> previous hour
        self.assertEqual(m.hour_of(start + 3, 300, 300), start - HOUR)
        self.assertEqual(m.hour_of(start + 200, 300, 300), start)
        self.assertEqual(m.hour_of(start + 200, None, 300), start)


def wifi_msg(epoch, rssi, channel=9, **extra):
    return payload(hub="backyard00", sensor="wifi", type="wifi", interval_s=300, samples=300,
                   rssi=rssi, rssi_min=rssi - 3, rssi_max=rssi + 2, channel=channel, roams=0, epoch=epoch, **extra)


class StoreTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "t.db")
        self.store = m.Store(self.path)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def add(self, topic, raw, received=None):
        r = m.parse_message(topic, raw, received if received is not None else LATER)
        return m.store_batch(self.store, [r])

    def query(self, sql, *args):
        return self.store.db.execute(sql, args).fetchall()

    def test_creates_table_and_inserts(self):
        self.assertEqual(self.add("telemetry/shed/battery", power_msg(NOW, -0.5, -6.6)), 1)
        rows = self.query("SELECT ts, hub, sensor, interval_s, samples, current, bus_voltage_max FROM power_5m")
        self.assertEqual(rows, [(NOW, "shed00", "battery", 300, 60, -0.5, 13.4)])

    def test_wal_mode(self):
        self.assertEqual(self.query("PRAGMA journal_mode")[0][0], "wal")

    def test_duplicate_ignored(self):
        self.add("t/battery", power_msg(NOW, -0.5, -6.6))
        self.assertEqual(self.add("t/battery", power_msg(NOW, 9.9, 99)), 0)
        self.assertEqual(self.query("SELECT current FROM power_5m"), [(-0.5,)])

    def test_wifi_views(self):
        # No WiFi data yet: no WiFi view (it is created with the table)
        views = {r[0] for r in self.query("SELECT name FROM sqlite_master WHERE type = 'view'")}
        self.assertNotIn("v_wifi_5m", views)
        self.add("telemetry/backyard/wifi", wifi_msg(NOW, -60))
        cols = [r[1] for r in self.query("PRAGMA table_info(v_wifi_5m)")]
        self.assertEqual(cols, list(m.VIEWS["v_wifi_5m"][1]))
        self.assertNotIn("ping_ms", cols)  # ping is not collected (only on the device's WiFi page)
        # tx_power_dbm not sent yet: NULL
        self.assertEqual(self.query("SELECT ts, hub, rssi, rssi_min, tx_power_dbm, channel FROM v_wifi_5m"),
                         [(NOW, "backyard00", -60.0, -63.0, None, 9.0)])
        # A message with the field adds the column and the view picks it up
        self.add("telemetry/backyard/wifi", wifi_msg(NOW + 300, -61, tx_power_dbm=19.5))
        self.assertEqual(self.query("SELECT tx_power_dbm FROM v_wifi_5m ORDER BY ts"), [(None,), (19.5,)])
        # The hourly view follows the hourly table
        self.store.rollup({("wifi", "backyard00", "wifi", (NOW // HOUR) * HOUR)})
        self.assertEqual(len(self.query("SELECT rssi, tx_power_dbm FROM v_wifi_1h")), 1)
        # A restart (new Store) keeps the views working
        other = m.Store(self.path)
        self.assertEqual(len(other.db.execute("SELECT * FROM v_wifi_5m").fetchall()), 2)
        other.close()

    def test_access_point_changes_only(self):
        ap1, ap2 = "80:69:1a:1d:e1:8c", "24:A4:3C:AA:CE:5A"
        for i, bssid in enumerate([ap1, ap1, ap1, ap2, ap2, ap1]):
            self.add("telemetry/backyard/wifi", wifi_msg(NOW + 300 * i, -60, bssid=bssid, channel=9 if bssid == ap1
                                                         else 11))
        self.assertEqual(self.query("SELECT ts, hub, bssid, channel, previous FROM v_ap_events ORDER BY ts"), [
            (NOW, "backyard00", "80:69:1A:1D:E1:8C", 9, None),  # stored in capitals
            (NOW + 900, "backyard00", ap2, 11, "80:69:1A:1D:E1:8C"),
            (NOW + 1500, "backyard00", "80:69:1A:1D:E1:8C", 9, ap2)])
        self.assertEqual(self.query("SELECT hub, bssid FROM v_ap_current"), [("backyard00", "80:69:1A:1D:E1:8C")])
        # The 5-minute rows do not store it
        self.assertNotIn("bssid", self.store._table_columns("wifi_5m"))
        # A replay, an older report and an invalid value add nothing
        self.add("telemetry/backyard/wifi", wifi_msg(NOW + 1500, -60, bssid=ap1))
        self.add("telemetry/backyard/wifi", wifi_msg(NOW + 100, -60, bssid=ap2))
        self.add("telemetry/backyard/wifi", wifi_msg(NOW + 1800, -60, bssid="not-a-mac"))
        self.assertEqual(len(self.query("SELECT * FROM ap_events")), 3)
        # Other message types never record an access point
        self.add("t/battery", power_msg(NOW + 2000, -0.5, -6.6, bssid=ap2))
        self.assertEqual(len(self.query("SELECT * FROM ap_events")), 3)
        # Each node has its own history, and a restart (new Store) keeps the last one
        other = m.Store(self.path)
        r = m.parse_message("telemetry/shed/wifi", payload(hub="shed00", sensor="wifi", type="wifi", rssi=-61,
                                                           bssid=ap2, epoch=NOW + 2100), LATER)
        r2 = m.parse_message("telemetry/backyard/wifi", wifi_msg(NOW + 2100, -60, bssid=ap1), LATER)
        self.assertEqual(other.record_access_points([r, r2]), 1)
        other.close()

    def test_new_field_adds_column(self):
        self.add("t/battery", power_msg(NOW, -0.5, -6.6))
        self.add("t/battery", power_msg(NOW + 300, -0.5, -6.6, shunt_mv=-0.75))
        self.assertEqual(self.query("SELECT shunt_mv FROM power_5m ORDER BY ts"), [(None,), (-0.75,)])
        # A new Store (service restart) sees the column too
        other = m.Store(self.path)
        self.assertIn("shunt_mv", other._table_columns("power_5m"))
        other.close()

    def test_types_get_separate_tables(self):
        self.add("t/battery", power_msg(NOW, 1, 13))
        self.add("t/env", payload(hub="shed00", sensor="env", type="env", interval_s=300,
                                  temperature_c=10.0, epoch=NOW))
        self.add("t/device", payload(hub="shed00", sensor="device", type="device", interval_s=300,
                                     rssi=-61, epoch=NOW))
        self.assertEqual(self.store.raw_types(), ["device", "env", "power"])
        tables = {r[0] for r in self.query("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"power_1h", "env_1h", "device_1h"} <= tables)

    def test_hourly_summary(self):
        hour = (NOW // HOUR) * HOUR
        # Twelve 5-minute points in one hour; the last one has a 600 s interval
        # (e.g. a missed publish), so it counts twice in the weighted average.
        currents = [-2.0] * 6 + [1.0] * 5 + [4.0]
        readings = []
        for i, current in enumerate(currents):
            interval = 600 if i == 11 else 300
            ts = hour + 300 * (i + 1)
            if i == 11:
                ts = hour + 300 * 11 + 300   # middle at hour+3300, still this hour
            readings.append(m.parse_message("t/battery", power_msg(
                ts, current, current * 13.0, interval_s=interval), LATER))
        # Interval middles must all fall in this hour
        self.assertEqual({m.hour_of(r.ts, r.interval_s, 300) for r in readings}, {hour})
        m.store_batch(self.store, readings)

        row = self.query("SELECT points, samples, interval_s, current, current_min, current_max,"
                         " charge_ah, charge_in_ah, charge_out_ah, energy_wh, energy_in_wh, energy_out_wh"
                         " FROM power_1h WHERE hub='shed00' AND sensor='battery' AND ts=?", hour)[0]
        points, samples, seconds, avg, cmin, cmax, ah, ah_in, ah_out, wh, wh_in, wh_out = row
        self.assertEqual((points, samples, seconds), (12, 720, 3900))
        expected_avg = (6 * -2.0 * 300 + 5 * 1.0 * 300 + 4.0 * 600) / 3900
        self.assertAlmostEqual(avg, expected_avg)
        self.assertEqual((cmin, cmax), (-3.0, 5.0))
        self.assertAlmostEqual(ah_out, 6 * 2.0 * 300 / 3600)
        self.assertAlmostEqual(ah_in, (5 * 1.0 * 300 + 4.0 * 600) / 3600)
        self.assertAlmostEqual(ah, ah_in - ah_out)
        self.assertAlmostEqual(wh, ah * 13.0)
        self.assertAlmostEqual(wh_in - wh_out, wh)

    def test_hourly_updates_as_points_arrive(self):
        hour = (NOW // HOUR) * HOUR
        self.add("t/env", payload(hub="h", sensor="env", type="env", interval_s=300,
                                  temperature_c=10.0, temperature_c_min=9.0, temperature_c_max=11.0,
                                  epoch=hour + 300))
        self.add("t/env", payload(hub="h", sensor="env", type="env", interval_s=300,
                                  temperature_c=20.0, temperature_c_min=19.0, temperature_c_max=22.0,
                                  epoch=hour + 600))
        rows = self.query("SELECT ts, points, temperature_c, temperature_c_min, temperature_c_max FROM env_1h")
        self.assertEqual(rows, [(hour, 2, 15.0, 9.0, 22.0)])

    def test_hourly_kept_separate_per_sensor_and_hour(self):
        hour = (NOW // HOUR) * HOUR
        self.add("t/solar", power_msg(hour + 600, 2.0, 26.0, sensor="solar"))
        self.add("t/battery", power_msg(hour + 600, -1.0, -13.0))
        self.add("t/battery", power_msg(hour + HOUR + 600, -3.0, -39.0))
        rows = self.query("SELECT sensor, ts, current FROM power_1h ORDER BY sensor, ts")
        self.assertEqual(rows, [("battery", hour, -1.0), ("battery", hour + HOUR, -3.0), ("solar", hour, 2.0)])

    def test_rebuild_hourly(self):
        hour = (NOW // HOUR) * HOUR
        self.add("t/battery", power_msg(hour + 600, -1.0, -13.0))
        self.store.db.execute("DELETE FROM power_1h")
        self.assertEqual(self.store.rebuild_hourly(), 1)
        self.assertEqual(self.query("SELECT current FROM power_1h"), [(-1.0,)])

    def test_retention_deletes_old_raw_rows_only(self):
        old = NOW - 800 * 86400
        self.add("t/battery", power_msg(old, -1.0, -13.0), received=old)
        self.add("t/battery", power_msg(NOW, -2.0, -26.0))
        deleted = self.store.apply_retention(730, now=NOW + 60)
        self.assertEqual(deleted, 1)
        self.assertEqual(self.query("SELECT ts FROM power_5m"), [(NOW,)])
        self.assertEqual(len(self.query("SELECT ts FROM power_1h")), 2)

    def test_reader_sees_data_while_writer_open(self):
        # Grafana reads the file with its own connection
        self.add("t/battery", power_msg(NOW, -0.5, -6.6))
        reader = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        self.assertEqual(reader.execute("SELECT COUNT(*) FROM power_5m").fetchone()[0], 1)
        reader.close()


class TopicMatchTests(unittest.TestCase):

    def test_topic_matches(self):
        self.assertTrue(m.topic_matches("sensor/#", "sensor/backyard/gate01"))
        self.assertTrue(m.topic_matches("sensor/+/status", "sensor/shed/status"))
        self.assertFalse(m.topic_matches("sensor/+/status", "sensor/shed/battery"))
        self.assertFalse(m.topic_matches("sensor/#", "telemetry/shed/battery"))
        self.assertFalse(m.topic_matches("", "sensor/x"))


def door(state, t, reason="State changed"):
    return payload(hub="backyard00", sensor="Backyard Gate", state=state, reason=reason, epoch=t)


def status(state, t=None, reason="Connected", **extra):
    fields = dict(hub="backyard00", sensor="Backyard Monitor", state=state, reason=reason, **extra)
    if t is None:
        fields["timestamp"] = "unknown"
    else:
        fields["epoch"] = t
    return payload(**fields)


class StateHistoryTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "t.db")
        self.store = m.Store(self.path)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def add(self, topic, raw, received):
        msg = m.parse_state_message(topic, raw, received)
        return self.store.insert_states([msg] if msg else [])

    def events(self):
        return self.store.db.execute(
            "SELECT ts, hub, sensor, topic, state, previous, reason FROM state_events ORDER BY id").fetchall()

    def test_only_changes_recorded(self):
        gate = "sensor/backyard/gate01"
        self.assertEqual(self.add(gate, door("CLOSED", NOW, "Initial state"), NOW), (1, 0))
        self.assertEqual(self.add(gate, door("CLOSED", NOW + 300, "Periodic update"), NOW + 300), (0, 0))
        self.assertEqual(self.add(gate, door("OPEN", NOW + 400), NOW + 400), (1, 0))
        self.assertEqual(self.add(gate, door("CLOSED", NOW + 460), NOW + 460), (1, 0))
        self.assertEqual(self.events(), [
            (NOW, "backyard00", "Backyard Gate", gate, "CLOSED", None, "Initial state"),
            (NOW + 400, "backyard00", "Backyard Gate", gate, "OPEN", "CLOSED", "State changed"),
            (NOW + 460, "backyard00", "Backyard Gate", gate, "CLOSED", "OPEN", "State changed"),
        ])

    def test_restart_and_retained_replay_add_nothing(self):
        gate = "sensor/backyard/gate01"
        self.add(gate, door("OPEN", NOW), NOW)
        self.store.close()
        self.store = m.Store(self.path)               # service restart
        self.assertEqual(self.add(gate, door("OPEN", NOW), NOW + 900), (0, 0))
        self.assertEqual(len(self.events()), 1)

    def test_offline_uses_received_time(self):
        st = "sensor/backyard/status"
        self.add(st, status("ONLINE", NOW), NOW)
        self.add(st, status("OFFLINE", reason="Connection lost"), NOW + 1000)
        self.assertEqual(self.events()[-1][0], NOW + 1000)
        self.assertEqual(self.events()[-1][4:6], ("OFFLINE", "ONLINE"))

    def test_quick_reconnect_keeps_time_order(self):
        # Broker publishes the old session's OFFLINE (server time) and the device's ONLINE
        # (device clock, a second behind) in the same second: ONLINE must stay last
        st = "sensor/garage/status"
        self.add(st, status("ONLINE", NOW, "Started"), NOW)
        self.add(st, status("OFFLINE", reason="Connection lost"), NOW + 600)
        self.add(st, status("ONLINE", NOW + 599, "Reconnected"), NOW + 600)
        ev = self.events()
        self.assertEqual([(e[0], e[4]) for e in ev], [(NOW, "ONLINE"), (NOW + 600, "OFFLINE"), (NOW + 601, "ONLINE")])
        last = self.store.db.execute("SELECT state, ts FROM last_state WHERE topic = ?", (st,)).fetchone()
        self.assertEqual(last, ("ONLINE", NOW + 601))

    def test_same_second_change_gets_next_second(self):
        st = "sensor/garage/status"
        self.add(st, status("OFFLINE", reason="Connection lost"), NOW)
        self.add(st, status("ONLINE", NOW, "Started"), NOW)
        self.assertEqual([e[0] for e in self.events()], [NOW, NOW + 1])

    def test_repair_state_order(self):
        st, gate = "sensor/garage/status", "sensor/backyard/gate01"
        ins = ("INSERT INTO state_events (ts, hub, sensor, topic, state, previous, reason, received)"
               " VALUES (?, 'h', 's', ?, ?, NULL, 'r', ?)")
        for ts, topic, state in [(NOW, st, "ONLINE"), (NOW + 10, gate, "OPEN"), (NOW + 600, st, "OFFLINE"),
                                 (NOW + 599, st, "ONLINE"), (NOW + 20, gate, "CLOSED"), (NOW + 20, gate, "OPEN")]:
            self.store.db.execute(ins, (ts, topic, state, ts))
        self.store.db.execute("INSERT OR REPLACE INTO last_state VALUES (?, ?, ?)", (st, "ONLINE", NOW + 599))
        self.store.db.commit()
        self.assertEqual(self.store.repair_state_order(), 2)
        self.assertEqual([e[0] for e in self.events()], [NOW, NOW + 10, NOW + 600, NOW + 601, NOW + 20, NOW + 21])
        self.assertEqual(self.store.db.execute("SELECT ts FROM last_state WHERE topic = ?", (st,)).fetchone()[0],
                         NOW + 601)
        self.assertEqual(self.store.repair_state_order(), 0)  # idempotent

    def test_boots_recorded_once(self):
        st = "sensor/backyard/status"
        started = status("ONLINE", NOW, "Started", firmware="4.1", reset_reason="BROWNOUT")
        self.assertEqual(self.add(st, started, NOW), (1, 1))
        self.assertEqual(self.add(st, started, NOW + 60), (0, 0))          # retained replay
        self.add(st, status("ONLINE", NOW + 500, "Reconnected", firmware="4.1", reset_reason="BROWNOUT"), NOW + 500)
        rows = self.store.db.execute("SELECT ts, hub, firmware, reset_reason FROM boots").fetchall()
        self.assertEqual(rows, [(NOW, "backyard00", "4.1", "BROWNOUT")])

    def test_boot_build_recorded(self):
        st = "sensor/backyard/status"
        self.add(st, status("ONLINE", NOW, "Started", firmware="261122-0552", build="244d03e",
                            reset_reason="SOFTWARE"), NOW)
        self.add(st, status("ONLINE", NOW + 900, "Started", firmware="4.9.0", reset_reason="POWERON"), NOW + 900)
        rows = self.store.db.execute("SELECT firmware, build FROM boots ORDER BY ts").fetchall()
        self.assertEqual(rows, [("261122-0552", "244d03e"), ("4.9.0", None)])  # older firmware: no build

    def test_old_boots_table_gets_build_column(self):
        # A database from before the build column
        self.store.close()
        path = os.path.join(self.tmp.name, "old.db")
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE boots (ts INTEGER NOT NULL, hub TEXT NOT NULL, firmware TEXT, reset_reason TEXT,"
                   " received INTEGER, PRIMARY KEY (hub, ts)) WITHOUT ROWID")
        db.execute("INSERT INTO boots VALUES (?, 'shed00', '1.16.0', 'POWERON', ?)", (NOW, NOW))
        db.commit()
        db.close()
        self.store = m.Store(path)
        self.add("sensor/shed/status", status("ONLINE", NOW + 60, "Started", firmware="261122-0556",
                                              build="4e9cb03", reset_reason="SOFTWARE"), NOW + 60)
        rows = self.store.db.execute("SELECT firmware, build FROM boots ORDER BY ts").fetchall()
        self.assertEqual(rows, [("1.16.0", None), ("261122-0556", "4e9cb03")])

    def test_reboot_without_offline_still_a_boot(self):
        # A quick reboot can go ONLINE -> ONLINE (no state change), but it is a boot
        st = "sensor/shed/status"
        self.add(st, status("ONLINE", NOW, "Started", firmware="1.9.1", reset_reason="POWERON"), NOW)
        self.assertEqual(self.add(st, status("ONLINE", NOW + 700, "Started", firmware="1.9.2",
                                             reset_reason="SOFTWARE"), NOW + 700), (0, 1))

    def test_messages_without_state_ignored(self):
        self.assertIsNone(m.parse_state_message("sensor/x", b"not json", NOW))
        self.assertIsNone(m.parse_state_message("sensor/x", payload(hub="h"), NOW))

    def test_ingester_routes_by_topic(self):
        ing = m.Ingester.__new__(m.Ingester)              # no MQTT, no config file
        ing.state_topic = "sensor/#"
        ing.stats = {"ignored": 0}
        readings, states = [], []
        ing._handle("sensor/backyard/gate01", door("OPEN", NOW), NOW, readings, states)
        ing._handle("telemetry/shed/battery", power_msg(NOW, 1.0, 13.0), NOW, readings, states)
        self.assertEqual((len(readings), len(states)), (1, 1))
        self.assertEqual(states[0].state, "OPEN")


class FirmwareMessageTests(unittest.TestCase):
    """Messages exactly as esp32-monitor v1.8 publishes them."""

    def test_firmware_messages(self):
        msgs = {
            "telemetry/shed/battery": '{"hub":"shed00","sensor":"battery","type":"power","interval_s":300,'
                                      '"samples":60,"bus_voltage":13.295,"bus_voltage_min":13.000,'
                                      '"bus_voltage_max":13.590,"current":-0.525,"current_min":-2.000,'
                                      '"current_max":0.950,"power":-7.00,"power_min":-26.00,'
                                      '"power_max":12.91,"epoch":1793556900}',
            "telemetry/shed/env": '{"hub":"shed00","sensor":"env","type":"env","interval_s":300,"samples":60,'
                                  '"temperature_c":12.95,"temperature_c_min":10.00,"temperature_c_max":15.90,'
                                  '"humidity":84.50,"humidity_min":80.00,"humidity_max":89.00,'
                                  '"epoch":1793556900}',
            "telemetry/shed/device": '{"hub":"shed00","sensor":"device","type":"device","interval_s":300,'
                                     '"samples":1,"rssi":-61,"uptime_s":300,"free_heap":201234,'
                                     '"min_free_heap":180112,"mqtt_connects":1,"uptime_ms":300000}',
        }
        for topic, raw in msgs.items():
            r = m.parse_message(topic, raw.encode(), NOW + 1)
            self.assertIsNotNone(r, topic)
        dev = m.parse_message("telemetry/shed/device", msgs["telemetry/shed/device"].encode(), NOW + 1)
        self.assertEqual(dev.ts, NOW + 1)   # no epoch (NTP not synced) -> received time
        self.assertEqual(set(dev.values), {"rssi", "uptime_s", "free_heap", "min_free_heap", "mqtt_connects"})


if __name__ == "__main__":
    unittest.main()
