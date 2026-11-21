"""Mosquitto configuration in git (mosquitto/README.md).

No secrets, anonymous access off on every listener, and the planned ACL in a form
Mosquitto accepts, with the rules the architecture asks for (§5, §12).
"""

import re
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent / "mosquitto"
CONFIGS = [DIR / "mosquitto.conf", DIR / "conf.d" / "local.conf"]
ACL = DIR / "acl.example"


def lines(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def acl_rules() -> dict[str, list[tuple[str, str]]]:
    """user -> [(access, topic)] from acl.example."""
    rules: dict[str, list[tuple[str, str]]] = {}
    user = None
    for line in lines(ACL):
        words = line.split()
        if words[0] == "user":
            user = words[1]
            rules[user] = []
        else:
            assert words[0] == "topic" and user, line
            access, topic = (words[1], words[2]) if len(words) == 3 else ("readwrite", words[1])
            rules[user].append((access, topic))
    return rules


def test_no_secrets_in_the_configuration():
    secret = re.compile(r"^\s*(remote_password|psk|keyfile_password)\b", re.I)
    for path in [*CONFIGS, ACL]:
        for line in path.read_text().splitlines():
            assert not secret.match(line), f"{path.name}: {line}"
    assert not list(DIR.rglob("*.pem")) and not list(DIR.rglob("*.key"))
    assert not (DIR / "passwords").exists()


def test_every_listener_refuses_anonymous_clients():
    conf = lines(DIR / "conf.d" / "local.conf")
    listeners = [i for i, line in enumerate(conf) if line.startswith("listener ")]
    assert listeners
    for start, end in zip(listeners, [*listeners[1:], len(conf)], strict=True):
        assert "allow_anonymous false" in conf[start:end], conf[start]


def test_connections_are_logged():
    # Shows which client uses which listener (tightening step 1, mosquitto/README.md).
    conf = lines(DIR / "conf.d" / "local.conf")
    assert "log_type notice" in conf and "log_type error" in conf


def test_password_file_is_used():
    assert "password_file /etc/mosquitto/passwords" in lines(DIR / "conf.d" / "local.conf")


def test_acl_is_not_active_yet():
    assert not any(line.startswith("acl_file") for path in CONFIGS for line in lines(path))


def test_acl_lines_are_valid():
    for user, rules in acl_rules().items():
        assert rules, user
        for access, topic in rules:
            assert access in {"read", "write", "readwrite", "deny"}, (user, access)
            assert topic and " " not in topic


def test_only_the_control_service_sends_commands_and_settings():
    for user, rules in acl_rules().items():
        if user in {"control", "ADMIN_USER"}:
            continue
        for access, topic in rules:
            if topic.startswith(("cmd/", "set/")) or topic == "#":
                assert access == "read", (user, topic)
    control = acl_rules()["control"]
    assert ("write", "cmd/#") in control and ("write", "set/#") in control


def test_services_only_read():
    assert all(access == "read" for access, _ in acl_rules()["telemetry"])


def test_each_device_writes_only_its_own_topics():
    devices = {"backyard00": "backyard", "shed00": "shed", "garage00": "garage"}
    rules = acl_rules()
    for hub, loc in devices.items():
        for _access, topic in rules[hub]:
            assert topic.split("/")[1] in {loc, "device"}, (hub, topic)
            if topic.startswith("homeassistant/"):
                assert topic == f"homeassistant/device/{hub}/config"
        assert ("read", f"cmd/{loc}/#") in rules[hub]
