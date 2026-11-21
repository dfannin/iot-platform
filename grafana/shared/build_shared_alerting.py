"""Build the host-wide Grafana alerting provisioning file as JSON.

grafana/shared/alerting/shared-alerting.json holds the Slack contact point, the
message template and the notification policy tree for every project on the Grafana
server (README.md in this folder, architecture §8). Grafana keeps one policy tree
per organisation and provisioning replaces all of it, so this is the only file on
the host that may define policies. Projects (router-monitor, ESP devices, ...) ship
rule files only, with the labels `project` and `severity`.

Grafana reads alerting provisioning files ending in .yaml, .yml or .json, all with
its YAML parser (JSON is valid YAML), so the file is written as JSON and the tests
can check it without a YAML library.

The Slack webhook URL is not in the file: the contact point reads it from the
environment variable SLACK_ALERTS_WEBHOOK_URL, which systemd sets from a secrets
file outside the repository (README.md).

Moved from router-monitor's tools/build_alerting.py (design §5.6), which keeps the
router's own rules.

Usage:
    python3 grafana/shared/build_shared_alerting.py          # rewrite the JSON file
    python3 grafana/shared/build_shared_alerting.py --check  # exit 1 if out of date
"""

import argparse
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "alerting" / "shared-alerting.json"

ORG = 1
CONTACT_POINT = "slack-alerts"
WEBHOOK_ENV = "SLACK_ALERTS_WEBHOOK_URL"
TEMPLATE_GROUP = "slack-alerts"
SEVERITIES = ("critical", "warning", "info")  # the `severity` label of every rule

# One layout for every alert on the host. The description explains the problem, so
# it is shown only while firing; a resolved message says what ended and when.
# Notification templates are not interpolated with environment variables.
TEMPLATE = """\
{{ define "slack-alerts.title" -}}
{{ if eq .Status "firing" }}FIRING{{ else }}RESOLVED{{ end }}: {{ .CommonLabels.alertname }}
{{- with .CommonLabels.project }} ({{ . }}){{ end }}
{{- end }}

{{ define "slack-alerts.text" -}}
{{ range .Alerts -}}
{{ if eq .Status "firing" }}:rotating_light:{{ else }}:white_check_mark:{{ end }} \
*{{ .Annotations.summary }}*{{ with .Labels.severity }} [{{ . }}]{{ end }}
{{ if eq .Status "firing" -}}
{{ with .Annotations.description }}{{ . }}
{{ end -}}
Since {{ .StartsAt.Local.Format "Mon 2006-01-02 15:04 MST" }}
{{- else -}}
Resolved {{ .EndsAt.Local.Format "Mon 2006-01-02 15:04 MST" }}
{{- end }}
{{ end -}}
{{- end }}
"""


def shared() -> dict:
    return {
        "apiVersion": 1,
        "templates": [{"orgId": ORG, "name": TEMPLATE_GROUP, "template": TEMPLATE}],
        "contactPoints": [
            {
                "orgId": ORG,
                "name": CONTACT_POINT,
                "receivers": [
                    {
                        "uid": CONTACT_POINT,
                        "type": "slack",
                        "disableResolveMessage": False,
                        "settings": {
                            "url": f"${WEBHOOK_ENV}",
                            "title": '{{ template "slack-alerts.title" . }}',
                            "text": '{{ template "slack-alerts.text" . }}',
                        },
                    }
                ],
            }
        ],
        # The whole tree for the organisation. Everything goes to Slack; add nested
        # routes (matchers on `project` or `severity`) here, never in a project file.
        "policies": [
            {
                "orgId": ORG,
                "receiver": CONTACT_POINT,
                "group_by": ["alertname", "project"],
                "group_wait": "30s",
                "group_interval": "5m",
                "repeat_interval": "4h",
            }
        ],
    }


def render() -> str:
    return json.dumps(shared(), indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="verify instead of writing")
    args = parser.parse_args()
    content = render()
    if args.check:
        if not OUT.exists() or OUT.read_text() != content:
            print(f"out of date: {OUT}", file=sys.stderr)
            return 1
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(content)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
