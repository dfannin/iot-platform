"""Host-wide Grafana alerting (grafana/shared/README.md).

Checks the generated file (contact point, template, the one policy tree) and that no
Slack webhook URL is committed anywhere. Moved from router-monitor's
tests/test_alerting.py; the router's own rules are still tested there.
"""

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERATOR = ROOT / "grafana" / "shared" / "build_shared_alerting.py"

spec = importlib.util.spec_from_file_location("build_shared_alerting", GENERATOR)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)

SHARED = json.loads(build.OUT.read_text())


def test_file_is_up_to_date():
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_shared_file_has_contact_points_templates_and_policies_but_no_rules():
    assert set(SHARED) == {"apiVersion", "templates", "contactPoints", "policies"}


def test_one_policy_tree_routing_everything_to_slack():
    (tree,) = SHARED["policies"]
    assert tree["receiver"] == build.CONTACT_POINT
    assert tree["repeat_interval"] == "4h"
    assert set(tree["group_by"]) <= {"alertname", "project", "severity"}
    receivers = {cp["name"] for cp in SHARED["contactPoints"]}
    assert build.CONTACT_POINT in receivers


def test_contact_point_reads_the_webhook_from_the_environment():
    (receiver,) = SHARED["contactPoints"][0]["receivers"]
    assert receiver["type"] == "slack"
    assert receiver["settings"]["url"] == "$SLACK_ALERTS_WEBHOOK_URL"
    assert receiver["disableResolveMessage"] is False  # "resolved" is sent too


def test_contact_point_uses_the_shared_template():
    defined = set(re.findall(r'define "([^"]+)"', SHARED["templates"][0]["template"]))
    settings = SHARED["contactPoints"][0]["receivers"][0]["settings"]
    used = {name for text in settings.values() for name in re.findall(r'template "([^"]+)"', text)}
    assert used and used <= defined


def test_description_only_while_firing():
    text = SHARED["templates"][0]["template"]
    firing = text.index('if eq .Status "firing" -}}\n{{ with .Annotations.description')
    assert firing < text.index("Since") < text.index("Resolved")


def test_dropin_reads_the_webhook_from_a_secrets_file():
    conf = (ROOT / "grafana/shared/grafana-server.service.d/alerting.conf").read_text()
    assert "[Service]" in conf
    assert re.search(r"^EnvironmentFile=/etc/grafana/secrets/\S+$", conf, re.M)


def test_no_slack_webhook_is_committed():
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    hook = re.compile(r"hooks\.slack\.com/services/[A-Za-z0-9]")
    for name in tracked:
        path = ROOT / name
        if path.is_file() and path.suffix not in {".db", ".png", ".pdf"}:
            assert not hook.search(path.read_text(errors="ignore")), name
