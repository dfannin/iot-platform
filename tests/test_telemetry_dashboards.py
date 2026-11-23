"""Run every query of the telemetry dashboards against a database written by mqtt2sqlite.

The dashboards are generated (grafana/telemetry/build_dashboards.py) and their SQL
only fails in Grafana, so this feeds sample messages through mqtt2sqlite into a
temporary database and runs each panel's query with the Grafana variables filled in.
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "services", "mqtt2sqlite"))

import mqtt2sqlite as m  # noqa: E402

DASHBOARDS = os.path.join(ROOT, "grafana", "telemetry", "dashboards")
NOW = 1_793_556_900
LATER = NOW + 2 * 86400


def msg(**fields) -> bytes:
    return json.dumps(fields).encode()


def queries(dashboard):
    """(panel title, SQL) for every panel query of a dashboard file."""
    with open(os.path.join(DASHBOARDS, dashboard)) as f:
        d = json.load(f)
    return [(p["title"], t["rawQueryText"]) for p in d["panels"] for t in p.get("targets", [])]


def fill(sql, res="5m"):
    return (
        sql.replace("$__from", str(NOW * 1000))
        .replace("$__to", str((NOW + 86400) * 1000))
        .replace("${res}", res)
        .replace("$hub", "shed00")
    )


class DashboardQueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        path = os.path.join(cls.tmp.name, "telemetry.db")
        store = m.Store(path)
        readings = []
        for i in range(12):
            ts = NOW + 300 * (i + 1)
            for hub, ping in (("backyard00", True), ("shed00", False), ("garage00", True)):
                readings.append(
                    m.parse_message(
                        f"telemetry/{hub}/device",
                        msg(
                            hub=hub,
                            sensor="device",
                            type="device",
                            interval_s=300,
                            samples=1,
                            rssi=-60,
                            uptime_s=600 + i,
                            free_heap=140000,
                            min_free_heap=120000,
                            mqtt_connects=1,
                            wifi_reconnects=0,
                            reset_reason=3,
                            epoch=ts,
                        ),
                        LATER,
                    )
                )
                wifi = dict(
                    hub=hub,
                    sensor="wifi",
                    type="wifi",
                    interval_s=300,
                    samples=300,
                    rssi=-60.5,
                    rssi_min=-66,
                    rssi_max=-55,
                    rssi_std=2.1,
                    channel=9,
                    tx_power_dbm=19.5,
                    roams=0,
                    bssid="11:22:33:44:55:66",
                    epoch=ts,
                )
                if ping:
                    wifi.update(ping_sent=300, ping_ms=4.2, ping_ms_max=30, ping_loss=0.01)
                readings.append(m.parse_message(f"telemetry/{hub}/wifi", msg(**wifi), LATER))
            readings.append(
                m.parse_message(
                    "telemetry/shed/battery",
                    msg(
                        hub="shed00",
                        sensor="battery",
                        type="power",
                        interval_s=300,
                        samples=60,
                        bus_voltage=13.2,
                        bus_voltage_min=13.1,
                        bus_voltage_max=13.3,
                        current=-0.5,
                        current_min=-1,
                        current_max=0,
                        power=-6.6,
                        power_min=-10,
                        power_max=0,
                        epoch=ts,
                    ),
                    LATER,
                )
            )
            readings.append(
                m.parse_message(
                    "telemetry/shed/env",
                    msg(
                        hub="shed00",
                        sensor="env",
                        type="env",
                        interval_s=300,
                        samples=60,
                        temperature_c=5.6,
                        temperature_c_min=5.0,
                        temperature_c_max=6.0,
                        humidity=81.0,
                        humidity_min=80,
                        humidity_max=82,
                        epoch=ts,
                    ),
                    LATER,
                )
            )
        m.store_batch(store, readings)
        states = [
            (
                "sensor/backyard/status",
                msg(
                    hub="backyard00",
                    sensor="status",
                    state="ONLINE",
                    reason="Started",
                    firmware="261122-0552",
                    build="244d03e",
                    reset_reason="SOFTWARE",
                    epoch=NOW + 10,
                ),
            ),
            (
                "sensor/backyard/gate01",
                msg(
                    hub="backyard00",
                    sensor="Backyard Gate",
                    state="OPEN",
                    reason="State changed",
                    epoch=NOW + 600,
                ),
            ),
            (
                "sensor/backyard/gate01",
                msg(
                    hub="backyard00",
                    sensor="Backyard Gate",
                    state="CLOSED",
                    reason="State changed",
                    epoch=NOW + 900,
                ),
            ),
        ]
        store.insert_states([m.parse_state_message(t, p, NOW + 1000) for t, p in states])
        store.close()
        cls.db = sqlite3.connect(path)
        # mqtt2slack writes its alert history into the same database (same schema as its DB setup)
        cls.db.execute("""CREATE TABLE IF NOT EXISTS alerts (
            ts INTEGER NOT NULL, hub TEXT, sensor TEXT, topic TEXT, state TEXT,
            previous TEXT, reason TEXT, kind TEXT, text TEXT)""")
        cls.db.execute(
            "INSERT INTO alerts VALUES (?, 'shed00', 'battery', 'sensor/shed/battery', 'LOW', "
            "'OK', 'Battery low', 'alert', 'Battery LOW')",
            (NOW + 1200,),
        )

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.tmp.cleanup()

    def run_all(self, dashboard):
        results = {}
        for res in ("5m", "1h"):
            for title, sql in queries(dashboard):
                with self.subTest(dashboard=dashboard, panel=title, res=res):
                    results[(title, res)] = self.db.execute(fill(sql, res)).fetchall()
        return results

    def test_every_query_runs(self):
        for dashboard in ("iot-device-health.json", "doors.json", "shed-monitor.json"):
            self.run_all(dashboard)

    def test_device_health(self):
        r = self.run_all("iot-device-health.json")
        devices = r[("Devices", "5m")]
        self.assertEqual([row[0] for row in devices], ["backyard00", "garage00", "shed00"])
        backyard = devices[0]
        self.assertIn("261122-0552", backyard)
        self.assertIn("244d03e", backyard)  # build
        for res in ("5m", "1h"):
            self.assertEqual(
                {row[1] for row in r[("Ping to the gateway", res)]},
                {"backyard00", "garage00"},
                "the shed sends no ping fields",
            )
            self.assertTrue(r[("Ping loss", res)])
            self.assertEqual(
                {row[1] for row in r[("Signal range (average and lowest)", res)]},
                {
                    f"{h} {s}"
                    for h in ("backyard00", "shed00", "garage00")
                    for s in ("avg", "lowest")
                },
            )
            self.assertTrue(r[("WiFi channel and roams", res)])
        self.assertEqual(
            r[("Recent boots", "5m")][0][1:4], ("backyard00", "261122-0552", "244d03e")
        )


if __name__ == "__main__":
    unittest.main()
