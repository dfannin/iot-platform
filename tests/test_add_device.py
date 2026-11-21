"""tools/add_device.py with a fake mosquitto_passwd; never touches the real broker."""

import importlib.util
import stat
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("add_device", ROOT / "tools" / "add_device.py")
add_device = importlib.util.module_from_spec(spec)
spec.loader.exec_module(add_device)

ACL = (ROOT / "mosquitto" / "acl.example").read_text()


@pytest.fixture
def fake_passwd(tmp_path):
    """A stand-in for mosquitto_passwd -b FILE USER PASSWORD: replaces or adds the line."""
    script = tmp_path / "fake_mosquitto_passwd"
    script.write_text(
        textwrap.dedent("""\
        #!/usr/bin/env python3
        import sys
        args = sys.argv[1:]
        assert args[0] == "-b"
        path, user, pw = args[1:]
        lines = [l for l in open(path).read().splitlines() if not l.startswith(user + ":")]
        lines.append(user + ":$7$101$fakesalt$hash-of-" + pw)
        open(path, "w").write("\\n".join(lines) + "\\n")
    """)
    )
    script.chmod(0o755)
    return script


@pytest.fixture
def passwords(tmp_path):
    p = tmp_path / "passwords"
    p.write_text("admin:$7$101$x$y\ntelemetry:$7$101$x$z\n")
    p.chmod(0o600)
    return p


def run(passwords, fake_passwd, *args):
    return add_device.main(
        [
            *args,
            "--passwords",
            str(passwords),
            "--mosquitto-passwd",
            str(fake_passwd),
            "--no-reload",
        ]
    )


def test_adds_a_planned_device_user(passwords, fake_passwd, capsys):
    assert run(passwords, fake_passwd, "backyard00") == 0
    assert "backyard00" in add_device.existing_users(passwords)
    assert {"admin", "telemetry"} <= add_device.existing_users(passwords)  # others kept
    out = capsys.readouterr().out
    assert '#define MQTT_USER "backyard00"' in out
    password = out.split('#define MQTT_PASSWORD "')[1].split('"')[0]
    assert len(password) == 32
    assert f"hash-of-{password}" in passwords.read_text()


def test_keeps_the_password_file_mode_and_makes_a_backup(passwords, fake_passwd):
    run(passwords, fake_passwd, "shed00")
    assert stat.S_IMODE(passwords.stat().st_mode) == 0o600
    (copy,) = passwords.parent.glob("passwords.bak-*")
    assert "shed00" not in copy.read_text()
    assert stat.S_IMODE(copy.stat().st_mode) == 0o600


def test_refuses_an_existing_user_without_rotate(passwords, fake_passwd, capsys):
    assert run(passwords, fake_passwd, "garage00") == 0
    first = passwords.read_text()
    assert run(passwords, fake_passwd, "garage00") == 1
    assert "already exists" in capsys.readouterr().err
    assert passwords.read_text() == first
    assert run(passwords, fake_passwd, "garage00", "--rotate") == 0
    assert passwords.read_text() != first
    assert passwords.read_text().count("garage00:") == 1


@pytest.mark.parametrize("hub", ["Backyard00", "backyard", "back_yard00", "x0", "backyard00; rm"])
def test_rejects_bad_hub_names(passwords, fake_passwd, hub):
    assert run(passwords, fake_passwd, hub) == 2


def test_rejects_a_device_missing_from_the_acl_plan(passwords, fake_passwd, capsys):
    assert run(passwords, fake_passwd, "porch00") == 2
    assert "acl.example" in capsys.readouterr().err
    assert "porch00" not in add_device.existing_users(passwords)


def test_every_planned_device_passes_the_acl_check():
    for hub in ("backyard00", "shed00", "garage00"):
        assert add_device.check_acl(hub, ACL) is None, hub


def test_acl_check_needs_the_device_topics():
    bad = "user porch00\ntopic write sensor/other/#\ntopic read cmd/porch/#\n"
    assert "sensor/porch/#" in add_device.check_acl("porch00", bad)


def test_location_and_password_shape():
    assert add_device.location_of("backyard00") == "backyard"
    pw = add_device.new_password()
    assert len(pw) == 32 and not set(pw) & set("\"'\\ ")


def test_a_missing_password_file_is_an_error(tmp_path, fake_passwd, capsys):
    assert run(tmp_path / "missing", fake_passwd, "backyard00") == 2
    assert "not found" in capsys.readouterr().err
