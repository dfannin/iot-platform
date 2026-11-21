"""deploy/install-grafana-shared.sh, run as a test run (--root DIR) only."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "deploy" / "install-grafana-shared.sh"
SHARED = ROOT / "grafana" / "shared"


def run(host: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["bash", str(SCRIPT), "--root", str(host), *args], capture_output=True, text=True
    )
    if check:
        assert result.returncode == 0, result.stderr
    return result


@pytest.fixture
def host(tmp_path):
    (tmp_path / "etc/grafana").mkdir(parents=True)
    return tmp_path


def test_installs_both_files_and_restarts_grafana(host):
    out = run(host).stdout
    json_file = host / "etc/grafana/provisioning/alerting/shared-alerting.json"
    dropin = host / "etc/systemd/system/grafana-server.service.d/alerting.conf"
    assert json_file.read_bytes() == (SHARED / "alerting/shared-alerting.json").read_bytes()
    assert dropin.read_bytes() == (SHARED / "grafana-server.service.d/alerting.conf").read_bytes()
    assert oct(json_file.stat().st_mode & 0o777) == "0o644"
    assert "would run: systemctl daemon-reload" in out
    assert "would run: systemctl restart grafana-server" in out
    assert "files changed: 2" in out


def test_second_run_changes_nothing(host):
    run(host)
    out = run(host).stdout
    assert "files changed: 0" in out
    assert "would run" not in out


def test_changed_json_restarts_grafana_without_daemon_reload(host):
    run(host)
    (host / "etc/grafana/provisioning/alerting/shared-alerting.json").write_text("{}\n")
    out = run(host).stdout
    assert "files changed: 1" in out
    assert "restart grafana-server" in out
    assert "daemon-reload" not in out


def test_webhook_file_checks(host):
    assert "no Slack webhook file" in run(host).stdout
    secret = host / "etc/grafana/secrets/slack-webhook-router-monitor"
    secret.parent.mkdir(parents=True)
    secret.write_text("https://example.invalid/hook\n")
    assert "must hold SLACK_ALERTS_WEBHOOK_URL=" in run(host).stdout
    secret.write_text("SLACK_ALERTS_WEBHOOK_URL=https://example.invalid/hook\n")
    assert "WARN" not in run(host).stdout


def test_no_grafana_is_an_error(tmp_path):
    result = run(tmp_path, check=False)
    assert result.returncode == 1
    assert "install Grafana first" in result.stderr


def test_help_and_unknown_option():
    help_out = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True)
    assert help_out.returncode == 0 and "--root DIR" in help_out.stdout
    bad = subprocess.run(["bash", str(SCRIPT), "--bogus"], capture_output=True, text=True)
    assert bad.returncode == 2


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_shellcheck():
    subprocess.run(["shellcheck", str(SCRIPT)], check=True)
