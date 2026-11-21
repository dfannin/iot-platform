# Changelog

## Unreleased

- Architecture documents (`docs/architecture.md`, `docs/architecture-review.md`).
- Phase 0: `CLAUDE.md`, `.gitignore`, tool configuration, CI (pytest, ruff, generated-dashboard check, secret check).
- Phase 1: `mqtt2sqlite`, `mqtt2slack` and the telemetry Grafana files moved from `esp32-monitor/server/` with their history. No change to the code or the server install paths.
- Phase 4: links and names for `esp32-shed` (formerly `esp32-monitor`) and `garage-control` (now `esp32-garage/hardware/`, `garage-control` archived); sketches moved locally to `~/src/iot/`.
- Broker tightening step 5 (brought forward): plain listener 1883 on `127.0.0.1` only; the log showed all devices on TLS 8883. Node-RED retired.
- Broker tightening step 1: connection logging (`log_type notice` in `mosquitto/conf.d/local.conf`), with install and check steps in `mosquitto/README.md`.
- Phase 3: Mosquitto configuration in git (`mosquitto/`, byte-identical to the server's), `acl.example` with the planned access rules (not active), README with the current gaps and the order for tightening the broker, tests. Nothing changes on the server.
- Phase 2: host-wide Grafana alerting (`grafana/shared/`) moved from `router-monitor/deploy/grafana/shared/` with its history; its generator split out of router-monitor's `tools/build_alerting.py` (`build_shared_alerting.py`, same output); `deploy/install-grafana-shared.sh` replaces router-monitor's `install.sh --shared-alerting`. The installed files are unchanged.
