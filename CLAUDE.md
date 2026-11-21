# CLAUDE.md — iot-platform

Shared server side for the home IoT projects: Mosquitto configuration, the MQTT contract, the Python services (`mqtt2sqlite`, `mqtt2slack`, later the control service), shared Grafana provisioning and the installer. Devices (`esp32-door`, `esp32-shed`, `esp32-garage`) and `router-monitor` live in their own repositories.

## Read first

- `docs/architecture.md`: goals, architecture, MQTT contract outline, security, roadmap, decisions. **Authoritative.** §15 has the repository layout and the restructure phases.
- `docs/architecture-review.md`: why things were added, deferred or left out.

If code would differ from these documents, stop and ask before changing either. When a design decision is made or changed, update `docs/architecture.md` (revision history and the affected section) in the same change.

## Rules that must not be broken

1. **No secrets or personal identifiers in the repository**: no passwords, Slack tokens or webhook URLs, Mosquitto password files, private keys, MAC or public IP addresses. Configuration with secrets lives outside the repository (`/etc/<service>/`, mode 640 or 600); the repository holds `*.example` files only.
2. **Tests never contact the real broker, Slack or the server.** Use temporary databases, fake HTTP servers and (later) the device simulator.
3. **Move first, change later.** A move keeps the code and the server install paths as they are; behavior changes are separate commits (§15.2).
4. **Times are UTC epoch seconds (INTEGER)** in every database.
5. **Grafana queries views**, not tables, for new dashboards.
6. **MQTT commands are never retained**; the device is the authority for commands and settings (§5).
7. **Devices connect with TLS only** (decision A9).

## Environment

- Server: Ubuntu 22.04, system Python 3.10; each service runs in its own venv under `/opt/<service>/` with a systemd unit. Runtime dependency: `paho-mqtt` (standard library otherwise).
- Dev tools: `pytest`, `ruff` (`requirements-dev.txt`), `shellcheck`.

## Commands

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest                                   # all tests
ruff check . && ruff format --check .
python3 grafana/telemetry/build_dashboards.py   # regenerate telemetry dashboards
python3 grafana/shared/build_shared_alerting.py # regenerate the shared alerting file
shellcheck deploy/*.sh
deploy/install-grafana-shared.sh --root /tmp/gs-test   # test run (needs /tmp/gs-test/etc/grafana)
```

## Repository map

| Path | Contents |
|---|---|
| `docs/` | Architecture, review, later the MQTT contract |
| `services/mqtt2sqlite/` | MQTT → SQLite ingester (moved from `esp32-monitor/server/`, history kept) |
| `services/mqtt2slack/` | MQTT → Slack alerts (moved from `esp32-monitor/server/`; later the alert router) |
| `services/README.md` | Installation and operation of both services |
| `grafana/telemetry/` | Telemetry datasource, IoT dashboards and their generator |
| `grafana/shared/` | Host-wide Grafana alerting: Slack contact point, template, the one policy tree, webhook drop-in, and `build_shared_alerting.py` (moved from `router-monitor`, history kept) |
| `deploy/install-grafana-shared.sh` | Installs `grafana/shared/` on the server (`--root DIR` for a test run) |
| `tests/` | Tests for platform-level files (shared alerting, installers) |
| `mosquitto/` | Broker configuration (byte-identical to the server's) and `acl.example`, the planned access rules (not active); README has the order for tightening the broker |

## Working style

- One restructure phase or roadmap item at a time; finish with tests passing and `ruff` clean.
- Keep parsing and rules in pure functions tested without I/O; keep I/O at the edges.
- Small, readable code over abstraction. Documents in GitHub Markdown.
- The moved services are excluded from `ruff` until they are packaged (a later, separate step).
