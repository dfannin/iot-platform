# Mosquitto (MQTT Broker)

Configuration of the Mosquitto broker on the IoT server (Mosquitto 2.0.22, Ubuntu package). The two configuration files are copies of the server's, byte for byte; the access rules are a plan that is not active yet.

## Files

| File | Installed as | Status |
|---|---|---|
| `mosquitto.conf` | `/etc/mosquitto/mosquitto.conf` | Same as the server (the package default with the log line commented out) |
| `conf.d/local.conf` | `/etc/mosquitto/conf.d/local.conf` | Same as the server: password file, plain listener 1883, TLS listener 8883 |
| `acl.example` | `/etc/mosquitto/acl` (later) | **Not active**: planned access rules, one user per device and service |

**Not in git** (secrets, or created on the server):

| On the server | What it is | How it is made |
|---|---|---|
| `/etc/mosquitto/passwords` | Usernames and password hashes (owner `mosquitto`, mode 600) | `sudo mosquitto_passwd /etc/mosquitto/passwords <user>` |
| `/etc/mosquitto/certs/cert.pem`, `key.pem` | The broker's self-signed TLS certificate and private key (owner `mosquitto`, mode 600 directory) | Created once with `openssl`; devices hold the matching certificate in `ca_cert.h` |
| `/etc/mosquitto/conf.d/README` | Package file | Ubuntu package |

Users today: an admin user (a personal name, not written here) and `telemetry` (`mqtt2sqlite`, `mqtt2slack`).

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
| Plain listener 1883 | Open on every interface | Unencrypted MQTT on the LAN; the devices' plain-text fallback can land here |
| Logging | `log_type error` only | Cannot see which client uses which port |

## Tightening the Broker (Planned Order)

Each step is a separate change, checked before the next. None is part of the restructure's phase 3.

1. **Log connections.** Add `log_type notice` to `conf.d/local.conf` and restart. The log then shows each client's connection and port, so you can see who still uses 1883.
2. **One user per device and service.** Create `backyard00`, `shed00`, `garage00` (and later `control`) with `mosquitto_passwd`; put each device's user in its `secrets.h` and reflash. The planned `add-device` tool (architecture §10) will do this.
3. **Access rules.** Copy `acl.example` to `/etc/mosquitto/acl` (replace `ADMIN_USER`), add `acl_file /etc/mosquitto/acl` to `conf.d/local.conf`, restart, and watch the log for `denied`.
4. **TLS-only devices.** Remove the plain-text fallback from the firmware (architecture O7) and check in the log that no device connects on 1883.
5. **Plain listener on localhost only.** Change `listener 1883` to `listener 1883 127.0.0.1`, for the local services only.

To check a configuration before restarting: `sudo mosquitto -c /etc/mosquitto/mosquitto.conf -v` in a terminal while the service is stopped, or restart and read `journalctl -u mosquitto`.
