# IoT Platform Architecture

| | |
|---|---|
| Status | **Draft 0.25**: architecture and restructure agreed; restructure phases 0–6 done |
| Date | 2026-11-21 |
| Scope | Shared server side for every home IoT project: ESP32 devices (shed, doors, garage) and the gateway router monitor |

## Revision History

| Rev | Date | Change |
|---|---|---|
| 0.1 | 2026-11-21 | First version: keep MQTT + SQLite + Grafana + small Python services; add an MQTT message contract, a shared firmware library, a FastAPI control service and remote access through a NetBird mesh VPN |
| 0.2 | 2026-11-21 | Security goal G7 and new §11 Security (threat model and protections per layer; decision A9; open items O7, O8). Constraint reworded: the code is small and testable (not teaching material). §12 states exactly what stays in `router-monitor` (collector, database, its dashboards and rules) and what moves here (host-wide Grafana alerting only). Sections 11–13 renumbered to 12–14 |
| 0.3 | 2026-11-21 | Goals G8 (memory-limited, low-power firmware) and G9 (future network and server monitoring); constraints for ESP32 memory and power; §6.1 compile-time feature selection with CI budgets, §6.2 power classes; §9 device registry fields, including the device page URL; plain-text MQTT fallback removed by decision (A9, O7); new §12 Network and Server Monitoring (preliminary); decisions A10, A11; open items O9, O10. Sections 12–14 renumbered to 13–15 |
| 0.4 | 2026-11-21 | Outcome of the [architecture review](architecture-review.md): self-description uses Home Assistant device-based discovery instead of our own `meta/` (§5.1, X2); fleet OTA (§6.3, M1); not ESPHome (X4); sleepy devices deferred (X5); two alert paths kept because Grafana's 1–2 min delay is too long (X3), silences and inhibition later (M5, M6); control UI in three phases (X1); new sections Testing and Operations (M2–M4 now; M9–M11 later) and Roadmap; decisions A12–A14. Sections renumbered |
| 0.5 | 2026-11-21 | §5.1 checked against the Home Assistant documentation source: `uniq_id` added to each component (required), availability mapped to the existing `sensor/<loc>/status` last will, cover state template lower-cased to match the defaults, table of the documented rules (topic, required keys, abbreviations, extra keys ignored, retained vs birth message, removal); O2 updated |
| 0.6 | 2026-11-21 | Repository restructure agreed (§15): layout of this repository, phases 0–6, history kept for moved code, Python services moved as they are, shared library through the Arduino libraries folder and tags, `esp32-monitor` renamed `esp32-shed`, `garage-control` archived |
| 0.7 | 2026-11-21 | Phases 0 and 1 done: scaffold (CLAUDE.md, CI) and `esp32-monitor/server/` moved here with its history (byte-identical files, 70 tests pass, dashboards regenerate identically). Phase 4 also moves the sketches locally from `~/Arduino/` to `~/src/iot/`, symlinked back into `~/Arduino/` |
| 0.8 | 2026-11-22 | Phase 2 done: host-wide Grafana alerting moved from router-monitor to `grafana/shared/` with its history, with `build_shared_alerting.py` and `deploy/install-grafana-shared.sh`. Checked on the server: installed files identical to git, installer reported `files changed: 0`, Slack contact-point test works |
| 0.9 | 2026-11-22 | Phase 3 done: Mosquitto configuration in git (`mosquitto/`), byte-identical to the server's; planned access rules in `mosquitto/acl.example` (not active). Findings added to §12 Known Gaps (no ACL, plain listener on every interface, error-only logging) with the order for fixing them (O11) |
| 0.10 | 2026-11-22 | Broker tightening: connection logging (step 1) done; the log showed all three devices on TLS 8883 (as the admin user) and only the local services on 1883, so step 5 (plain listener on `127.0.0.1` only) was brought forward. Node-RED retired (A8, O5 done). §12 Known Gaps and O11 updated |
| 0.11 | 2026-11-22 | Phase 4 under way: `esp32-monitor` renamed `esp32-shed` (sketch renamed `esp32-shed.ino`, because the Arduino tools need the folder's name); `garage-control` copied into `esp32-garage/hardware/`; links updated; ESP32 core pinned to 3.3.13 in all three device repos. Still to do: archive `garage-control`, local move of the sketches to `~/src/iot/` |
| 0.12 | 2026-11-22 | Phase 4 done: `garage-control` archived; the three sketches moved locally to `~/src/iot/` (symlinked into `~/Arduino/`); `esp32-shed` builds from there with the same size as before (1,266,700 bytes, 96 %) |
| 0.13 | 2026-11-22 | Phase 5 under way: `esp32-iot-common` v0.1.0 (public; `wifi_diag`, `stats_util`), devices switched one PR each. §6.1: all devices use the Minimal SPIFFS partition scheme (1.9 MB app slots; no device uses SPIFFS) and the library has no board-specific code (the boards differ); A10 updated |
| 0.14 | 2026-11-22 | Phase 5 done: door and shed reflashed over USB with the library build and the Minimal SPIFFS layout (door 1,216,012 bytes, 61 %; shed 1,266,784 bytes, 64 %); both reconnected to the broker on TLS 8883 with their settings kept. The garage keeps its current firmware until its next OTA update |
| 0.15 | 2026-11-22 | Phase 6 step 1 done: plain-text MQTT fallback removed from all three devices (door 4.5.0, shed 1.12.0, garage 2.4.2-m3a; the garage's `MQTT_ALLOW_PLAIN_FALLBACK` option removed), updated over OTA, all on TLS 8883 (O7 done; O11 step 4 done). Known Gaps updated |
| 0.16 | 2026-11-22 | `tools/add_device.py` (M4, first version) for O11 step 2: runs on the production server, creates a device's MQTT user and prints its `secrets.h` lines for the development machine. The production server and the development machine are separate machines |
| 0.17 | 2026-11-22 | O11 step 2 done: each device logs in as its own MQTT user. Step 3 prepared: `acl_file` switched on in `conf.d/local.conf`, `acl.example` adds `debug/garage/#` for the garage's bus logger (found by checking every topic the devices use), install and undo procedure in `mosquitto/README.md` |
| 0.18 | 2026-11-22 | O11 done: access rules switched on in production; all clients reconnected as their own users, Grafana without gaps, a simulated garage command reached the device and Slack. Known Gaps updated |
| 0.19 | 2026-11-22 | Phase 6: MQTT connection moved into the library (`esp32-iot-common` v0.2.0: `IotMqtt` and `retry_schedule.h`, host-tested). All three devices switched and updated over OTA (door 4.6.0, shed 1.13.0, garage 2.5.0-m3a); each reconnected to the broker within seconds of rebooting. `MQTT_INSECURE` removed from the devices (all three had it off). Next library modules: NTP, OTA, web page and `/status` |
| 0.20 | 2026-11-22 | Phase 6: NTP time and uptime moved into the library (`esp32-iot-common` v0.3.0: `IotTime`, `uptimeMillis()`, `uptimeSeconds()`, host-tested). All three devices switched and updated over OTA (door 4.7.0, shed 1.14.0, garage 2.6.0-m3a); all show the time synced. O7 closed (`MQTT_INSECURE` removed in 0.19). New open items O12 (the same time fields in every device's `/status`) and O13 (date-based firmware versions) |
| 0.21 | 2026-11-22 | Phase 6: OTA updates and mDNS moved into the library (`esp32-iot-common` v0.4.0: `IotOta`, host-tested). All three devices switched (door 4.8.0, shed 1.15.0, garage 2.7.0-m3a) and each updated twice over OTA, the second time through the library code; `avahi-browse` shows all three with the `_arduino._tcp` service and password-protected uploads. Found in CI: v0.4.0 linked ArduinoOTA and mDNS into every sketch (about 40 KB, even without OTA); v0.5.0 makes `iot_ota.h` an opt-in, header-only module (§6.1) |
| 0.22 | 2026-11-22 | Phase 6 done: the common `/status` sections (`device`, `network`, `mqtt`) and the text helpers moved into the library (`esp32-iot-common` v0.6.0: `iot_status`, host-tested), one field structure on all three devices (O12 done; the garage's `mqtt.last_error_at_s` became `last_error_uptime`, `mqtt.secure` dropped). Door 4.9.0, shed 1.16.0, garage 2.8.0-m3a updated over OTA; all three report `time` and `time_synced`. The shared web page frame is dropped from the plan: the door and shed build their pages in the sketch and the garage serves a static page with JavaScript, so a shared frame would mean rewriting pages for little gain. Library releases are tagged by CI (`tag` workflow) from `library.properties` |
| 0.23 | 2026-11-22 | O13 done: device firmware uses date versions `YYMMDD-HHMM` from the git committer time in UTC, with `-dirty` or `-local`, plus a separate `build` field with the commit hash (§6.4, A15); the shared library keeps semantic versions. Rolled out with `esp32-iot-common` v0.7.0 (the first tag created by its `tag` workflow): door `261122-0552`, shed `261122-0556`, garage `261122-0558`, each with its `build` hash on the web page and in `/status` |
| 0.24 | 2026-11-23 | Shared device web pages (§6.5, A16; reverses the 0.22 decision to keep the pages in each device): one page shell from the library, drawn from `/status`; common cards Device Identity, System Status, Network Settings, MQTT Status; device cards, pages and controls in the device's `device.js`; pages embedded gzip-compressed. `/status` contract written down (§6.6) with the new `device.chip`. Self-description and settings move to the control service work (O2) |
| 0.25 | 2026-11-23 | Shared web pages done (§6.5): esp32-door, esp32-shed and esp32-garage serve every page from `esp32-iot-common` (v0.8.0; v0.8.1 widens the WiFi signal graph's range for strong signals, found on the garage at −21 dBm). No device has HTML or CSS of its own; each firmware is about 10 KB smaller. Device pages: the shed's I2C page (data moved from `/i2c` to `/api/i2c`, now including the SHT30), the garage's Simulator (same door status as the Home card; opens without a login, every button asks for it) and Bus pages |

## Contents

1. [Goals](#1-goals)
2. [Where We Are Today](#2-where-we-are-today)
3. [Verdict on the Current Stack](#3-verdict-on-the-current-stack)
4. [Target Architecture](#4-target-architecture)
5. [The MQTT Contract](#5-the-mqtt-contract)
6. [Shared Firmware Library](#6-shared-firmware-library)
7. [Charts and Events in Grafana](#7-charts-and-events-in-grafana)
8. [Slack Alerts and Filtering](#8-slack-alerts-and-filtering)
9. [Device Control](#9-device-control)
10. [Testing and Operations](#10-testing-and-operations)
11. [External Access](#11-external-access)
12. [Security](#12-security)
13. [Network and Server Monitoring (Future)](#13-network-and-server-monitoring-future)
14. [Roadmap](#14-roadmap)
15. [Repository Layout](#15-repository-layout)
16. [Decisions](#16-decisions)
17. [Open Items](#17-open-items)

## 1. Goals

| # | Goal | Where it is met |
|---|---|---|
| G1 | Adding another ESP32 device is easy | [§5](#5-the-mqtt-contract), [§6](#6-shared-firmware-library) |
| G2 | Adding charts and events to Grafana is easy | [§7](#7-charts-and-events-in-grafana) |
| G3 | It is easy to filter which events reach Slack | [§8](#8-slack-alerts-and-filtering) |
| G4 | Device settings can be changed and devices controlled | [§9](#9-device-control) |
| G5 | Control and graphs can be reached from a browser or phone away from home | [§11](#11-external-access) |
| G6 | Lightweight and open source; runs on the existing server | Throughout |
| G7 | Reasonable protection against attackers: nothing reachable from the internet, every connection authenticated and encrypted, least privilege for devices and services, and a record of every control action | [§12](#12-security) |
| G8 | ESP32 firmware fits memory-limited boards (features can be left out per device) and uses little power | [§6](#6-shared-firmware-library) |
| G9 | *Future:* monitor the home network's servers, PCs and other devices in Grafana, with optional Slack alerts | [§13](#13-network-and-server-monitoring-future) |

**Constraints:**
- The server is a small fanless PC (Celeron N3350, 4 GB RAM, 32 GB eMMC) that already runs Mosquitto and Grafana. Write volume and memory use matter.
- The only internet connection is an AT&T Internet Air gateway (fixed wireless over cellular), which almost certainly sits behind carrier-grade NAT (CGNAT): no inbound connections from the internet.
- The code is small and testable: small single-purpose services and firmware modules, pure functions tested against fixtures, I/O kept at the edges.
- The ESP32 devices have little memory (about 320 KB of usable RAM and 1.2–1.9 MB of flash per OTA app slot on a 4 MB board, with TLS alone taking roughly 40–50 KB of heap). A shared library must not force every feature onto every device.
- The ESP32 devices should use little power. Some run on mains power, but others (the shed monitor) run on solar and battery, where every milliamp counts.

## 2. Where We Are Today

| Repository | Contents |
|---|---|
| `esp32-door` | Multi-door magnetic-switch sensor. MQTT over TLS, retained `sensor/<loc>/<name>`, `sensor/<loc>/status` with last will, telemetry, OTA, mDNS, web page and `/status` JSON |
| `esp32-monitor` | Shed solar/battery and environment monitor, same firmware design. Its `server/` folder is the de facto platform: `mqtt2sqlite.py`, `mqtt2slack.py` and the Grafana dashboards for the shed, doors and device health |
| `esp32-garage` | Garage door monitor and **controller** (Security+ 2.0). First device with commands: `cmd/garage/{left,right}`, `event/garage/…`; planned `garagebot` Slack app and a Mosquitto ACL |
| `garage-control` | Hardware design for the garage bus interface |
| `router-monitor` | Polls the gateway every 5 minutes into its own SQLite database; Grafana dashboards over views; Grafana alerting to Slack, with a host-wide contact point and policy tree in `deploy/grafana/shared/` |

```mermaid
flowchart LR
  D[esp32-door] & S[esp32-monitor] & GA[esp32-garage] --> B[(Mosquitto)]
  B -- "sensor/#" --> A[mqtt2slack] --> SL[Slack]
  B -- "telemetry/#, sensor/#" --> I[mqtt2sqlite] --> DB1[(telemetry.db)]
  A -- "alert history" --> DB1
  R[router-monitor] --> DB2[(router-monitor.db)]
  DB1 & DB2 --> G[Grafana] -- "alert rules" --> SL
```

**What works well:** the firmware repos already share one design and one topic convention. `mqtt2sqlite` creates tables and columns from the messages, so new telemetry needs no schema work. Dashboards and alert rules are generated by Python scripts and tested.

**What hurts:**
- Common firmware code (`wifi_diag.*`, `stats_util.h`, MQTT/TLS fallback, OTA, web) is **copied** into each device repo.
- Platform code lives inside one device repo (`esp32-monitor/server/`) and inside `router-monitor/deploy/grafana/shared/`.
- Two Slack paths (`mqtt2slack` and Grafana alerting) with different filter mechanisms.
- No general way to change device settings, and no remote access except the planned Slack bot.

## 3. Verdict on the Current Stack

**Keep MQTT + SQLite + Grafana + small Python services: the stack is right for this scale.** Formalize the message contract, and add a control service and a mesh VPN.

| Component | Decision | Reason |
|---|---|---|
| **Mosquitto (MQTT)** | Keep; the backbone for every device | Made for small devices: PubSubClient on the ESP32, retained last state, last will for online/offline, topic ACLs. Adding a device costs nothing on the server. Its configuration and ACL move into this repository |
| **SQLite** | Keep | About 50 MB a year, no server process, batched writes are kind to eMMC, easy to query from Python. Reconsider only at tens of writes per second or with several hosts (then VictoriaMetrics or TimescaleDB) |
| **Grafana** | Keep, for **viewing and metric alerts only** | Strong dashboards and alert engine; a poor control panel (awkward buttons, coarse permissions) |
| **Python services** | Keep, consolidated here | One purpose each, but one config style, one installer and one message contract |
| **Node-RED** | Retire | Replaced by `mqtt2slack`; logic in flows is hard to review and test in git |

## 4. Target Architecture

```mermaid
flowchart LR
  subgraph Devices
    D1[esp32-door] & D2[esp32-monitor] & D3[esp32-garage] & DN[new ESP32]
  end
  RM[router-monitor<br/>poll job]
  B[(Mosquitto<br/>ACL per user)]
  Devices -- "discovery, sensor/ telemetry/ event/ config/" --> B
  B -- "cmd/ set/" --> Devices
  RM -. "event/router/… (optional)" .-> B
  RM --> DB
  B --> ING[mqtt2sqlite] --> DB[(SQLite)]
  B --> AL[alert router<br/>rules.toml] --> SL[Slack]
  DB --> G[Grafana<br/>dashboards + metric alerts] --> SL
  B <--> CTL[control service<br/>FastAPI + MQTT]
  CTL --> DB
  SL <-- "Socket Mode" --> CTL
  PH[Phone / browser] -- "NetBird mesh VPN" --> PX[Caddy] --> CTL & G
```

| Service | Language | Reads | Writes | Notes |
|---|---|---|---|---|
| Mosquitto | — | — | — | TLS listener for devices, ACL per MQTT user |
| `mqtt2sqlite` | Python | `telemetry/#`, `sensor/#` | SQLite | From `esp32-monitor/server/` |
| Alert router | Python | `sensor/#`, `event/#` | Slack, alert history | Generalized `mqtt2slack` with a rules file |
| Control service | Python (FastAPI) | `homeassistant/device/+/config`, `config/#`, `sensor/#`, `event/#` | `cmd/#`, `set/#`, audit table | Web UI and the `garagebot` Slack front end |
| Grafana | — | SQLite views | Slack (alert rules) | Existing install, `frser-sqlite-datasource` |
| `router-monitor` | Python | Gateway HTTP API | Its own SQLite | Stays a separate collector |
| Caddy | — | — | — | One host name and TLS for Grafana and the control service |
| NetBird | — | — | — | Mesh VPN client on the server and on phones/laptops |

## 5. The MQTT Contract

The contract is what makes a new device cheap: **a device that follows it gets storage, alerts, dashboards and controls without server code changes.** It will be written out in full as `docs/mqtt-contract.md` (versioned); this section is the outline.

`<loc>` is the location (`shed`, `garage`, `backyard`) and `<name>` is the sensor or actuator, as today. Every payload is JSON with `hub`, `sensor`, `timestamp`/`epoch` as the devices already send.

| Topic | Direction | Retained | Purpose | Status |
|---|---|---|---|---|
| `sensor/<loc>/status` | device → | yes | `ONLINE`; last will `OFFLINE` | In use |
| `sensor/<loc>/<name>` | device → | yes | Current state (`OK`, `OPEN`, `LOW`, …), on change and every 5 min | In use |
| `telemetry/<loc>/<name>` | device → | no | Aggregates every 5 min (avg/min/max); `type` selects the table | In use |
| `event/<loc>/<name>` | device → | no | Something happened (door moved, command result) | In use (garage) |
| `cmd/<loc>/<name>` | → device | **never** | One-shot action (`OPEN`, `CLOSE`, `STOP`, `REBOOT`) with `{"source": "...", "id": "..."}` | In use (garage) |
| `homeassistant/device/<hub>/config` | device → | yes | **New.** Self-description in Home Assistant's device-based discovery format ([§5.1](#51-self-description-home-assistant-discovery)): device name, model, firmware, page URL, and each sensor, setting and command with the topics above | Proposed |
| `set/<loc>/<setting>` | → device | no | **New.** Request to change a setting | Proposed |
| `config/<loc>` | device → | yes | **New.** Current values of all settings, published after the device accepts (and saves to NVS) a change | Proposed |

**Rules:**
- Commands are never retained, so a reconnecting device does not repeat an old command. `set/` messages are retained only for sleepy devices ([§6.2](#62-low-power)), which pick them up when they wake and then clear them.
- Devices connect with **TLS only**; there is no plain-text fallback (decision A9).
- The device is the authority. It validates every `cmd/` and `set/`, refuses unsafe ones (for example, closing a garage door with an obstruction), and reports the result on `event/` or `config/`. The server never assumes a command worked.
- **ACL:** each device user may publish only its own `sensor/<loc>/#`, `telemetry/<loc>/#`, `event/<loc>/#`, `config/<loc>`, `homeassistant/device/<hub>/config` and may subscribe to `cmd/<loc>/#` and `set/<loc>/#`. Only the control service's user may publish `cmd/#` and `set/#`.

**Adding a device:** write firmware with the shared library ([§6](#6-shared-firmware-library)), add a Mosquitto user and ACL entry, and optionally add dashboard panels and alert rules ([§7](#7-charts-and-events-in-grafana), [§8](#8-slack-alerts-and-filtering)). Storage and the control page work from the messages alone.

### 5.1 Self-Description (Home Assistant Discovery)

Instead of designing our own self-description schema, devices use **Home Assistant's MQTT discovery format** (review item X2). It is already specified and documented, it can point at *our existing topics* (no topic changes), and Home Assistant, openHAB and other tools understand it. We use it as a format only; Home Assistant itself is not part of the platform.

We use the **device-based** variant ("device discovery"): **one retained message per device** on `homeassistant/device/<hub>/config`, with abbreviated keys, rather than one message per sensor. A shortened example for the garage:

```json
{
  "dev":  {"ids": "garage00", "name": "Garage", "mdl": "ESP32-DevKitC-V4",
           "sw": "2.3.0", "cu": "http://192.168.1.42/"},
  "o":    {"name": "esp32-iot-common", "sw": "1.0.0"},
  "avty_t": "sensor/garage/status", "pl_avail": "ONLINE", "pl_not_avail": "OFFLINE",
  "cmps": {
    "left":         {"p": "cover", "uniq_id": "garage00_left",
                     "stat_t": "sensor/garage/left", "cmd_t": "cmd/garage/left",
                     "val_tpl": "{{ value_json.opener | lower }}"},
    "close_warn_s": {"p": "number", "uniq_id": "garage00_close_warn_s",
                     "stat_t": "config/garage", "cmd_t": "set/garage/close_warn_s",
                     "val_tpl": "{{ value_json.close_warn_s }}",
                     "min": 0, "max": 120, "step": 1, "unit_of_meas": "s"},
    "reboot":       {"p": "button", "uniq_id": "garage00_reboot",
                     "cmd_t": "cmd/garage/device", "pl_prs": "REBOOT"}
  }
}
```

**Rules from the Home Assistant documentation** (checked 2026-11-21 against the published pages [MQTT](https://www.home-assistant.io/integrations/mqtt/), [MQTT Number](https://www.home-assistant.io/integrations/number.mqtt/), [MQTT Button](https://www.home-assistant.io/integrations/button.mqtt/) and [MQTT Cover](https://www.home-assistant.io/integrations/cover.mqtt/), and their source in `home-assistant/home-assistant.io`):

| Rule | Detail |
|---|---|
| Topic | `<discovery_prefix>/device/<object_id>/config`; prefix `homeassistant`; `<object_id>` from `[a-zA-Z0-9_-]` → we use the hub name |
| Required at the root | `dev` (device) and `o` (origin, with at least `name`); components in `cmps` |
| Required per component | `p` (platform) and, for entities, `uniq_id`; component keys unique within the device |
| Our own topics | Every `*_topic` can be any topic, so `stat_t`/`cmd_t` point at the existing `sensor/`, `cmd/`, `config/`, `set/` topics. A base topic `~` can shorten repeated prefixes |
| Abbreviations | Allowed everywhere "to conserve memory … from memory constrained devices" (`stat_t`, `cmd_t`, `val_tpl`, `uniq_id`, `unit_of_meas`, `pl_prs`, `avty_t`, `cu`, `sw`, `mdl`, `ids`, …) |
| Extra keys | "unknown configuration keys are allowed but ignored", so a field of our own can be added if ever needed |
| Availability | Shared `avty_t` / `pl_avail` / `pl_not_avail` at the root: points at our existing `sensor/<loc>/status` with `ONLINE` / `OFFLINE` |
| Retained | Allowed; the docs prefer re-sending when Home Assistant publishes `online` on `homeassistant/status`. Not needed while Home Assistant is not used; the library may subscribe to it later |
| Remove a device | Empty retained payload on the discovery topic. One component: send it with only `p`, then send the payload without it |

| Need | Discovery feature |
|---|---|
| Device page URL for the registry | `configuration_url` (`cu`): `http://` or `https://` |
| Firmware version | `sw_version` (`sw`) |
| Settings with type and range | `number` (`min` default 1, `max` default 100, `step` default 1, unit, mode), `switch`, `select` |
| Commands | `button` (`pl_prs`, default `PRESS`), `cover` (`pl_open`/`pl_cls`/`pl_stop` default `OPEN`/`CLOSE`/`STOP`, the garage's own commands) |
| Door state | `cover` states default to lower case (`open`, `closed`, `opening`, `closing`, `stopped`), so the template lower-cases the garage's `opener` field |
| Readings and states | `sensor`, `binary_sensor` |

**Check against our needs:** the format meets them and is not verbose for one device (about 1–2 KB with abbreviations). On the ESP32 the message is written in pieces (`beginPublish` / `write` / `endPublish`) so no large buffer is needed, and it can be switched off with `IOT_FEATURE_DISCOVERY`. **If the format ever stops fitting, we fall back to a small `meta/<loc>` message of our own.**

## 6. Shared Firmware Library

A new Arduino library repository, `esp32-iot-common`, holds what every device repeats today:

- WiFi connection, retries and diagnostics (`wifi_diag`), statistics (`stats_util`)
- MQTT over TLS (no plain-text fallback, A9), last will, `sensor/<loc>/status`, reconnect schedule (done in v0.2.0: `IotMqtt`)
- NTP time and uptime (done in v0.3.0: `IotTime`), mDNS and OTA (done in v0.4.0: `IotOta`), the common `/status` JSON sections and text helpers (done in v0.6.0: `iot_status`), the web pages (v0.8.0: `iot_web`, §6.5; the 2026-11-22 decision to keep the pages in each device is reversed)
- **New:** publishing the discovery message ([§5.1](#51-self-description-home-assistant-discovery)), handling `set/<loc>/<setting>` with validation and NVS storage, publishing `config/<loc>`

Device sketches keep only their own sensors, actuators and alarm logic. The library is installed into the Arduino `libraries/` folder (or added as a git submodule) and has its own version and CI build.

### 6.1 Leaving Features Out (Memory)

Each feature is a separate module switched on or off **at compile time** in the device's `settings.h`, so a disabled feature costs no flash and no RAM:

```cpp
// settings.h (per device)
#define IOT_FEATURE_WEB        1   // web page and /status JSON
#define IOT_FEATURE_OTA        1   // over-the-air update
#define IOT_FEATURE_MDNS       1
#define IOT_FEATURE_WIFI_DIAG  0   // WiFi survey and antenna test pages
#define IOT_FEATURE_SETTINGS   1   // set/ and config/ topics, NVS storage
#define IOT_FEATURE_DISCOVERY  1   // self-description (§5.1)
```

- **Required core** (always on): WiFi, MQTT over TLS, NTP, last will/status. Everything else is optional.
- **Opt-in headers:** a module that pulls in a large part of the ESP32 core is not included by the library's umbrella header; the sketch includes it itself (`#include <iot_ota.h>`), and the module is header-only so no library `.cpp` links it in. Found with OTA: v0.4.0 added about 40 KB to every sketch using the library, with or without OTA. CI compiles each example, so size changes show up in the library's own pull request.
- **Defaults** suit a mains-powered board; a small or battery device turns features off.
- **Partition scheme: Minimal SPIFFS on every device** (decided 2026-11-22). On the 4 MB ESP32 it gives two 1.9 MB app slots (running firmware + OTA) instead of 1.25 MB, and 128 KB SPIFFS instead of 1.4 MB. No device stores files in flash (web pages are compiled in; settings live in NVS, which is the same in both layouts), so nothing is lost. The shed firmware went from 96 % of the old slot to about 65 %, the door from 92 % to about 62 %. Changing the layout needs one USB upload per device; OTA cannot change it. Each device repository has a `sketch.yaml` with the board and partition scheme for `arduino-cli`.
- **Boards differ; the library does not care:** ESP32 DEVKITV1 (ESP-WROOM-32) for the door and the shed, ESP32-DevKitC-V4 (ESP32-WROOM-32UE, external antenna, no user LED) for the garage. The library has no pin numbers, LED handling or board checks; anything board-specific stays in the device's `settings.h` and is passed to the library.
- **Budgets checked in CI:** the build prints flash and static RAM use per device, and fails above a set limit (for example 85 % of the app slot), so growth is noticed when it happens. The device reports `free_heap` and `min_free_heap` in telemetry (already done), and the Device Health dashboard shows them.
- **Memory habits in the library:** fixed-size buffers instead of `String` concatenation, `PROGMEM` for web pages, one TLS client reused, JSON written into a fixed buffer.
- The discovery message lists only the sensors, settings and commands compiled in, so the control service shows only what the device can do.

### 6.2 Low Power

> **Deferred** until the first battery-powered device (review item X5). Until then every device is *always connected*; the library rules at the end of this section apply now.

Devices will have one of two **power classes**:

| Class | Examples | How it saves power | Consequence |
|---|---|---|---|
| **Always connected** | Garage (must react to commands), doors | WiFi modem sleep (`WiFi.setSleep(true)`), CPU at 80 MHz, Bluetooth off, sensors read only as often as needed, telemetry aggregated on the device and sent every 5 minutes | Commands arrive within about a second |
| **Sleepy** | A future battery sensor; possibly the shed monitor | Deep sleep between readings; wakes on a timer or a GPIO (door switch), connects, publishes, checks for settings, sleeps again | Cannot receive commands while asleep: its settings use **retained** `set/` messages, which it reads on waking, and the control page shows "applies at next wake-up" |

Library rules for both classes: no busy loops (block on events or `delay()`, which lets the CPU idle), Bluetooth off unless used, the radio used in short bursts, LEDs off or dimmed by default, and brown-out and low-battery states reported on `sensor/<loc>/status`.

### 6.3 Firmware Updates (Fleet OTA)

Today each device is flashed from the Arduino IDE. With several devices, the platform manages updates centrally (review item M1):

1. **CI builds** each device's firmware on a tag (the device repos already build in GitHub Actions) and publishes the binary with its version and SHA-256.
2. **The server keeps a firmware catalog**: one folder per device type, served over HTTPS on the LAN by Caddy.
3. **The control service** shows, in the registry, the running version against the latest one, and sends `cmd/<loc>/device` with `{"cmd": "OTA", "url": ..., "sha256": ..., "version": ...}`.
4. **The device** downloads the image over HTTPS (`HTTPUpdate`), checks the hash, writes it to the spare OTA slot, reboots, and reports the result on `event/<loc>/device`. If the new firmware does not reach MQTT after the restart, the ESP32's rollback returns to the previous slot.

Arduino IDE / `ArduinoOTA` stays available for development. Signed firmware images are a later option.

**Not ESPHome.** ESPHome would provide much of this library (feature selection, OTA, discovery), but the existing firmware works well and is tested, and it would add a large dependency that may not fit our devices (review item X4).

### 6.4 Firmware Versions

Decided 2026-11-22 (O13). One convention for every device, current and future:

| Version | Meaning |
|---|---|
| `261122-1530` | Built from a clean commit: `YYMMDD-HHMM` is the **git committer time in UTC** of `HEAD` |
| `261122-1530-dirty` | The same commit plus uncommitted changes to tracked files (ignored files such as `secrets.h` and `settings.h` do not count) |
| `261122-1530-local` | Built without git or without the version script (Arduino IDE, zip download): the compiler's build time in local time |

- **Rule:** a firmware version always matches `^[0-9]{6}-[0-9]{4}(-dirty|-local)?$`. Nothing else is added to it; any other build detail gets its own field.
- **Always a string**, never an integer (`2611221530` does not fit a signed 32-bit integer). It sorts correctly as text because the width is fixed.
- **`build`:** the short commit hash (7 characters), in `/status` (`device.build`) and in the ONLINE status message next to `firmware`; empty for `-local` builds. It identifies the exact commit even when two commits share a minute.
- **Committer time, not author time:** firmware installed on devices is built from `main`, where a merged pull request is a new merge commit, so the version is the merge time. CI builds of a pull request get a different (earlier or later) version than the merged build; only builds from `main` are installed.
- **Generation:** `tools/fw_version.sh` in `esp32-iot-common` writes `version.h` (ignored by git) in the sketch folder before each compile, run by the device CI and the build command. Without it the library's `iot_version.h` supplies the `-local` fallback.
- **Milestones** (the garage's `-m3a`) are recorded in the device's documents only.
- **The shared library keeps semantic versions** (`vX.Y.Z`): devices pin it, and a minor step signals a change devices must make. Its tags are created by CI.
- **Changeover:** done 2026-11-22 in one round (door `261122-0552`, shed `261122-0556`, garage `261122-0558`; the last semantic versions were 4.9.0, 1.16.0 and 2.8.0-m3a). Anything that compares versions now only sees date versions.

### 6.5 Device Web Pages

Decided 2026-11-23 (A16). Every device serves the same pages from the library (`esp32-iot-common` `iot_web.h`, opt-in); a device has no HTML or CSS of its own.

- **One page shell** for every page, served gzip-compressed from flash; the browser draws it from `/status` (§6.6) with the library's script, every 2 s. Nothing outside the device is loaded (no CDN, no web fonts), so the pages work without internet access. The look is the existing one (white container, green rule, `.info` and `.sensor` cards, round status badges, tab menu).
- **Pages and tabs:** **Home** (`/`), **WiFi** (`/wifi`), the device's own pages, **JSON** (`/status`).
- **Home page:** Device Identity, the device's sensor cards, System Status, Network Settings, MQTT Status:

| Card | Lines |
|---|---|
| **Device Identity** | Device name; chip model (read from the chip, `device.chip`); board (the device's label); firmware with build hash |
| **System Status** | CPU speed; current time; time sync; uptime; last reset; reconnects (WiFi, MQTT); free memory (now and lowest) |
| **Network Settings** | WiFi SSID; signal quality (RSSI in dBm); IP address; MAC address; mDNS host name; access point and channel |
| **MQTT Status** | Status (with how long it has been down); failed attempts and last error, only when there are any; broker; port; username (the client id); status topic; telemetry topic. No encryption line (the port implies TLS) and no client id line |
| **Sensor cards** | One per entry in `sensors[]`; a default card (name, state badge, every plain field, topic) unless the device registers a card for that sensor `type`. A sensor's own topics are on its card |

- **Device extensions** (the device's `web/device.js`, embedded with the library's `tools/web_embed.py`): title and icon, sensor cards per type, own pages with tabs (shown only when relevant, e.g. the garage's Simulator), banners, and controls built from library parts: buttons (POST, optional confirmation), on/off toggles, forms (number, text, list, checkbox fields; filled from the device without overwriting edits). Device endpoints use the library's login check, result answer (`{ok, result, detail}`) and number checks.
- **Size:** the shared shell, stylesheet and script are about 8 KB compressed (about 21 KB as files); a device script is a few hundred bytes to a few KB.
- **Tests:** library host tests for the C++ side; browser tests (headless Chromium in CI) load the real page files against fixture `/status` data.
- **Done 2026-11-23:** library v0.8.0 (v0.8.1: the WiFi signal graph widens its range beyond −95..−25 dBm), then esp32-door (title only; the default door card), esp32-shed (power and environment cards; I2C page with the SHT30, data at `/api/i2c`), esp32-garage (door cards with commands; Simulator page sharing the door status with the Home card, opening without a login while every button needs it; Bus page). Each firmware lost about 10 KB (door 1,218,960 → 1,209,192 bytes; garage 1,259,460 → 1,249,240).

### 6.6 Device `/status` Contract

Every device answers `GET /status` with one JSON object; the web pages (§6.5), the registry and the control service (§9) read only this, so a device on another platform (not an ESP32) fits by serving the same object. `device`, `network` and `mqtt` are the same on every device (`esp32-iot-common` `iot_status`); the rest is the device's own.

| Field | Type | Meaning |
|---|---|---|
| `device.name` | string | Hub name: MQTT client id and mDNS host name |
| `device.firmware`, `device.build` | string | Version (§6.4) and short commit hash (empty for `-local`) |
| `device.chip` | string | Chip model as reported by the chip, e.g. `ESP32-D0WD-V3 rev 3` |
| `device.board` | string | The board, as labelled by the device (module and antenna cannot be read from the chip) |
| `device.cpu_speed` | number | MHz |
| `device.mac`, `device.ip` | string | |
| `device.uptime` | number | Seconds since boot |
| `device.time`, `device.time_synced` | string, bool | Local time `YYYY-MM-DDTHH:MM:SS` (`not_synced` before NTP) |
| `device.reset_reason` | string | `POWERON`, `SOFTWARE`, `BROWNOUT`, `PANIC`, ... |
| `device.free_heap`, `device.min_free_heap` | number | Bytes |
| `device.wifi_reconnects` | number | Since boot |
| `network.ssid`, `network.rssi`, `network.bssid`, `network.channel`, `network.hostname` | string, number | `rssi` in dBm is the signal quality |
| `mqtt.broker`, `mqtt.port`, `mqtt.user` | string, number | |
| `mqtt.connected`, `mqtt.connects`, `mqtt.failed_attempts` | bool, number | `connects - 1` is the number of reconnects |
| `mqtt.disconnected_s` | number, optional | Only while disconnected |
| `mqtt.last_error`, `mqtt.last_error_uptime` | string, number | Empty / 0 until the first failure |
| `mqtt.status_topic`, `mqtt.telemetry_topic` | string | |
| `sensors[]` | array | Each with at least `name`, `type`, `state`, `topic`; any further fields are the device's |

Additions are allowed (readers ignore unknown fields); renaming or removing a field is a contract change recorded here.

## 7. Charts and Events in Grafana

- **Generated dashboards.** Both projects already build dashboards with `build_dashboards.py` (Python → JSON, provisioned from files, checked by tests). Merge them into one generator in this repository with reusable helpers: a device-health row, a state timeline from `state_events`, a telemetry graph by `hub/sensor/field`, an events table. A new chart is a few lines of Python.
- **Views, not tables.** Grafana queries views (the router-monitor rule), so tables can change underneath without breaking dashboards.
- **Folders by domain:** Devices (health), Shed, Doors, Garage, Router. One SQLite datasource per database.
- **Device integrations.** A device repository may ship its own panels and alert rules in an `integration/` folder that the platform's generator and installer pick up ([§15](#15-repository-layout)).

## 8. Slack Alerts and Filtering

There are two kinds of alert, and each gets one owner and one place to filter:

| | Alert router (generalized `mqtt2slack`) | Grafana alerting |
|---|---|---|
| Handles | **States and events** from MQTT: door open, battery LOW, device OFFLINE, garage command results | **Metrics over time**: collector stale, throughput thresholds, battery trends |
| Filter in | `rules.toml` | Rule labels + the shared notification policy |
| Already has | Delays (`OFFLINE` after 15 min), reminders (`COLD` every 6 h), watchdog, outbox for restarts | Shared contact point `slack-alerts`, template, policy tree grouped by `alertname` and `project` |

**Alert router rules.** The fixed `[alerts]`/`[delays]`/`[reminders]` sections become an ordered list of rules; the first match wins and the default is explicit:

```toml
[[rule]]                       # never post routine states
match  = { state = ["OK", "ONLINE", "CLOSED"] }
action = "drop"

[[rule]]                       # garage: everything, to its own channel
match   = { topic = "event/garage/#" }
action  = "post"
channel = "garage"

[[rule]]                       # a door left open at night
match       = { topic = "sensor/backyard/#", state = "OPEN" }
action      = "post"
delay_min   = 10
quiet_hours = "23:00-06:00"    # outside quiet hours only, or "digest"

[[rule]]
match     = { state = "OFFLINE" }
action    = "post"
delay_min = 15
severity  = "warning"

[default]
action = "post"
```

Match fields: `topic` (MQTT wildcard), `hub`, `state`, `severity`. Actions: `post`, `drop`, `digest` (one summary message per period). Options: `channel`, `delay_min`, `repeat_h`, `quiet_hours`.

**Shared vocabulary:** both paths use the severities `critical`, `warning`, `info` and the same channel names. Grafana's host-wide alerting files move from `router-monitor/deploy/grafana/shared/` to this repository, as the router-monitor design (§5.6) already planned for when a second project adds alerts.

**Why two paths (review item X3).** Grafana alerting could do the filtering (matchers, mute timings, pending periods, silences), but its alerts come from the database: batching in `mqtt2sqlite` plus the evaluation interval delays them by 1–2 minutes, which is too long for door and garage events. So state and event alerts stay in the alert router (immediate), and Grafana keeps the metric rules.

**Later (wanted, not yet scheduled):**
- **Silences and maintenance mode** (M5): a `silence` rule with an expiry, set from Slack (`/silence shed 2h`) or from the registry, so working in the shed does not flood Slack.
- **Inhibition** (M6): when all devices go OFFLINE together, post one "broker or network down" message instead of one per device.

## 9. Device Control

A small **control service** (Python, FastAPI + paho-mqtt + SQLite; one process, well under 100 MB):

- **Device registry** built from the discovery messages, `config/#`, `sensor/#` and `sensor/<loc>/status`, stored in SQLite (so Grafana can show it too):

  | Field | Source | Example |
  |---|---|---|
  | Location, hub | discovery | `garage`, `garage00` |
  | Description, board, firmware version | discovery (device block) | Garage door controller, DevKitC-V4, 2.3.0 |
  | **Device page URL** | discovery (`configuration_url`); the device knows its own address | `http://garage00.local/` (link to the device's own web page) |
  | IP address | `/status` JSON or last connection | `192.168.1.42` |
  | Firmware: running vs latest | discovery, OTA catalog ([§6.3](#63-firmware-updates-fleet-ota)) | 2.3.0 → 2.3.1 available |
  | Online/offline, last seen | `sensor/<loc>/status`, any message | ONLINE, 12 s ago |
  | Current states | `sensor/<loc>/#` | left: CLOSED, right: OPEN |
  | Settings (current values), commands | `config/<loc>`, discovery | `close_warn_s = 30`; OPEN/CLOSE/STOP |

  The registry page lists every device with a link to its own web page. The link uses the IP address as well as the `.local` name, because mDNS names do not resolve over the VPN. Reaching the device pages from outside the home needs a NetBird route to the IoT addresses ([§11](#11-external-access)); the device pages stay read-only or password-protected ([§12](#12-security)).
- **Web page, built in phases** (review item X1). Server-rendered HTML with htmx (no JavaScript build), usable on a phone:

  | Phase | Contents | When |
  |---|---|---|
  | 1 | Registry page: every device, online/offline, states, firmware, link to its page | First |
  | 2 | Hand-written garage controls (OPEN/CLOSE/STOP with confirmation) and garage settings | With `esp32-garage` M4 |
  | 3 | Controls **generated from the discovery messages**: a `number` becomes a slider or number box, a `switch` a toggle, a `button` a button (confirmation for anything that moves something) | When a second or third device has controls |
- **One command path:** the service publishes `cmd/` and `set/` as its own MQTT user, then waits for the device's `event/` or `config/` reply and shows the result (or a timeout).
- **Audit table** in SQLite: who, what, when, from where (web or Slack), result. Shown in Grafana.
- **`garagebot`** (Slack Socket Mode, outbound only) runs as a second front end in the same service, so Slack and the web page share permissions and the audit log.
- **Authentication:** its own login (users in a config file, password hashes), session cookies, and the mesh VPN in front ([§11](#11-external-access)).

**Alternatives not chosen:**
- **Home Assistant:** does all of this and has a phone app, but it is heavy for this server, duplicates Grafana and SQLite, and hides the mechanics (harder to test and to keep small). `esp32-garage` design decision D3 also chose not to use it. Using Home Assistant's discovery format for self-description leaves the option open.
- **Node-RED Dashboard:** already installed and quick to build, but its logic lives in flows that are hard to test and review.
- **Grafana buttons:** awkward to build, coarse permissions, no confirmation step.

**Not now:**
- **Full desired-state tracking** ("device shadow", M7): the registry shows the *reported* settings from `config/`; it does not keep a separate desired value and a pending state.
- **Automations and schedules** (M8, wanted later): when they come, safety-related behavior (for example garage auto-close) goes **on the device**; cross-device rules ("warn at 22:00 if a door is open") go in a small scheduler in the control service.

## 10. Testing and Operations

These make the platform dependable and keep "add a device" easy.

### Now

| Item | What | Where |
|---|---|---|
| **Contract schemas and checker** (M3) | A JSON Schema for every message type in the contract; a `contract-check` tool that subscribes to the broker and reports messages that do not match; the same schemas used in the services' tests | `iot-platform/contract/` |
| **Device simulator** (M3) | A Python fake device that speaks the contract (status, states, telemetry, discovery, commands, settings) so services and the control UI can be tested without hardware; builds on `esp32-garage`'s simulated drivers and router-monitor's fake gateway approach | `iot-platform/tools/` |
| **Platform self-monitoring** (M2) | Each service publishes a heartbeat (`status/platform/<service>`) and its counters; Grafana alerts on a stale service, Mosquitto down, disk above 85 %, database growth. An outside dead-man's switch (Healthchecks, open source, hosted free tier or self-hosted later) alerts when the server itself goes quiet | Services, Grafana rules |
| **Device onboarding** (M4) | `add_device.py <hub>` on the production server: checks the hub against the ACL plan, creates the MQTT user with a random password, reloads Mosquitto and prints the `secrets.h` lines to copy to the development machine (first version, 2026-11-22; activating ACL entries comes with O11 step 3) | `iot-platform/tools/add_device.py` |

### Later (wanted, not yet scheduled)

| Item | What |
|---|---|
| **Server configuration as code and restore test** (M9) | One installer for the whole host (Mosquitto, Grafana, services, firewall, NetBird, Caddy); restore from backup onto a spare machine or VM once to prove it works |
| **Local development stack** (M10) | `docker compose` with Mosquitto, Grafana and the services, for development only; production stays systemd |
| **Certificate lifecycle** (M11) | Record the CA and broker certificate expiry dates, alert 30 days ahead, document renewal (long-lived CA on the devices, shorter-lived broker certificate) |

**Not now:** a second notification channel such as ntfy (M12).

## 11. External Access

### The Problem

Away from home, a phone needs to reach Grafana and the control page on the home server. The usual answer (open a port on the router) does not work here: AT&T Internet Air is fixed wireless on the cellular network, and cellular carriers put customers behind **CGNAT**, so the gateway has no public IPv4 address and nothing on the internet can start a connection to it. Every workable solution has the home server **connect out**, as `garagebot` does with Slack Socket Mode.

### Decision: Mesh VPN, Hosted NetBird Now, Self-Hosted Later

The server and each phone or laptop run a **NetBird** client and join a private network. Traffic between them is WireGuard, encrypted end to end. From anywhere, `https://iot-server/grafana` and `https://iot-server/control` work as they do at home.

- **Nothing is exposed to the internet:** no port forwarding, no public login page. Only enrolled devices can connect.
- **One host name:** Caddy on the server serves Grafana and the control service under one name with TLS.
- **App logins stay on:** Grafana users, the control service login, and confirmations for dangerous commands.
- **Slack stays** the no-VPN path for alerts and quick garage commands.

### What the Third Party Does (and Does Not Do)

| Part | What it does | Hosted NetBird | Self-hosted later |
|---|---|---|---|
| Coordination (management + signal) | Keeps the device list, exchanges WireGuard public keys, enforces access rules | NetBird's servers | Our VPS |
| Data path | The actual traffic, WireGuard end to end | Direct between devices when possible | Same |
| Relay (fallback) | Carries traffic when devices cannot connect directly (more likely behind CGNAT); cannot decrypt it | NetBird's relays | Our VPS |

- **Privacy:** the provider cannot read our traffic; it does know which devices exist and when they connect.
- **Availability:** if the hosted service is down, existing connections usually continue; new logins and new devices fail until it is back.
- **Exit path:** all NetBird server components are open source. Moving them to a small VPS (about $4–6 a month) needs no client changes. That is the "self-host later" step.

### Alternatives

| Option | Why not (now) |
|---|---|
| Tailscale | Same model with the most polished apps, but the coordination service is closed source. A fine substitute if NetBird disappoints |
| Headscale | Open-source Tailscale coordination server; needs a VPS from day one |
| Plain WireGuard | Needs an inbound port, blocked by CGNAT on IPv4. **Might work over IPv6**: see [Open Items](#17-open-items) |
| Public tunnel (Cloudflare Tunnel, ngrok) | Puts the login page, and garage control, on the public internet; mostly closed source |

**Limitation of every option:** when the gateway is down, remote access is down too. router-monitor already accepts the same limit for its Slack alerts.

## 12. Security

The aim is **reasonable protection for a home system**: an attacker on the internet should find nothing to attack, and an attacker who gets onto the home network (a guest, a compromised gadget, a stolen phone) should not be able to control devices or read secrets. Defending against physical access to the hardware or a determined, well-funded attacker is out of scope.

### Threats

| # | Threat | Main protection |
|---|---|---|
| T1 | Someone on the internet probes the home | Nothing listens publicly: CGNAT, no port forwarding, remote access only through the mesh VPN ([§11](#11-external-access)) |
| T2 | Someone on the home WiFi (guest, compromised smart TV or ESP32) sniffs or sends MQTT messages | TLS on MQTT, no anonymous login, one user per device, topic ACLs; only the control service may publish `cmd/#` and `set/#` |
| T3 | A device is compromised or its credentials leak | Its MQTT user can publish only its own topics and cannot command other devices; revoke by deleting the user |
| T4 | A phone or laptop is lost or stolen | Remove it from NetBird; the control service and Grafana still need their own logins |
| T5 | Someone gets into Slack (or Slack is misused) | `garagebot` accepts commands only from a list of Slack user IDs; workspace two-factor authentication; every command is audited |
| T6 | A secret ends up in git | Secrets live only in config files outside the repositories (mode 600/640); a test fails if a token or webhook URL appears in a tracked file (as router-monitor does) |
| T7 | Unpatched software is exploited | Automatic security updates on the server; pinned, reviewed versions of Python packages and Grafana plugins |
| T8 | A bad command damages something (garage door closes on a car) | The device enforces safety itself and refuses unsafe commands; confirmation in the UI; the server never assumes success |

### Protections by Layer

| Layer | Protection |
|---|---|
| **Network** | No public listeners. Firewall (`ufw`) on the server allows only the MQTT TLS port, Caddy (HTTPS) and SSH from the LAN and the VPN. Optional: put the ESP32s on a separate guest/IoT WiFi network if the gateway supports it |
| **Remote access** | NetBird mesh VPN only; access rules let phones reach Caddy, not every port on the server; devices removed when lost |
| **MQTT broker** | TLS listener for the LAN; plain-text listener on `localhost` only (for local services); `allow_anonymous false`; one user per device and per service; ACL per [§5](#5-the-mqtt-contract) |
| **Devices** | MQTT over TLS only (the plain-text fallback is removed); per-device MQTT credentials and CA certificate in `secrets.h`/`ca_cert.h` (not in git); OTA protected by a password; the device web page is read-only or password-protected for anything that changes state; commands and settings validated on the device |
| **Web (Caddy, control service, Grafana)** | HTTPS everywhere; control service login with hashed passwords, secure session cookies, CSRF protection and confirmation for actions that move hardware; Grafana anonymous access off, admin password changed, other users as Viewers |
| **Slack** | Bot tokens in config files outside git; Socket Mode (outbound only); allow-list of user IDs for commands |
| **Server** | Each service runs as its own system user with systemd hardening (`NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, only the directories it needs writable); SSH by key only; automatic security updates |
| **Audit and detection** | Every command and setting change in the audit table (who, what, when, from where, result), shown in Grafana; Slack alert for a device that goes OFFLINE or a failed login burst |
| **Recovery** | Nightly SQLite backups (as router-monitor does); configuration in git; secrets kept separately |

### Known Gaps

- **Plain-text fallback:** removed (2026-11-22). All three devices are TLS-only (door 4.5.0, shed 1.12.0, garage 2.4.2-m3a) and the broker accepts plain MQTT only from its own host (O7 done).
- **Broker (found in phase 3):** fixed 2026-11-22 (O11): access rules are on, each device has its own user, and plain MQTT is limited to the server itself.
- **No outside monitoring of the server itself.** If the server is down, nothing alerts (the same gap router-monitor accepts).

## 13. Network and Server Monitoring (Future)

> **Preliminary.** The requirements are not written yet (open item O9). This section records the intended shape so the IoT work does not block it.

**Goal:** show the health of the home network's servers, PCs and other devices (NAS, printers, access points, TVs) in Grafana, with optional Slack alerts. It is **separate from the IoT controls**: no control service, no Caddy, no MQTT commands. Remote viewing comes for free through Grafana over the VPN.

**Likely measurements:**

| Kind | Applies to | Examples |
|---|---|---|
| Reachability | Every device with an address | Up/down by ping, response time, packet loss |
| Services | Servers | HTTP/TLS checks (certificate expiry), SSH, DNS, Mosquitto, Grafana |
| Host metrics | Linux servers and PCs | CPU, memory, disk space, temperature, load, uptime, systemd failed units |
| Inventory | The network | New or unknown device appeared on the LAN (from the gateway's device list or an ARP scan) |

**Preliminary direction:**

- **A separate collector project** (`net-monitor`, own repository), like `router-monitor`: it owns its collection, storage and dashboards, and uses only the shared Grafana alerting (contact point and policy, label `project = net-monitor`) from this repository.
- **Collection:** a host list in a config file. Use standard open-source agents where they fit (Prometheus `node_exporter` on Linux hosts for host metrics; ping/HTTP checks from the server, for example with `blackbox_exporter`) rather than writing them.
- **Storage, to be chosen with the requirements:**

  | Option | When it fits |
  |---|---|
  | **SQLite + small Python poller** (as `router-monitor`) | A few dozen hosts, ping and service checks every minute or more; one technology across the platform |
  | **VictoriaMetrics (single node)** | Host metrics from agents at 15–60 s intervals; Prometheus-compatible, light (tens of MB of RAM), compact on disk, works with the existing exporters and Grafana |

- **Alerts:** Grafana alert rules (host down for N minutes, disk above 90 %, certificate expiring) through the shared Slack contact point, filtered by `project` and `severity` like every other rule ([§8](#8-slack-alerts-and-filtering)).
- **Security:** agents listen on the LAN only, and the firewall on each host allows their port only from the monitoring server ([§12](#12-security)).

## 14. Roadmap

| Stage | Contents |
|---|---|
| **1. Foundation** | Repository restructure; Mosquitto config and ACL in git; TLS-only firmware; MQTT contract with schemas, checker and simulator; `add-device`; platform self-monitoring |
| **2. Devices** | `esp32-iot-common` with compile-time features; discovery messages; fleet OTA |
| **3. Control** | Control service phase 1 (registry), NetBird + Caddy, phase 2 (garage controls and `garagebot`) |
| **4. Later** | Silences and inhibition (M5, M6), automations (M8), configuration as code (M9), dev stack (M10), certificate lifecycle (M11), control UI phase 3, network monitoring ([§13](#13-network-and-server-monitoring-future)) |
| **Not planned** | Full device shadow (M7), second notification channel (M12), ESPHome (X4), sleepy devices until a battery device exists (X5) |

## 15. Repository Layout

Agreed 2026-11-21. The work is done in phases ([§15.2](#152-restructure-phases)); phases 0–5 are done.

### 15.1 Repositories

| Repository | Role |
|---|---|
| `iot-platform` (this repository) | Everything server side and shared: Mosquitto configuration and ACL, the MQTT contract, `mqtt2sqlite`, alert router, control service (with `garagebot`), Grafana shared provisioning and the dashboard/alert generators, one installer, architecture documents. Takes over `esp32-monitor/server/` and `router-monitor/deploy/grafana/shared/` |
| `esp32-iot-common` (new) | Arduino library for all firmware ([§6](#6-shared-firmware-library)) |
| `esp32-door`, `esp32-shed` (renamed from `esp32-monitor`), `esp32-garage` | Firmware only, plus an `integration/` folder (device manifest, dashboard panels, alert rules) later. `garage-control` moves into `esp32-garage/hardware/` and is then archived |
| `router-monitor` | Stays a standalone project: collector, database, its own dashboards and alert rules (see below) |
| `net-monitor` (future) | Network and server monitoring ([§13](#13-network-and-server-monitoring-future)), built the same way as `router-monitor` |

**What stays in `router-monitor` and what moves.** Only the *host-wide* Grafana pieces move; everything specific to the router stays:

| Stays in `router-monitor` | Moves to `iot-platform` |
|---|---|
| Router client (login, GET-only reads), parsing, poll job and systemd timer | `deploy/grafana/shared/`: the `slack-alerts` contact point, message template and notification policy tree, and its Grafana drop-in for the webhook secret |
| SQLite schema, migrations, views (`v_sample`, `v_status`, …), backups | |
| Its Grafana datasource, dashboards (Status, History, Events, Report) and dashboard generator | |
| Its alert rule ("Router collector stale") and rule generator | |
| `install.sh`, `INSTALL.md`, design documents, tests | |

The router's dashboards and rules stay with the router because they depend on its views; they only *use* the shared contact point and policy, through the `project` and `severity` labels. Later, the router's dashboard generator may import shared panel helpers from this repository, but that is optional. The move is a change to `router-monitor`'s design (§5.6, §13) and is made there when the restructure is carried out.

**Local checkout layout:** all repositories side by side under `~/src/iot/`:

```text
~/src/iot/
├── iot-platform/
├── esp32-iot-common/
├── esp32-door/
├── esp32-shed/
├── esp32-garage/
└── router-monitor/
```

**Layout of this repository** (filled in by the phases below):

```text
iot-platform/
├── README.md  CLAUDE.md  CHANGELOG.md
├── docs/           architecture.md, architecture-review.md, mqtt-contract.md (later)
├── mosquitto/      conf.d/iot.conf, acl.example (no password file in git)
├── services/
│   ├── mqtt2sqlite/    from esp32-monitor/server/ (script, ini example, systemd unit, tests)
│   ├── mqtt2slack/     from esp32-monitor/server/ (later becomes the alert router)
│   └── control/        later
├── grafana/
│   ├── shared/         from router-monitor/deploy/grafana/shared/
│   └── telemetry/      from esp32-monitor/server/grafana/ (datasource, dashboards, generator)
├── contract/       schemas and contract-check (later)
├── tools/          add-device, fake device (later)
└── deploy/         installer, systemd, Caddy (later)
```

Firmware images for OTA live on the server, not in git; the repository holds only the scripts that build and serve them.

### 15.2 Restructure Phases

**Principles:** move first, change later (a move keeps the code and the server install paths as they are; behavior changes are separate steps); one repository change at a time, checked before the next; git history kept for code with history; a pointer README left in each old location for a while. Steps on the server are run by hand with a written check.

| Phase | Change | Repositories | Server impact | Check |
|---|---|---|---|---|
| 0 ✅ | Scaffold: `CLAUDE.md`, `.gitignore` for secrets, CI with `pytest` and `ruff` | iot-platform | None | CI passes |
| 1 ✅ | Move `esp32-monitor/server/` **with its history** (`git filter-repo`); scripts, install paths and tests unchanged; pointer README in the old place | iot-platform, esp32-monitor | None | Tests pass; files installed from the new place are identical to the server's |
| 2 ✅ | Move the shared Grafana alerting; small installer here; router-monitor drops `--shared-alerting` and updates its design (§5.6, §13), `CLAUDE.md` and tests | iot-platform, router-monitor | Re-install the same files | `/etc/grafana/provisioning/alerting/` unchanged; contact-point test reaches Slack |
| 3 ✅ | Mosquitto configuration into git from the server's current files, cleaned of secrets, with the ACL plan | iot-platform | None until applied | Broker accepts the configuration |
| 4 ✅ | `garage-control` → `esp32-garage/hardware/`; archive `garage-control`; rename `esp32-monitor` → `esp32-shed` and update links (including the `esp32-door` and `esp32-garage` links to the old `server/`); locally, move the sketches from `~/Arduino/` to `~/src/iot/` and symlink them back into `~/Arduino/` | esp32-garage, esp32-monitor, all READMEs | None | Links and images render; old URLs redirect |
| 5 ✅ | Create `esp32-iot-common` with the files that are identical in every device repo (`wifi_diag.*`, `stats_util.h`; `web_wifi.h` differs and stays in the devices); switch devices one at a time, door first. Done: library `v0.1.0` (public); all three devices switched; door and shed reflashed over USB with Minimal SPIFFS (door 61 %, shed 64 % of the 1.9 MB slot), garage already on it | new repository, device repositories | Reflash each device | Each device builds and reports as before |
| 6 ✅ | ✅ Remove the plain-text MQTT fallback (O7, done 2026-11-22); ✅ one MQTT user per device and ✅ access rules (O11 steps 2–3, broker tightening complete); ✅ MQTT/TLS connection in the library (`v0.2.0`, `IotMqtt`; door 4.6.0, shed 1.13.0, garage 2.5.0-m3a); ✅ NTP time and uptime (`v0.3.0`, `IotTime`; door 4.7.0, shed 1.14.0, garage 2.6.0-m3a); ✅ OTA and mDNS (`v0.4.0`, `IotOta`; door 4.8.0, shed 1.15.0, garage 2.7.0-m3a; opt-in from `v0.5.0`); ✅ common `/status` sections and text helpers (`v0.6.0`, `iot_status`; door 4.9.0, shed 1.16.0, garage 2.8.0-m3a; O12). Web page frame dropped (pages stay in the devices) | device repositories, library | Reflash | No plain-text connections in the broker log |

**Decisions for the restructure:**
- **Git history:** kept for moved code with history (`git filter-repo` of `server/`, merged here). `garage-control` has one commit and is copied.
- **Python services:** moved as they are; turned into one package (one venv, console commands, like router-monitor) in a later, separate step.
- **Shared Arduino library:** developed through a symlink from `~/src/iot/esp32-iot-common` into `~/Arduino/libraries/`; CI checks out a pinned library tag. No git submodules.
- **Renames:** `esp32-monitor` → `esp32-shed` (GitHub redirects the old name; MQTT topics do not change). `garage-control` archived after its move.

## 16. Decisions

| ID | Decision | Alternatives considered | Consequences |
|---|---|---|---|
| A1 | Keep MQTT + SQLite + Grafana + small Python services | Home Assistant; InfluxDB/VictoriaMetrics; TimescaleDB | Light and transparent; we write and maintain the glue code |
| A2 | The MQTT contract ([§5](#5-the-mqtt-contract)) is the integration point; devices describe themselves with Home Assistant device-based discovery | Per-device server code; our own `meta/` schema; Homie | New devices need no server changes; no schema of our own to design; firmware must publish one discovery message and handle `set/` |
| A3 | Shared Arduino library `esp32-iot-common` | Keep copying files; ESPHome (X4) | One fix reaches every device; the library needs versioning |
| A4 | Grafana for viewing and metric alerts only | Grafana as control UI | Control needs its own service (A6) |
| A5 | Two alert paths, one owner each: alert router (`rules.toml`) for states/events, Grafana for metrics; shared severities and channels | Everything in Grafana; everything in the router | Each alert type is filtered in one obvious place |
| A6 | Custom FastAPI control service with an htmx UI built in three phases (registry, garage controls, then generated from discovery); hosts `garagebot` | Home Assistant; Node-RED Dashboard | Small and testable; we build the UI |
| A7 | Remote access through a NetBird mesh VPN, hosted now, self-hosted later | Tailscale; Headscale; plain WireGuard; public tunnel | No open ports; the hosted provider coordinates but cannot read traffic; open-source exit path |
| A8 | Retire Node-RED (done 2026-11-21: stopped, nothing connects to the broker from it) | Keep it | One fewer service on the server |
| A9 | Security by layers ([§12](#12-security)): no public listeners, TLS and per-user ACLs on MQTT, logins on every web UI, least-privilege services, audit of every control action | Rely on the home network being trusted | Some setup work (users, ACLs, certificates); devices connect with TLS only (plain-text fallback removed) |
| A10 | Firmware features chosen at compile time per device; flash/RAM budgets checked in CI; two power classes (always connected, sleepy); Minimal SPIFFS partition scheme on every device; no board-specific code in the library ([§6](#6-shared-firmware-library)) | One full-featured build for all devices | Small boards and battery devices fit; sleepy devices take settings at the next wake-up |
| A11 | Device registry in the control service, with a link to each device's own web page ([§9](#9-device-control)) | Devices found by hand or by mDNS browsing | One page lists every device, its state and its page |
| A12 | Keep two alert paths: alert router for states and events (immediate), Grafana for metrics (X3) | Everything through Grafana alerting | Door and garage alerts are not delayed 1–2 minutes; two places to filter |
| A13 | Fleet OTA: CI-built images, catalog on the server, update by command with hash check and rollback (M1) | Flash each device from the IDE | Versions visible and updates in one place |
| A14 | Contract schemas and checker, device simulator, platform self-monitoring, `add-device` onboarding (M2–M4) | Manual checks | Problems caught by tests and alerts; adding a device is one command |
| A15 | Device firmware versions `YYMMDD-HHMM` from the git committer time in UTC, with `-dirty` / `-local`, and a separate `build` hash ([§6.4](#64-firmware-versions)); the shared library keeps semantic versions | Semantic versions per device; build time; file save time | One convention for every device, sortable as text, no version bumps to forget; the same commit gives the same version everywhere; what changed lives in the README history |
| A16 | Device web pages from the shared library: one page shell drawn from `/status`, common cards, device extensions in `device.js`, gzip-embedded ([§6.5](#65-device-web-pages)); `/status` as a written contract ([§6.6](#66-device-status-contract)) | Pages in each device (the 0.22 decision); C++ templates building HTML on the device | The same pages and cards on every device; a new device gets working pages from `/status` alone; less RAM per request than building HTML on the device |

## 17. Open Items

| # | Item | Notes |
|---|---|---|
| O1 | Repository restructure | **Done 2026-11-22**: phases 0–6 carried out ([§15.2](#152-restructure-phases)). Later, separate steps: package the Python services; the device self-description (§5.1) and settings (`set/`, `config/`) in the library |
| O2 | MQTT contract details | Discovery format checked against the Home Assistant docs (done, §5.1). Still open: `sensor/<loc>/status` kept or renamed; payload versioning; the `cmd/<loc>/device` payloads (REBOOT, OTA); the device self-description (§5.1) and settings (`set/`, `config/`) are built together with the control service and registry (decided 2026-11-23: they have no consumer before then; estimated 10–15 KB of flash per device) |
| O3 | router-monitor events on MQTT | Optional; would add `paho-mqtt` to router-monitor, which is a design change there (its runtime dependency is `requests` only) |
| O4 | IPv6 test | Does the gateway give the server a public IPv6 address and allow an inbound IPv6 port? If so, plain WireGuard is possible with no third party |
| O5 | Retire Node-RED | Done 2026-11-21: stopped and no longer used; the broker log shows no other clients |
| O6 | Control service security | Users and roles, which commands need confirmation, rate limits |
| O7 | TLS-only firmware | Done 2026-11-22: plain-text fallback removed from esp32-door (4.5.0), esp32-shed (1.12.0) and esp32-garage (2.4.2-m3a, with its `MQTT_ALLOW_PLAIN_FALLBACK` option); all reconnected on 8883. `MQTT_INSECURE` (TLS without checking the certificate) removed 2026-11-22 with the move to the library's `IotMqtt` (door 4.6.0, shed 1.13.0, garage 2.5.0-m3a) |
| O8 | Separate IoT WiFi network | Check whether the BGW530 can put the ESP32s on a guest/IoT network isolated from laptops and phones |
| O9 | Network and server monitoring requirements | Which hosts, which measurements, retention, which alerts; then choose the storage ([§13](#13-network-and-server-monitoring-future)) |
| O11 | Tighten the broker | Done 2026-11-22, all five steps (`mosquitto/README.md`): connection logging; one user per device (`tools/add_device.py`); access rules (`acl_file`); TLS-only firmware; plain listener on `127.0.0.1` only. Checked: all clients reconnect as their own users, Grafana without gaps, a garage command reaches the device and Slack |
| O10 | Flash/RAM budgets per device | Measure today's builds and set the CI limits. Power classes are deferred (X5) |
| O12 | Same time fields in every `/status` | **Done 2026-11-22** with `esp32-iot-common` v0.6.0 (`iot_status`): all three devices have the same `device`, `network` and `mqtt` sections, including `time` and `time_synced`. Requested 2026-11-22. Today only esp32-garage's `/status` JSON has `"time"` (local `YYYY-MM-DDTHH:MM:SS`, `"not_synced"` before the first sync) and `"time_synced"`; the door and shed show them on the web page only. Give all three devices the same fields and structure, best done when `/status` moves into the library |
| O13 | Date-based firmware versions | **Done 2026-11-22** ([§6.4](#64-firmware-versions)): `YYMMDD-HHMM` from the git committer time in UTC, `-dirty` / `-local`, separate `build` hash; rolled out with `esp32-iot-common` v0.7.0 (door `261122-0552`, shed `261122-0556`, garage `261122-0558`). Requested 2026-11-22: one version convention for all devices, based on the build date instead of semantic versioning (today door 4.7.0, shed 1.14.0, garage 2.6.0-m3a). To decide: the format (for example `2026.11.22` with a suffix for a second release on the same day), what happens to the garage's milestone suffix (`-m3a`), and whether the version is set by hand or by CI. Grafana and the device registry compare versions as text |
