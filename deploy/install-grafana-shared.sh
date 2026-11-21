#!/usr/bin/env bash
# Install the host-wide Grafana alerting files (grafana/shared/README.md).
#
#   sudo deploy/install-grafana-shared.sh        install or update
#   deploy/install-grafana-shared.sh --root DIR  test run: files go under DIR, no system changes
#
# Safe to run again: a file is copied only when it differs, and Grafana restarts only then.
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SRC=$REPO/grafana/shared
ROOT=""

usage() { sed -n '2,7p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while (($#)); do
    case $1 in
        --root) ROOT=${2:?--root needs a directory}; shift 2 ;;
        -h | --help) usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [[ -n $ROOT ]]; then
    ROOT=$(cd "$ROOT" && pwd)
elif ((EUID != 0)); then
    echo "run as root: sudo deploy/install-grafana-shared.sh (or --root DIR for a test run)" >&2
    exit 2
fi

GRAFANA=$ROOT/etc/grafana
DROPINS=$ROOT/etc/systemd/system/grafana-server.service.d
# The name is historical (router-monitor was the first project); the file is host-wide.
SECRET=$GRAFANA/secrets/slack-webhook-router-monitor

changed=0
warnings=()
WROTE=0

say() { echo "$*"; }
warn() { warnings+=("$*"); }
sys() {  # a command that changes the running system: only printed in a test run
    if [[ -n $ROOT ]]; then say "would run: $*"; else "$@"; fi
}
put() {  # put SRC DST MODE: copy when missing or different; sets WROTE
    local src=$1 dst=$2 mode=$3
    WROTE=0
    if [[ -f $dst ]] && cmp -s "$src" "$dst"; then
        return
    fi
    install -D -m "$mode" "$src" "$dst"
    say "wrote    ${dst#"$ROOT"}"
    changed=$((changed + 1))
    WROTE=1
}

if [[ ! -d $GRAFANA ]]; then
    echo "no ${GRAFANA#"$ROOT"} on this host: install Grafana first" >&2
    exit 1
fi

put "$SRC/alerting/shared-alerting.json" "$GRAFANA/provisioning/alerting/shared-alerting.json" 644
put "$SRC/grafana-server.service.d/alerting.conf" "$DROPINS/alerting.conf" 644
dropin_changed=$WROTE

if [[ ! -f $SECRET ]]; then
    warn "no Slack webhook file ${SECRET#"$ROOT"} (grafana/shared/README.md)"
elif ! grep -q '^SLACK_ALERTS_WEBHOOK_URL=' "$SECRET"; then
    warn "${SECRET#"$ROOT"} must hold SLACK_ALERTS_WEBHOOK_URL=<url>"
fi

if ((dropin_changed)); then
    sys systemctl daemon-reload
fi
if ((changed)); then
    sys systemctl restart grafana-server
fi

say "files changed: $changed"
for w in "${warnings[@]}"; do say "WARN  $w"; done
