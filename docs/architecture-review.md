# Architecture Review

| | |
|---|---|
| Date | 2026-11-21 |
| Reviewed | [architecture.md](architecture.md) draft 0.3 |
| Result | Decisions recorded in draft 0.4 |

The review compared the platform with common IoT architectures, looked for missing features, and looked for parts that are extra or too complex. The **Decision** column records what was chosen.

## 1. Comparison with Common IoT Architectures

| Architecture | What it is | Similar to ours | What it does better | Borrowed |
|---|---|---|---|---|
| "MING" / TIG stack (Mosquitto, InfluxDB, Node-RED, Grafana; Telegraf) | The most common DIY setup | Nearly the same, with InfluxDB and Node-RED instead of SQLite and Python services | Telegraf's MQTT consumer replaces a custom ingester with configuration | No: `mqtt2sqlite` exists, is tested and creates tables itself |
| Home Assistant + ESPHome | Dominant home-automation platform | Same MQTT and device ideas | Discovery, automations, phone app; ESPHome as a firmware framework | The discovery format (X2). ESPHome not adopted (X4) |
| openHAB / ThingsBoard | Java home and industrial platforms | Registry, rule engine, dashboards | Built-in rules and dashboards | No: too heavy for the server |
| Cloud IoT designs (AWS IoT Core, Azure IoT Hub) | Commercial fleet platforms | Broker, rules engine, registry | Device shadow, fleet OTA, provisioning, per-device certificates | Fleet OTA (M1), onboarding (M4) |
| Homie / Sparkplug B | Open MQTT conventions for self-describing devices | Our former `meta/` idea | Already specified | Considered; Home Assistant discovery chosen because it can point at our existing topics |
| Prometheus + Alertmanager | Metrics and alert routing | Alert router and Grafana alerting | Grouping, silences, inhibition | Silences (M5) and inhibition (M6), later |

**Overall:** the design is the MING stack with Python services instead of Node-RED, plus a small control service. The main gaps were fleet-management features: OTA, onboarding, self-monitoring and contract testing.

## 2. Missing Features

| # | Feature | Summary | Decision |
|---|---|---|---|
| M1 | Fleet OTA | CI-built images, catalog on the server, update by command with hash check and rollback | **Adopt now** |
| M2 | Platform self-monitoring | Service heartbeats, disk and database alerts, outside dead-man's switch | **Adopt now** |
| M3 | Contract schemas, checker, device simulator | JSON Schema per message type, live checker, Python fake device | **Adopt now** |
| M4 | Device onboarding | `add-device` script: MQTT user, ACL, `secrets.h` | **Adopt now** |
| M5 | Alert silences and maintenance mode | Silence rules with expiry, set from Slack or the registry | Wanted later |
| M6 | Alert inhibition | One "broker/network down" alert instead of one per device | Wanted later |
| M7 | Full desired vs reported state | Pending settings tracked per device | Not now |
| M8 | Automations and schedules | Safety rules on the device; cross-device rules in the control service | Wanted later |
| M9 | Server configuration as code, restore test | One installer for the host; tested restore | Wanted later |
| M10 | Local development stack | `docker compose` for development | Wanted later |
| M11 | Certificate lifecycle | Expiry tracking and alerts, renewal steps | Wanted later |
| M12 | Second notification channel (ntfy) | Self-hosted phone push | Not now |

## 3. Extra or Too Complex

| # | Item | Concern | Decision |
|---|---|---|---|
| X1 | Generic auto-generated control UI | A framework for one controllable device | **Phase it:** registry, then hand-written garage controls, then generated UI |
| X2 | Our own `meta/` schema | Design and testing of a new format | **Adopt Home Assistant device-based discovery**, unless it proves verbose or missing something; then fall back to a small `meta/` |
| X3 | Two alert paths | Two filter mechanisms; Grafana could do most filtering | **Keep both:** Grafana's 1–2 minute delay is too long for door and garage events |
| X4 | Custom firmware library vs ESPHome | ESPHome would cover much of the library | **Keep the custom library:** current firmware works well; avoid a large new dependency |
| X5 | Sleepy power class, retained `set/` | No battery device yet | **Defer** |
| X6 | VictoriaMetrics option | Second time-series technology | Stays deferred to the network-monitoring requirements |
| X7 | Caddy | One more component | Keep |
| X8 | `garagebot` inside the control service | Couples Slack to the web service | Keep: one command path and one audit log |
