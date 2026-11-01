# Telemetry Server: MQTT → SQLite → Grafana

This directory holds the time series side of the shed monitor. The ESP32 publishes the average, min and max of its 5-second samples every 5 minutes on `telemetry/shed/...`. `mqtt2sqlite.py` stores them in a SQLite database, and Grafana shows them as graphs.

```mermaid
flowchart LR
  E[esp32-monitor] -- "telemetry/shed/* every 5 min" --> B[(Mosquitto)]
  B -- "telemetry/#" --> I["mqtt2sqlite.py<br/>(systemd service)"]
  I -- "one write per minute at most" --> DB[("/var/lib/telemetry/<br/>telemetry.db")]
  DB --> G["Grafana<br/>+ SQLite plugin"]
  DB --> P["Python / sqlite3 / pandas"]
```

Slack alerts don't use this path. They stay on `sensor/#` → Node-RED → Slack (see the main [README](../README.md#system-architecture)).

## Why This Design

The server is a small fanless PC (Celeron N3350, 4 GB RAM, 32 GB eMMC) that also runs Mosquitto and Node-RED, so the stack is kept light:

| Part | Footprint | Notes |
|------|-----------|-------|
| `mqtt2sqlite.py` | ~25 MB RAM | Python standard library plus `paho-mqtt` |
| SQLite | No server process | One file. Inserts are batched once a minute to keep eMMC writes low. |
| Grafana | ~150–200 MB RAM | Optional; you can also query the database from Python |
| Disk | ~50 MB per year | 4 rows every 5 minutes, plus small hourly summaries |

## What Is Stored

Each message's `type` field picks the table, and each numeric field becomes a column:

| Table | Contents | Kept |
|-------|----------|------|
| `power_5m` | `solar` and `battery`: `bus_voltage`, `current`, `power`, each with `_min` / `_max` | 2 years (`raw_retention_days`) |
| `env_5m` | `env`: `temperature_c`, `humidity`, each with `_min` / `_max` | 2 years |
| `device_5m` | `device`: `rssi`, `uptime_s`, `free_heap`, `min_free_heap`, `mqtt_connects` | 2 years |
| `power_1h`, `env_1h`, `device_1h` | Hourly summaries: weighted averages, the lowest `_min` and highest `_max` | Forever |

Every table has `ts` (Unix seconds, UTC), `hub` and `sensor`, plus `interval_s` and `samples`. The primary key is `(hub, sensor, ts)`, so a repeated message is ignored. Each 5-minute row also has `received`, the time the message arrived.

The hourly power tables also have energy and charge totals, calculated as average × interval ÷ 3600:

| Column | Meaning |
|--------|---------|
| `energy_wh`, `charge_ah` | Net energy (Wh) and charge (Ah) in the hour |
| `energy_in_wh`, `charge_in_ah` | Positive part: battery charging, or solar producing |
| `energy_out_wh`, `charge_out_ah` | Negative part, as a positive number: battery discharging |

Notes:
- A 5-minute point is stamped at the **end** of its interval (`ts`). It is counted in the hour that contains the **middle** of the interval, so a point at 10:00:03 belongs to 09:00–10:00.
- In/out totals are calculated from 5-minute averages. If the current changes direction within an interval, only the net amount is counted.
- The solar `power` uses the battery voltage, because the Keeper 1220 is common-positive (see the main README), so solar energy reads about 35 % low. The battery values are accurate.
- **The database follows the firmware.** A new numeric field from the firmware becomes a new column automatically, and a new `type` becomes a new table.

## Files

| File | Install to |
|------|------------|
| `mqtt2sqlite.py` | `/opt/mqtt2sqlite/mqtt2sqlite.py` |
| `requirements.txt` | Used to create `/opt/mqtt2sqlite/venv` |
| `mqtt2sqlite.ini.example` | `/etc/mqtt2sqlite/mqtt2sqlite.ini` |
| `systemd/mqtt2sqlite.service` | `/etc/systemd/system/mqtt2sqlite.service` |
| `grafana/provisioning/datasources/telemetry.yaml` | `/etc/grafana/provisioning/datasources/` |
| `grafana/provisioning/dashboards/telemetry.yaml` | `/etc/grafana/provisioning/dashboards/` |
| `grafana/dashboards/shed-monitor.json` | `/etc/grafana/dashboards/telemetry/` |
| `grafana/systemd/telemetry.conf` | `/etc/systemd/system/grafana-server.service.d/` |
| `tests/test_mqtt2sqlite.py` | Not installed. Run the tests from the repository. |

## Installation (Ubuntu 22.04)

Run these on the server, from the root of a clone of this repository. Each step can be checked before you go on to the next one.

### 1. Packages

```bash
sudo apt install python3-venv sqlite3
```

### 2. MQTT User for the Ingester

Check how Mosquitto handles logins:

```bash
grep -rE "password_file|acl_file|allow_anonymous" /etc/mosquitto/
```

If a `password_file` is listed, add a `telemetry` user to it. This example assumes the file is `/etc/mosquitto/passwd`:

```bash
sudo mosquitto_passwd /etc/mosquitto/passwd telemetry
```

```bash
sudo systemctl reload mosquitto
```

If an `acl_file` is listed, add these lines to it, then reload Mosquitto again. The ESP32's user also needs `topic write telemetry/#`.

```
user telemetry
topic read telemetry/#
```

### 3. Service User and Database Directory

The directory is setgid (the `2` in `2775`), so every database file belongs to the `telemetry` group. Grafana joins that group in step 8.

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin telemetry
```

```bash
sudo install -d -o telemetry -g telemetry -m 2775 /var/lib/telemetry
```

### 4. Program and Python Environment

```bash
sudo install -d /opt/mqtt2sqlite
```

```bash
sudo install -m 644 server/mqtt2sqlite.py /opt/mqtt2sqlite/
```

```bash
sudo python3 -m venv /opt/mqtt2sqlite/venv
```

```bash
sudo /opt/mqtt2sqlite/venv/bin/pip install -r server/requirements.txt
```

Optionally, run the tests. They need no broker and no extra packages:

```bash
cd server && python3 -m unittest discover -s tests -v; cd ..
```

### 5. Configuration

```bash
sudo install -d /etc/mqtt2sqlite
```

```bash
sudo install -m 640 -g telemetry server/mqtt2sqlite.ini.example /etc/mqtt2sqlite/mqtt2sqlite.ini
```

```bash
sudo nano /etc/mqtt2sqlite/mqtt2sqlite.ini
```

Set `username` and `password` to the MQTT user from step 2. If Mosquitto allows anonymous logins, leave both empty.

### 6. Start the Service

```bash
sudo install -m 644 server/systemd/mqtt2sqlite.service /etc/systemd/system/
```

```bash
sudo systemctl daemon-reload
```

```bash
sudo systemctl enable --now mqtt2sqlite
```

```bash
journalctl -u mqtt2sqlite -f
```

You should see `MQTT connected, subscribing to telemetry/#`. Press Ctrl+C to stop following the log.

### 7. Test Without the ESP32 (Optional)

Publish a test message. Add `-u` and `-P` if your broker needs a login.

```bash
mosquitto_pub -h localhost -t telemetry/test/env -m '{"hub":"test","sensor":"env","type":"env","interval_s":300,"temperature_c":20.5}'
```

Within a minute, the log shows `Creating table env_5m`. Check the row:

```bash
sqlite3 /var/lib/telemetry/telemetry.db "SELECT * FROM env_5m"
```

Delete the test rows:

```bash
sudo -u telemetry sqlite3 /var/lib/telemetry/telemetry.db "DELETE FROM env_5m WHERE hub='test'; DELETE FROM env_1h WHERE hub='test'"
```

### 8. Grafana

Add Grafana's apt repository and install Grafana. These are Grafana's documented steps for Debian and Ubuntu:

```bash
sudo apt install -y apt-transport-https software-properties-common wget
```

```bash
sudo mkdir -p /etc/apt/keyrings
```

```bash
wget -q -O - https://apt.grafana.com/gpg.key | gpg --dearmor | sudo tee /etc/apt/keyrings/grafana.gpg > /dev/null
```

```bash
echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" | sudo tee /etc/apt/sources.list.d/grafana.list
```

```bash
sudo apt update && sudo apt install grafana
```

Install the SQLite plugin:

```bash
sudo grafana-cli plugins install frser-sqlite-datasource
```

Let Grafana read the database. This is a systemd drop-in that adds the `telemetry` group and write access to the database directory, which SQLite needs for its `-shm` file:

```bash
sudo install -D -m 644 server/grafana/systemd/telemetry.conf /etc/systemd/system/grafana-server.service.d/telemetry.conf
```

Install the data source and the dashboard:

```bash
sudo install -m 644 server/grafana/provisioning/datasources/telemetry.yaml /etc/grafana/provisioning/datasources/
```

```bash
sudo install -m 644 server/grafana/provisioning/dashboards/telemetry.yaml /etc/grafana/provisioning/dashboards/
```

```bash
sudo install -D -m 644 server/grafana/dashboards/shed-monitor.json /etc/grafana/dashboards/telemetry/shed-monitor.json
```

Start Grafana:

```bash
sudo systemctl daemon-reload
```

```bash
sudo systemctl enable --now grafana-server
```

Open `http://rizz:3000/` (or the server's IP address) and log in as `admin` / `admin`. Grafana asks for a new password. The dashboard is under **Dashboards → Shed → Shed Monitor**.

**Optional: reduce Grafana's background work.** In `/etc/grafana/grafana.ini`, set the following, then restart Grafana:

```ini
[analytics]
reporting_enabled = false
check_for_updates = false
check_for_plugin_updates = false

[news]
news_feed_enabled = false
```

### 9. Firmware

Flash esp32-monitor v1.8. The first telemetry arrives 5 minutes after it boots.

## The Dashboard

| Section | Panels |
|---------|--------|
| Top row | Latest battery voltage and current, solar current, temperature, humidity and RSSI, colored by the alarm thresholds |
| Power | Battery voltage, battery current and solar current (average line with a min–max band), and power |
| Environment | Temperature and humidity, with lines at the alarm thresholds |
| Daily energy | Battery charged and discharged and solar Wh per day, for the last 30 days |
| Device | RSSI, free memory and uptime (drops show reboots) |

Two variables at the top:
- **Resolution:** `5m` (5-minute points) or `1h` (hourly summaries). Use `1h` for ranges longer than about a month: it is faster, and 5-minute data is only kept for 2 years.
- **Hub:** `shed00`.

The dashboard is provisioned from the JSON file but can still be edited in Grafana. To keep your changes, export the JSON (**Share → Export**) and save it over `grafana/dashboards/shed-monitor.json`.

## Operation

### Logs

```bash
journalctl -u mqtt2sqlite --since today
```

The service logs once an hour (`Last hour: 48 messages, 48 stored, 0 ignored`), plus any warnings. Set `level = DEBUG` in the config to see every write.

### Disk Usage

```bash
du -h /var/lib/telemetry/
```

### Backup

The backup command is safe while the service is running. Copy the backup file to another machine.

```bash
sqlite3 /var/lib/telemetry/telemetry.db ".backup '/home/dfannin/telemetry-backup.db'"
```

### Rebuild the Hourly Summaries

Use this after changing data by hand, or after an upgrade that changes how summaries are calculated:

```bash
sudo systemctl stop mqtt2sqlite
```

```bash
sudo -u telemetry /opt/mqtt2sqlite/venv/bin/python /opt/mqtt2sqlite/mqtt2sqlite.py -c /etc/mqtt2sqlite/mqtt2sqlite.ini --rebuild-hourly
```

```bash
sudo systemctl start mqtt2sqlite
```

### Upgrade the Ingester

```bash
sudo install -m 644 server/mqtt2sqlite.py /opt/mqtt2sqlite/ && sudo systemctl restart mqtt2sqlite
```

## Querying the Data

### SQL

Daily battery energy and charge, using local dates:

```sql
SELECT date(ts, 'unixepoch', 'localtime') AS day,
       ROUND(SUM(energy_in_wh), 1)  AS charged_wh,
       ROUND(SUM(energy_out_wh), 1) AS discharged_wh,
       ROUND(SUM(charge_ah), 2)     AS net_ah
FROM power_1h
WHERE hub = 'shed00' AND sensor = 'battery'
GROUP BY day ORDER BY day DESC LIMIT 14;
```

Coldest and warmest hour per day:

```sql
SELECT date(ts, 'unixepoch', 'localtime') AS day,
       MIN(temperature_c_min) AS low_c, MAX(temperature_c_max) AS high_c
FROM env_1h GROUP BY day ORDER BY day DESC LIMIT 14;
```

### Python / pandas

```python
import sqlite3
import pandas as pd

db = sqlite3.connect("file:/var/lib/telemetry/telemetry.db?mode=ro", uri=True)
df = pd.read_sql(
    "SELECT ts, bus_voltage, current FROM power_5m "
    "WHERE hub = 'shed00' AND sensor = 'battery' AND ts >= strftime('%s', 'now', '-7 days')",
    db)
df["time"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert("America/Los_Angeles")
print(df.set_index("time").resample("1D")[["bus_voltage", "current"]].agg(["min", "mean", "max"]))
```

## Troubleshooting

### Grafana: "attempt to write a readonly database" or "unable to open database file"
- Check the directory: `ls -ld /var/lib/telemetry` should show `drwxrwsr-x telemetry telemetry`. The `s` is the setgid bit.
- Check that the drop-in is active: `systemctl show grafana-server -p SupplementaryGroups` should show `telemetry`. If it doesn't, run `sudo systemctl daemon-reload` and restart Grafana.
- Check the database files: `ls -l /var/lib/telemetry` should show group `telemetry` and `rw` for the group.

### Grafana Panels Say "No data"
- No telemetry yet: the tables are created by the first message. Check with `sqlite3 /var/lib/telemetry/telemetry.db .tables`.
- The **Hub** variable must match `mqtt_client_id` (`shed00`).
- Watch the raw messages: `mosquitto_sub -h localhost -t 'telemetry/#' -v`.

### Service Does Not Connect to MQTT
- `journalctl -u mqtt2sqlite` shows `MQTT connect failed: Not authorized` if the username, password or ACL is wrong (step 2).

### Messages Are "ignored"
- The log says why, for example `not valid JSON` or `missing or invalid "type"`. Only messages with `hub`, `type` and at least one numeric field are stored.

### Time Is Wrong on the Graphs
- Points use the ESP32's `epoch`. Until the ESP32 has synced NTP, it sends `uptime_ms` instead, and the time the message arrived is used. Grafana shows times in the browser's timezone.
