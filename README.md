# iot-platform

The shared server side for my home IoT projects: the ESP32 devices (shed, doors, garage) and the gateway router monitor.

**Stack:** Mosquitto (MQTT) · SQLite · Grafana · small Python services · NetBird mesh VPN for remote access. All open source and light enough for a small fanless server.

## Documents

| Document | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Goals, the current setup, target architecture, MQTT contract outline, alerts, control, external access, decisions and open items |

## Planned Contents (Proposed)

| Path | Contents | Comes from |
|---|---|---|
| `mosquitto/` | Broker configuration and ACL | Not yet in git |
| `docs/mqtt-contract.md` | Topics and payloads every device follows | New |
| `services/mqtt2sqlite/` | MQTT → SQLite ingester | `esp32-monitor/server/` |
| `services/alert-router/` | MQTT → Slack with a rules file | `esp32-monitor/server/mqtt2slack.py` |
| `services/control/` | FastAPI control service and `garagebot` | New (`esp32-garage` design §11.1) |
| `grafana/` | Shared provisioning, dashboard and alert generators | `esp32-monitor/server/grafana/`, `router-monitor/deploy/grafana/shared/` |
| `deploy/` | Installer, systemd units, Caddy | New |

Nothing has moved yet; see [Open Items](docs/architecture.md#13-open-items).

## Related Repositories

All checked out side by side under `~/src/iot/`:

| Repository | Role |
|---|---|
| [esp32-door](https://github.com/dfannin/esp32-door) | Door sensors |
| [esp32-monitor](https://github.com/dfannin/esp32-monitor) | Shed power and environment monitor |
| [esp32-garage](https://github.com/dfannin/esp32-garage) | Garage door monitor and controller |
| [garage-control](https://github.com/dfannin/garage-control) | Garage bus interface hardware |
| [router-monitor](https://github.com/dfannin/router-monitor) | AT&T Internet Air gateway monitor |
