# iot-platform

The shared server side for my home IoT projects: the ESP32 devices (shed, doors, garage) and the gateway router monitor.

**Stack:** Mosquitto (MQTT) · SQLite · Grafana · small Python services · NetBird mesh VPN for remote access. All open source and light enough for a small fanless server.

## Documents

| Document | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Goals, the current setup, target architecture, MQTT contract outline, firmware, alerts, control, testing and operations, external access, security, roadmap, decisions and open items |
| [docs/architecture-review.md](docs/architecture-review.md) | Comparison with other IoT architectures, missing and extra features, and what was decided |

## Planned Contents

| Path | Contents | Comes from |
|---|---|---|
| `mosquitto/` | Broker configuration and ACL | Not yet in git |
| `docs/mqtt-contract.md` | Topics and payloads every device follows | New |
| `contract/` | JSON Schemas for every message type, `contract-check` | New |
| `tools/` | `add-device` onboarding, device simulator | New |
| `services/mqtt2sqlite/` | MQTT → SQLite ingester | `esp32-monitor/server/` |
| `services/mqtt2slack/` | MQTT → Slack (later the alert router with a rules file) | `esp32-monitor/server/mqtt2slack.py` |
| `services/control/` | FastAPI control service and `garagebot` | New (`esp32-garage` design §11.1) |
| `grafana/shared/` | Host-wide alerting: Slack contact point, template, policy tree | `router-monitor/deploy/grafana/shared/` |
| `grafana/telemetry/` | Telemetry datasource, dashboards and generator | `esp32-monitor/server/grafana/` |
| `deploy/` | Installer, systemd units, Caddy | New |

Nothing has moved yet. The order of the work is in [Restructure Phases](docs/architecture.md#152-restructure-phases); firmware images for OTA will live on the server, not in git.

## Related Repositories

All checked out side by side under `~/src/iot/`:

| Repository | Role |
|---|---|
| [esp32-door](https://github.com/dfannin/esp32-door) | Door sensors |
| [esp32-monitor](https://github.com/dfannin/esp32-monitor) (to be renamed `esp32-shed`) | Shed power and environment monitor |
| [esp32-garage](https://github.com/dfannin/esp32-garage) | Garage door monitor and controller |
| [garage-control](https://github.com/dfannin/garage-control) (moving into `esp32-garage/hardware/`, then archived) | Garage bus interface hardware |
| [router-monitor](https://github.com/dfannin/router-monitor) | AT&T Internet Air gateway monitor |
