# Mosquitto (MQTT Broker)

Configuration of the Mosquitto broker on the IoT server (Mosquitto 2.0.22, Ubuntu package). The two configuration files match the server's (phase 3, plus tightening steps 1 and 5); the access rules are a plan that is not active yet.

## Files

| File | Installed as | Status |
|---|---|---|
| `mosquitto.conf` | `/etc/mosquitto/mosquitto.conf` | Same as the server (the package default with the log line commented out) |
| `conf.d/local.conf` | `/etc/mosquitto/conf.d/local.conf` | Password file, plain listener 1883 on `127.0.0.1` only (step 5), TLS listener 8883; connection logging (step 1) |
| `acl.example` | `/etc/mosquitto/acl` (with the admin user's name) | Access rules, one user per device and service (step 3) |

**Not in git** (secrets, or created on the server):

| On the server | What it is | How it is made |
|---|---|---|
| `/etc/mosquitto/passwords` | Usernames and password hashes (owner `mosquitto`, mode 600) | `sudo mosquitto_passwd /etc/mosquitto/passwords <user>` |
| `/etc/mosquitto/certs/cert.pem`, `key.pem` | The broker's self-signed TLS certificate and private key (owner `mosquitto`, mode 600 directory) | Created once with `openssl`; devices hold the matching certificate in `ca_cert.h` |
| `/etc/mosquitto/conf.d/README` | Package file | Ubuntu package |

Users today: an admin user (a personal name, not written here) and `telemetry` (`mqtt2sqlite`, `mqtt2slack`).

**Who connects (log, 2026-11-21):** `backyard00`, `shed00` and `garage00` on 8883 (TLS), all logged in as the admin user; `mqtt2slack` and `mqtt2sqlite` on 1883 from `127.0.0.1` as `telemetry`. Nothing else (Node-RED is retired).

## Check that the server matches git

From the root of an `iot-platform` checkout on the server (read-only):

```bash
diff /etc/mosquitto/mosquitto.conf mosquitto/mosquitto.conf && diff /etc/mosquitto/conf.d/local.conf mosquitto/conf.d/local.conf && echo identical
```

## Current State and Gaps

| Item | Today | Risk |
|---|---|---|
| Anonymous access | `allow_anonymous false` on both listeners | — |
| TLS | Listener 8883 with a self-signed certificate | Expiry not tracked (architecture M11) |
| Access rules | **None**: every user with a password may publish and subscribe to every topic, including `cmd/#` | Any device or leaked password can command the garage |
| Plain listener 1883 | `127.0.0.1` only (step 5) | — (a device's plain-text fallback now fails instead of connecting unencrypted) |
| Device users | All three devices log in as the admin user | A device that is taken over has full access; step 2 fixes this |
| Logging | `log_type error` and `log_type notice` (step 1) | — |

## Tightening the Broker (Planned Order)

Each step is a separate change, checked before the next. None is part of the restructure's phase 3. **Done: 1, 4 and 5.** Step 5 was brought forward after the log showed that no LAN client uses 1883.

1. ✅ **Log connections** (in `conf.d/local.conf`). The log shows each client's connection and port, so you can see who still uses 1883. Install and check: see [Step 1 on the Server](#step-1-on-the-server).
2. **One user per device and service.** For each device, on the **production server** from an `iot-platform` checkout:

   ```bash
   sudo python3 tools/add_device.py backyard00
   ```

   It checks the name against `acl.example`, backs up the password file, adds the user with a random password, keeps the file's owner and mode, reloads Mosquitto and prints the `MQTT_USER` / `MQTT_PASSWORD` lines. On the **development machine**, put those lines in the device's `secrets.h` and upload over OTA. Check: the log shows the device connecting as its own user (`u'backyard00'`). `--rotate` sets a new password for an existing user. Repeat for `shed00` and `garage00` (and later `control`); the admin user keeps working throughout.
3. **Access rules.** Install `acl.example` as `/etc/mosquitto/acl` with the admin user's name, switch on `acl_file` in `conf.d/local.conf` and restart: see [Step 3 on the Server](#step-3-on-the-server).
4. ✅ **TLS-only devices.** The plain-text fallback is removed from all three devices (architecture O7; door 4.5.0, shed 1.12.0, garage 2.4.2-m3a), and the log shows all of them on 8883.
5. ✅ **Plain listener on localhost only:** `listener 1883 127.0.0.1`, for the local services only. Install and check: as step 1 (below), with the backup named `.before-step5`; then confirm both services reconnected (`New client connected from 127.0.0.1 ... as mqtt2slack` and `... as mqtt2sqlite`).

## Step 3 on the Server

Run on the **production server**, from the root of an up-to-date `iot-platform` checkout. Steps 1, 2, 4 and 5 must be done first: every device logs in as its own user.

**1. Install the rules** with the admin user's name in place of `ADMIN_USER` (the name is typed here, so it never goes into git):

```bash
read -r -p "Admin MQTT user: " ADMIN
sed "s/^user ADMIN_USER\$/user $ADMIN/" mosquitto/acl.example | sudo install -m 600 -o mosquitto -g mosquitto /dev/stdin /etc/mosquitto/acl
```

**2. Check them before switching on.** No placeholder left, and every user in the password file has rules (a user without rules is locked out):

```bash
sudo grep -c '^user ADMIN_USER' /etc/mosquitto/acl
diff <(sudo cut -d: -f1 /etc/mosquitto/passwords | sort) <(sudo awk '$1=="user"{print $2}' /etc/mosquitto/acl | sort)
```

Expected: `0`, then only `> control` (planned for the control service, not created yet). A line starting with `<` is a user with a password but no rules: stop and fix `acl.example` first.

**3. Switch on and restart:**

```bash
sudo cp -p /etc/mosquitto/conf.d/local.conf /etc/mosquitto/conf.d/local.conf.before-step3
sudo install -m 644 mosquitto/conf.d/local.conf /etc/mosquitto/conf.d/local.conf
sudo systemctl restart mosquitto && systemctl is-active mosquitto
```

**4. Check** (`active`, then within about three minutes):

```bash
sudo grep "New client connected" /var/log/mosquitto/mosquitto.log | tail -5
```

All five clients back: `backyard00`, `shed00`, `garage00`, `mqtt2sqlite` and `mqtt2slack`. Then check that data still flows: new points in Grafana within 5 minutes, and a door change reaching Slack.

**Undo** (rules off again; the ACL file can stay, it is unused without `acl_file`):

```bash
sudo cp -p /etc/mosquitto/conf.d/local.conf.before-step3 /etc/mosquitto/conf.d/local.conf && sudo systemctl restart mosquitto
```

**Adding a device later:** add its `user` block to `acl.example` first (in git), install the rules again (step 1 above), restart Mosquitto, then run `tools/add_device.py`.

## Step 1 on the Server

From the root of an up-to-date `iot-platform` checkout:

```bash
sudo cp -p /etc/mosquitto/conf.d/local.conf /etc/mosquitto/conf.d/local.conf.before-step1
sudo install -m 644 mosquitto/conf.d/local.conf /etc/mosquitto/conf.d/local.conf
sudo systemctl restart mosquitto && systemctl is-active mosquitto
```

`active` means the broker accepted the configuration; the devices reconnect within seconds. If it is not active, restore the old file and restart: `sudo cp -p /etc/mosquitto/conf.d/local.conf.before-step1 /etc/mosquitto/conf.d/local.conf && sudo systemctl restart mosquitto`.

Which client uses which port (after a while). Each connection gives two lines: `New connection from <address> on port <port>`, then `New client connected from <address> as <client id> (..., u'<user>')`:

```bash
sudo grep -E "New connection from|New client connected" /var/log/mosquitto/mosquitto.log | tail -40
```

To check a configuration before restarting: `sudo mosquitto -c /etc/mosquitto/mosquitto.conf -v` in a terminal while the service is stopped, or restart and read `journalctl -u mosquitto`.
