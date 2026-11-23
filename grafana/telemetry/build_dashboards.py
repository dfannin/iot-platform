#!/usr/bin/env python3
"""
Build the Grafana dashboards (JSON) for the IoT folder.

    python3 grafana/telemetry/build_dashboards.py

writes into grafana/telemetry/dashboards/:

    iot-device-health.json   IoT Device Health   every node: status, last seen, RSSI, uptime, memory, boots,
                                                 WiFi quality (signal range, channel, roams, access points)
    doors.json               Door and Gate Status every door/gate node (OPEN/CLOSED states)
    shed-monitor.json        Shed Solar Monitor   the shed's power and environment

All queries use the SQLite data source (frser-sqlite-datasource, uid telemetry-sqlite)
and the tables written by mqtt2sqlite.py; new panels use its views (v_..., iot-platform
CLAUDE.md rule 5). Edit this script, not the JSON files, then
rebuild and install the JSON files (see services/README.md).
"""

import json
import os

DS = {"type": "frser-sqlite-datasource", "uid": "telemetry-sqlite"}
RANGE = "ts >= $__from / 1000 AND ts < $__to / 1000"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboards")

# Links shown at the top of every dashboard (all three are tagged "iot")
LINKS = [{"type": "dashboards", "tags": ["iot"], "asDropdown": True, "title": "IoT dashboards",
          "includeVars": False, "keepTime": True}]

# ESP-IDF reset reason codes (telemetry reset_reason) as text
RESET_REASONS = {0: "UNKNOWN", 1: "POWERON", 2: "EXTERNAL", 3: "SOFTWARE", 4: "PANIC",
                 5: "INT_WATCHDOG", 6: "TASK_WATCHDOG", 7: "WATCHDOG", 8: "DEEPSLEEP",
                 9: "BROWNOUT", 10: "SDIO"}
RESET_CASE = ("CASE {col} " + " ".join(f"WHEN {k} THEN '{v}'" for k, v in RESET_REASONS.items())
              + " END")


# ---------------------------------------------------------------------------
# Panel helpers
# ---------------------------------------------------------------------------

class Dashboard:
    def __init__(self):
        self.panels = []

    def add(self, panel, x, y, w, h):
        panel.update({"id": len(self.panels) + 1, "datasource": DS,
                      "gridPos": {"x": x, "y": y, "w": w, "h": h}})
        self.panels.append(panel)


def target(sql, time_cols=("time", "ts"), ref="A"):
    return {"datasource": DS, "refId": ref, "queryType": "table",
            "rawQueryText": sql, "queryText": sql, "timeColumns": list(time_cols)}


def steps(*pairs):
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for c, v in pairs]}


def color_map(colors):
    """Value mapping: text value -> color (e.g. {"OPEN": "orange"})."""
    return [{"type": "value", "options": {
        k: {"color": c, "index": i} for i, (k, c) in enumerate(colors.items())}}]


STATE_COLORS = {"ONLINE": "green", "OFFLINE": "red", "OPEN": "orange", "CLOSED": "green"}
RESET_COLORS = {"POWERON": "green", "SOFTWARE": "green", "EXTERNAL": "blue", "DEEPSLEEP": "blue",
                "BROWNOUT": "red", "PANIC": "red", "INT_WATCHDOG": "red", "TASK_WATCHDOG": "red",
                "WATCHDOG": "red", "UNKNOWN": "text"}

# One series per hub: split the long query result by its "hub" column
PARTITION_BY_HUB = [{"id": "partitionByValues",
                     "options": {"fields": ["hub"], "keepFields": False, "naming": {"asLabels": True}}}]


def series_defaults(unit, decimals=None, thr=None):
    # Colors by series name, so a node keeps its color in every panel; threshold
    # lines dashed, so they don't look like data
    d = {"unit": unit, "color": {"mode": "palette-classic-by-name"},
         "custom": {"lineWidth": 2, "fillOpacity": 0, "spanNulls": True, "showPoints": "never",
                    "axisBorderShow": False, "thresholdsStyle": {"mode": "dashed" if thr else "off"}}}
    if decimals is not None:
        d["decimals"] = decimals
    if thr:
        d["thresholds"] = thr
    return d


def legend(**extra):
    return {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
            "tooltip": {"mode": "multi", "sort": "none"}, **extra}


def lines(title, sql, unit, decimals=None, desc="", thr=None, by_hub=False, step=False,
          soft_min=None, soft_max=None):
    defaults = series_defaults(unit, decimals, thr)
    if soft_min is not None:
        defaults["custom"]["axisSoftMin"] = soft_min
    if soft_max is not None:
        defaults["custom"]["axisSoftMax"] = soft_max
    if step:
        defaults["custom"]["lineInterpolation"] = "stepAfter"
    panel = {"type": "timeseries", "title": title, "description": desc, "targets": [target(sql)],
             "fieldConfig": {"defaults": defaults, "overrides": []}, "options": legend()}
    if by_hub:
        panel["transformations"] = PARTITION_BY_HUB
        defaults["displayName"] = "${__field.labels.hub}"
    return panel


def table(title, sql, desc="", time_cols=("time", "ts"), overrides=(), sort=None, min_width=None):
    custom = {"align": "auto", "cellOptions": {"type": "auto"}}
    if min_width:
        custom["minWidth"] = min_width
    panel = {"type": "table", "title": title, "description": desc,
             "targets": [target(sql, time_cols)],
             "fieldConfig": {"defaults": {"custom": custom},
                             "overrides": list(overrides)},
             "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False}}}
    if sort:
        panel["options"]["sortBy"] = [{"displayName": sort, "desc": False}]
    return panel


def colored_text(name, colors, default="text"):
    """Override: show a text column in colors from a value mapping."""
    return {"matcher": {"id": "byName", "options": name},
            "properties": [{"id": "custom.cellOptions", "value": {"type": "color-text"}},
                           {"id": "mappings", "value": color_map(colors)},
                           {"id": "color", "value": {"mode": "fixed", "fixedColor": default}}]}


def unit_override(name, unit, **props):
    p = [{"id": "unit", "value": unit}]
    for k, v in props.items():
        p.append({"id": k, "value": v})
    return {"matcher": {"id": "byName", "options": name}, "properties": p}


def device_link(name):
    """Override: the device name opens the node's own web page (its name is its mDNS host name)."""
    return {"matcher": {"id": "byName", "options": name},
            "properties": [{"id": "links", "value": [{"title": "Open ${__value.text}'s web page",
                                                      "url": "http://${__value.text}.local/",
                                                      "targetBlank": True}]}]}


def timeline(title, sql, desc="", colors=STATE_COLORS, by_hub=True):
    """State timeline: one row per series, colored by state."""
    panel = {"type": "state-timeline", "title": title, "description": desc, "targets": [target(sql)],
             "fieldConfig": {"defaults": {"mappings": color_map(colors),
                                          "color": {"mode": "fixed", "fixedColor": "text"},
                                          "custom": {"fillOpacity": 80, "lineWidth": 0}},
                             "overrides": []},
             "options": {"showValue": "never", "mergeValues": True, "rowHeight": 0.8,
                         "alignValue": "left",
                         "legend": {"displayMode": "list", "placement": "bottom", "showLegend": False},
                         "tooltip": {"mode": "single", "sort": "none"}}}
    if by_hub:
        panel["transformations"] = PARTITION_BY_HUB
        panel["fieldConfig"]["defaults"]["displayName"] = "${__field.labels.hub}"
    return panel


def write(name, uid, title, panels, templating=(), annotations=(), time_from="now-24h",
          refresh="5m", description=""):
    dash = {
        "uid": uid, "title": title, "description": description, "tags": ["iot"],
        "timezone": "browser", "editable": True, "refresh": refresh, "schemaVersion": 39,
        "version": 1, "time": {"from": time_from, "to": "now"}, "links": LINKS,
        "templating": {"list": list(templating)}, "annotations": {"list": list(annotations)},
        "panels": panels,
    }
    path = os.path.join(OUT, name)
    with open(path, "w") as f:
        json.dump(dash, f, indent=2)
        f.write("\n")
    print(f"{path}: {len(panels)} panels")


def resolution_var():
    return {"type": "custom", "name": "res", "label": "Resolution", "query": "5m,1h",
            "description": "5m: 5-minute points (kept 2 years). 1h: hourly summaries (kept forever), "
                           "faster for long ranges.",
            "current": {"text": "5m", "value": "5m"},
            "options": [{"text": "5m", "value": "5m", "selected": True},
                        {"text": "1h", "value": "1h", "selected": False}], "hide": 0}


# Latest event per topic (the current state), optionally only events before a time
def latest_event_sql(where, before=None):
    extra = f" AND e2.ts < {before}" if before else ""
    return (f"e.id = (SELECT MAX(e2.id) FROM state_events e2 WHERE e2.topic = e.topic{extra}) "
            f"AND {where}")


# ---------------------------------------------------------------------------
# 1. IoT Device Health
# ---------------------------------------------------------------------------

def device_health():
    d = Dashboard()
    status_where = "e.topic LIKE '%/status'"

    devices_sql = f"""
WITH hubs AS (SELECT hub FROM device_5m UNION SELECT hub FROM state_events WHERE topic LIKE '%/status'),
last AS (SELECT hub, MAX(ts) AS ts FROM device_5m GROUP BY hub),
tel AS (SELECT x.* FROM device_5m x JOIN last ON x.hub = last.hub AND x.ts = last.ts),
st AS (SELECT e.hub, e.state FROM state_events e WHERE {latest_event_sql(status_where)}),
seen AS (SELECT hub, MAX(ts) AS ts FROM (SELECT hub, ts FROM device_5m UNION ALL
         SELECT hub, ts FROM state_events WHERE state != 'OFFLINE') GROUP BY hub),
boot AS (SELECT hub, firmware, build, reset_reason, MAX(ts) AS ts FROM boots GROUP BY hub),
ap AS (SELECT hub, bssid FROM v_ap_current)
SELECT h.hub AS device, COALESCE(st.state, '?') AS status,
       strftime('%s', 'now') - seen.ts AS "last seen",
       boot.firmware AS firmware, boot.build AS build,
       COALESCE({RESET_CASE.format(col='tel.reset_reason')}, boot.reset_reason) AS "last reset",
       boot.ts AS "last boot",
       tel.rssi AS rssi, ap.bssid AS "access point", tel.uptime_s AS uptime,
       tel.free_heap AS "free heap", tel.min_free_heap AS "lowest heap",
       tel.mqtt_connects - 1 AS "MQTT reconnects", tel.wifi_reconnects AS "WiFi reconnects"
FROM hubs h LEFT JOIN tel ON tel.hub = h.hub LEFT JOIN st ON st.hub = h.hub
LEFT JOIN seen ON seen.hub = h.hub LEFT JOIN boot ON boot.hub = h.hub LEFT JOIN ap ON ap.hub = h.hub
ORDER BY h.hub""".strip()
    d.add(table("Devices", devices_sql,
                "Every node that sends device telemetry or an online/offline status. Click a device to "
                "open its web page (http://<device>.local/). last seen is the age of its latest message. "
                "firmware and build: the version (YYMMDD-HHMM, the commit time in UTC) and commit hash "
                "running since the last boot. access point: the one the node is connected to now. last reset: why it last restarted (BROWNOUT = power "
                "supply, PANIC / WATCHDOG = firmware crash or hang). Reconnect counts are since the "
                "last boot.",
                time_cols=("time", "ts", "last boot"), min_width=70,
                overrides=[device_link("device"),
                           colored_text("status", {"ONLINE": "green", "OFFLINE": "red"}),
                           colored_text("last reset", RESET_COLORS),
                           unit_override("last seen", "s", **{
                               "custom.cellOptions": {"type": "color-text"},
                               "thresholds": steps(("green", None), ("orange", 900), ("red", 1800)),
                               "color": {"mode": "thresholds"}}),
                           unit_override("last boot", "dateTimeAsIso", **{"custom.width": 160}),
                           unit_override("rssi", "dBm", **{
                               "custom.cellOptions": {"type": "color-text"},
                               "thresholds": steps(("red", None), ("orange", -80), ("green", -70)),
                               "color": {"mode": "thresholds"}}),
                           unit_override("uptime", "dtdurations"),
                           unit_override("free heap", "bytes"),
                           unit_override("lowest heap", "bytes")]),
          0, 0, 24, 6)

    timeline_sql = f"""
SELECT ts AS time, hub, state FROM state_events
WHERE topic LIKE '%/status' AND {RANGE}
UNION ALL
SELECT $__from / 1000 AS time, e.hub, e.state FROM state_events e
WHERE {latest_event_sql(status_where, before='$__from / 1000')}
ORDER BY time""".strip()
    d.add(timeline("Online / offline", timeline_sql,
                   "From the retained status messages: ONLINE after each connect, OFFLINE from the "
                   "MQTT last will (about 90 s after a node stops responding)."), 0, 6, 16, 6)

    # Counted from the start of the range, or from the node's first report if it is newer,
    # allowing for the one report (5 minutes) that is not due yet
    received_sql = f"""
WITH first AS (SELECT hub, MIN(ts - COALESCE(interval_s, 300)) AS first_ts FROM device_5m GROUP BY hub)
SELECT d.hub, MIN(100.0, ROUND(100.0 * SUM(COALESCE(d.interval_s, 300))
       / MAX(300.0, $__to / 1000.0 - MAX($__from / 1000.0, f.first_ts) - 300), 1)) AS received
FROM device_5m d JOIN first f ON f.hub = d.hub
WHERE d.{RANGE.replace(' AND ts', ' AND d.ts')} GROUP BY d.hub ORDER BY d.hub""".strip()
    d.add({"type": "bargauge", "title": "Telemetry received", "targets": [target(received_sql, ())],
           "description": "Share of the time range covered by device telemetry (every 5 minutes), "
                          "counted from the node's first report if it is newer than the range. Below "
                          "100 % means missed reports: offline, reboots, WiFi or broker problems.",
           "fieldConfig": {"defaults": {"unit": "percent", "min": 0, "max": 100, "decimals": 1,
                                        "thresholds": steps(("red", None), ("orange", 95), ("green", 99)),
                                        "color": {"mode": "thresholds"}}, "overrides": []},
           "options": {"reduceOptions": {"values": True, "calcs": [], "fields": "/^received$/"},
                       "orientation": "horizontal", "displayMode": "basic", "showUnfilled": True,
                       "valueMode": "color", "namePlacement": "left"}},
          16, 6, 8, 6)

    dev = f"FROM device_${{res}} WHERE {RANGE} ORDER BY ts"
    d.add(lines("WiFi RSSI", f"SELECT ts AS time, hub, rssi {dev}", "dBm", 0,
                "Signal strength at each node. Below about -80 dBm connections start to drop.",
                thr=steps(("red", None), ("orange", -80), ("green", -70)), by_hub=True,
                soft_min=-85, soft_max=-45), 0, 12, 12, 8)
    d.add(lines("Uptime", f"SELECT ts AS time, hub, uptime_s {dev}", "dtdurations", 0,
                "Time since each node last booted. Drops to 0 are restarts (see Recent boots).",
                by_hub=True), 12, 12, 12, 8)
    d.add(lines("Free memory (heap)", f"SELECT ts AS time, hub, free_heap {dev}", "bytes", 0,
                "Free heap on each ESP32 (about 320 KB RAM in total). A steady fall over days would "
                "mean a memory leak.", by_hub=True), 0, 20, 12, 8)
    d.add(lines("Reconnects since boot",
                f"SELECT ts AS time, hub || ' WiFi' AS hub, wifi_reconnects {dev.replace('ORDER BY ts', '')}"
                f"UNION ALL SELECT ts AS time, hub || ' MQTT' AS hub, mqtt_connects - 1 "
                f"FROM device_${{res}} WHERE {RANGE} ORDER BY time",
                "short", 0, "WiFi drops and MQTT reconnects since each node's last boot. Rising counts "
                "mean an unstable connection.", by_hub=True, step=True), 12, 20, 12, 8)

    # WiFi quality, from the nodes' WiFi telemetry (view v_wifi_5m / v_wifi_1h)
    d.add({"type": "row", "title": "WiFi quality", "collapsed": False, "panels": []}, 0, 28, 24, 1)
    wifi = f"FROM v_wifi_${{res}} WHERE {RANGE}"
    d.add(lines("Signal range (average and lowest)",
                f"SELECT ts AS time, hub || ' avg' AS hub, rssi {wifi} UNION ALL "
                f"SELECT ts AS time, hub || ' lowest' AS hub, rssi_min {wifi} ORDER BY time", "dBm", 0,
                "RSSI averaged over each report (every second on the node) and the lowest second. A low "
                "'lowest' with a good average means short drops, e.g. from interference or movement.",
                thr=steps(("red", None), ("orange", -80), ("green", -70)), by_hub=True,
                soft_min=-90, soft_max=-40), 0, 29, 12, 8)
    d.add(lines("WiFi channel and roams",
                f"SELECT ts AS time, hub || ' channel' AS hub, channel {wifi} UNION ALL "
                f"SELECT ts AS time, hub || ' roams' AS hub, roams {wifi} ORDER BY time", "short", 0,
                "The channel of the access point each node is connected to, and how often it moved to a "
                "stronger access point since its last boot. A channel change is a move to another "
                "access point (or the access point changed channel).", by_hub=True, step=True),
          12, 29, 12, 8)

    # Access points: recorded only when a node changes access point (view v_ap_events)
    ap_timeline_sql = f"""
SELECT ts AS time, hub, bssid AS state FROM v_ap_events WHERE {RANGE}
UNION ALL
SELECT $__from / 1000 AS time, a.hub, a.bssid FROM v_ap_events a
WHERE a.ts = (SELECT MAX(b.ts) FROM v_ap_events b WHERE b.hub = a.hub AND b.ts < $__from / 1000)
ORDER BY time""".strip()
    d.add(timeline("Access points", ap_timeline_sql,
                   "The access point (BSSID) each node was connected to. A new segment is a roam or a "
                   "reconnect to another access point. Recorded only when it changes."), 0, 37, 24, 6)
    ap_changes_sql = (f"SELECT ts AS time, hub, previous AS \"from\", bssid AS \"to\", channel "
                      f"FROM v_ap_events WHERE {RANGE} ORDER BY ts DESC")
    d.add(table("Access point changes", ap_changes_sql,
                "Every change of access point in the time range (newest first). The first row of a node has "
                "no 'from': the first access point recorded for it.",
                overrides=[unit_override("time", "dateTimeAsIso", **{"custom.width": 170})]),
          0, 43, 24, 6)

    boots_sql = (f"SELECT ts AS time, hub, firmware, build, reset_reason FROM boots WHERE {RANGE} "
                 "ORDER BY ts DESC")
    d.add(table("Recent boots", boots_sql,
                "Every restart (the first connect after a boot), with the firmware version, build (commit "
                "hash; empty before date versions) and the reset reason. SOFTWARE is normal after an OTA "
                "update or a WiFi recovery restart.",
                overrides=[colored_text("reset_reason", RESET_COLORS),
                           unit_override("time", "dateTimeAsIso", **{"custom.width": 170})]),
          0, 49, 24, 7)

    write("iot-device-health.json", "iot-device-health", "IoT Device Health", d.panels,
          templating=[resolution_var()], time_from="now-24h",
          description="Health of every IoT node: online status, WiFi, uptime, memory, restarts, WiFi "
                      "quality and access points. Click a device to open its web page.")


# ---------------------------------------------------------------------------
# 2. Door and Gate Status
# ---------------------------------------------------------------------------

def doors():
    d = Dashboard()
    door_where = "e.state IN ('OPEN', 'CLOSED')"
    label = "e.hub || ' · ' || e.sensor"

    current_sql = f"""
SELECT {label} AS door, e.state AS state, e.ts AS since,
       strftime('%s', 'now') - e.ts AS for_s
FROM state_events e WHERE {latest_event_sql(door_where)} ORDER BY door""".strip()
    d.add(table("Doors and gates now", current_sql,
                "Every door or gate sensor (any node that reports OPEN / CLOSED): current state and how "
                "long it has been in it.",
                time_cols=("time", "ts", "since"),
                overrides=[colored_text("state", {"OPEN": "orange", "CLOSED": "green"}),
                           unit_override("since", "dateTimeAsIso"),
                           unit_override("for_s", "dtdurations", **{"displayName": "for"})]),
          0, 0, 24, 5)

    timeline_sql = f"""
SELECT ts AS time, hub || ' · ' || sensor AS hub, state FROM state_events
WHERE state IN ('OPEN', 'CLOSED') AND {RANGE}
UNION ALL
SELECT $__from / 1000 AS time, {label} AS hub, e.state FROM state_events e
WHERE {latest_event_sql(door_where, before='$__from / 1000')}
ORDER BY time""".strip()
    d.add(timeline("Open / closed", timeline_sql,
                   "Orange = open, green = closed, one row per door or gate."), 0, 5, 24, 7)

    opens_sql = f"""
SELECT date(ts, 'unixepoch', 'localtime') AS day, hub || ' · ' || sensor AS door, COUNT(*) AS opens
FROM state_events WHERE state = 'OPEN' AND {RANGE} GROUP BY day, door ORDER BY day""".strip()
    open_time_sql = f"""
WITH ev AS (
  SELECT ts, state, hub || ' · ' || sensor AS door,
         LEAD(ts) OVER (PARTITION BY topic ORDER BY ts, id) AS next_ts
  FROM state_events WHERE state IN ('OPEN', 'CLOSED'))
SELECT date(ts, 'unixepoch', 'localtime') AS day, door,
       ROUND(SUM(COALESCE(next_ts, strftime('%s', 'now')) - ts) / 60.0, 1) AS open_minutes
FROM ev WHERE state = 'OPEN' AND {RANGE} GROUP BY day, door ORDER BY day""".strip()
    matrix = lambda value: [{"id": "groupingToMatrix", "options": {
        "columnField": "door", "rowField": "day", "valueField": value, "emptyValue": "zero"}}]
    for title, sql, value, unit, desc, x in (
            ("Opens per day", opens_sql, "opens", "short", "Number of times each door or gate opened.", 0),
            ("Time open per day", open_time_sql, "open_minutes", "m",
             "Minutes each door or gate was open. An opening is counted on the day it started.", 12)):
        d.add({"type": "barchart", "title": title, "description": desc, "timeFrom": "30d",
               "targets": [target(sql, ())], "transformations": matrix(value),
               "fieldConfig": {"defaults": {"unit": unit, "decimals": 0, "color": {"mode": "palette-classic"},
                                            "custom": {"fillOpacity": 80, "lineWidth": 1}}, "overrides": []},
               "options": {"xField": "day\\door", "orientation": "vertical", "groupWidth": 0.7,
                           "barWidth": 0.9, "showValue": "auto", "stacking": "none",
                           "xTickLabelRotation": -45, **legend()}},
              x, 12, 12, 9)

    events_sql = (f"SELECT ts AS time, hub || ' · ' || sensor AS door, state, previous, reason "
                  f"FROM state_events WHERE state IN ('OPEN', 'CLOSED') AND {RANGE} ORDER BY ts DESC LIMIT 500")
    d.add(table("Recent events", events_sql, "Every open and close in the time range (newest first).",
                overrides=[colored_text("state", {"OPEN": "orange", "CLOSED": "green"}),
                           unit_override("time", "dateTimeAsIso", **{"custom.width": 170})]),
          0, 21, 24, 8)

    write("doors.json", "iot-doors", "Door and Gate Status", d.panels, time_from="now-7d", refresh="1m",
          description="Open / closed history of every door and gate sensor (esp32-door nodes).")


# ---------------------------------------------------------------------------
# 3. Shed Solar Monitor
# ---------------------------------------------------------------------------

def shed():
    d = Dashboard()

    def stat(title, sql, unit, decimals, thr):
        return {"type": "stat", "title": title, "targets": [target(sql)],
                "fieldConfig": {"defaults": {"unit": unit, "decimals": decimals, "thresholds": thr,
                                             "color": {"mode": "thresholds"}}, "overrides": []},
                "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                            "colorMode": "value", "graphMode": "none", "textMode": "value"}}

    def latest(table_, sensor, col):
        return (f"SELECT ts AS time, {col} AS value FROM {table_}_5m "
                f"WHERE hub = '$hub' AND sensor = '{sensor}' ORDER BY ts DESC LIMIT 1")

    def band(title, table_, sensor, col, unit, decimals=None, thr=None, desc=""):
        sql = (f'SELECT ts AS time, {col}_min AS "min", {col} AS "avg", {col}_max AS "max" '
               f"FROM {table_}_${{res}} WHERE hub = '$hub' AND sensor = '{sensor}' AND {RANGE} ORDER BY ts")
        blue = {"id": "color", "value": {"mode": "fixed", "fixedColor": "blue"}}
        over = [
            {"matcher": {"id": "byName", "options": "min"},
             "properties": [{"id": "custom.lineWidth", "value": 0}, blue]},
            {"matcher": {"id": "byName", "options": "max"},
             "properties": [{"id": "custom.lineWidth", "value": 0},
                            {"id": "custom.fillBelowTo", "value": "min"},
                            {"id": "custom.fillOpacity", "value": 20}, blue]},
            {"matcher": {"id": "byName", "options": "avg"}, "properties": [blue]},
        ]
        return {"type": "timeseries", "title": title, "description": desc, "targets": [target(sql)],
                "fieldConfig": {"defaults": series_defaults(unit, decimals, thr), "overrides": over},
                "options": legend()}

    # Current values
    d.add(stat("Battery", latest("power", "battery", "bus_voltage"), "volt", 2,
               steps(("red", None), ("green", 12.8), ("red", 14.6))), 0, 0, 5, 4)
    d.add(stat("Battery current", latest("power", "battery", "current"), "amp", 2,
               steps(("orange", None), ("green", 0))), 5, 0, 5, 4)
    d.add(stat("Solar current", latest("power", "solar", "current"), "amp", 2,
               steps(("green", None))), 10, 0, 5, 4)
    d.add(stat("Shed temperature", latest("env", "env", "temperature_c"), "celsius", 1,
               steps(("blue", None), ("green", 2), ("red", 45))), 15, 0, 5, 4)
    d.add(stat("Humidity", latest("env", "env", "humidity"), "humidity", 0,
               steps(("green", None), ("orange", 85))), 20, 0, 4, 4)

    # Power
    d.add(band("Battery voltage", "power", "battery", "bus_voltage", "volt", 2,
               steps(("red", None), ("green", 12.8), ("red", 14.6)),
               "Average with min-max band. Lines at the LOW (12.8 V) and HIGH (14.6 V) alarm levels."),
          0, 4, 12, 8)
    d.add(band("Battery current", "power", "battery", "current", "amp", 2, None,
               "+ charging, - discharging. Average with min-max band."), 12, 4, 12, 8)
    d.add(band("Solar current", "power", "solar", "current", "amp", 2, None,
               "Panel current (true reading). Average with min-max band."), 0, 12, 12, 8)
    d.add(lines("Power",
                "SELECT ts AS time, "
                "MAX(CASE WHEN sensor = 'solar' THEN power END) AS \"Solar (reads low)\", "
                "MAX(CASE WHEN sensor = 'battery' THEN power END) AS \"Battery\" "
                f"FROM power_${{res}} WHERE hub = '$hub' AND {RANGE} GROUP BY ts ORDER BY ts",
                "watt", 1,
                "Average power. Battery: + charging, - discharging. Solar uses battery voltage "
                "(common-positive controller), so it reads about 35 % low.",
                thr=steps(("transparent", None), ("text", 0))),
          12, 12, 12, 8)

    # Environment
    d.add(band("Shed temperature", "env", "env", "temperature_c", "celsius", 1,
               steps(("blue", None), ("green", 2), ("red", 45)),
               "Lines at the COLD (2 C) and HOT (45 C) alarm levels."), 0, 20, 12, 8)
    d.add(band("Humidity", "env", "env", "humidity", "humidity", 0,
               steps(("green", None), ("orange", 85)),
               "Line at the HUMID (85 %RH) alarm level."), 12, 20, 12, 8)

    # Daily energy
    d.add({"type": "barchart", "title": "Daily energy (last 30 days)",
           "description": "From the hourly summaries. Charged / discharged are the battery's positive and "
                          "negative energy. Solar uses battery voltage, so it reads low (see README).",
           "timeFrom": "30d",
           "targets": [target(
               "SELECT date(ts, 'unixepoch', 'localtime') AS day, "
               "ROUND(SUM(CASE WHEN sensor = 'battery' THEN energy_in_wh END), 1) AS \"Battery charged\", "
               "ROUND(SUM(CASE WHEN sensor = 'battery' THEN energy_out_wh END), 1) AS \"Battery discharged\", "
               "ROUND(SUM(CASE WHEN sensor = 'solar' THEN energy_in_wh END), 1) AS \"Solar (reads low)\" "
               f"FROM power_1h WHERE hub = '$hub' AND {RANGE} GROUP BY day ORDER BY day", ())],
           "fieldConfig": {"defaults": {"unit": "watth", "decimals": 0, "color": {"mode": "palette-classic"},
                                        "custom": {"fillOpacity": 80, "lineWidth": 1}}, "overrides": []},
           "options": {"xField": "day", "orientation": "vertical", "groupWidth": 0.7, "barWidth": 0.9,
                       "showValue": "auto", "stacking": "none", "xTickLabelRotation": -45, **legend()}},
          0, 28, 24, 9)

    # Alerts for this hub (written by mqtt2slack)
    d.add(table("Recent alerts",
                "SELECT ts AS time, sensor, state, previous, reason, kind FROM alerts "
                f"WHERE hub = '$hub' AND {RANGE} ORDER BY ts DESC LIMIT 200",
                "Every alert for this hub in the time range (newest first). kind: change, reminder, "
                "watchdog, or brief (an outage too short to post).",
                overrides=[colored_text("state", {"OK": "green", "ONLINE": "green", "REPORTING": "green"},
                                        default="orange"),
                           unit_override("time", "dateTimeAsIso", **{"custom.width": 170})]),
          0, 37, 24, 9)

    annotation = {"name": "Alerts", "enable": True, "iconColor": "red", "datasource": DS,
                  "target": target(
                      "SELECT ts AS time, sensor || ': ' || state || "
                      "COALESCE(' (' || NULLIF(reason, '') || ')', '') AS text, state AS tags "
                      f"FROM alerts WHERE hub = '$hub' AND {RANGE} AND kind NOT IN ('reminder', 'brief') "
                      "ORDER BY ts", ref="Anno")}
    hub_var = {"type": "textbox", "name": "hub", "label": "Hub", "query": "shed00",
               "current": {"text": "shed00", "value": "shed00"}, "hide": 0}
    write("shed-monitor.json", "shed-monitor", "Shed Solar Monitor", d.panels,
          templating=[hub_var, resolution_var()], annotations=[annotation],
          description="Shed solar power, battery and environment (esp32-monitor).")


if __name__ == "__main__":
    device_health()
    doors()
    shed()
