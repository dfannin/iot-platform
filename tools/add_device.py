"""Create the MQTT user for a device on the production server (architecture M4, O11 step 2).

Run on the production server, from the root of an iot-platform checkout:

    sudo python3 tools/add_device.py backyard00
    sudo python3 tools/add_device.py backyard00 --rotate   # new password for an existing user

What it does:
  1. checks the hub name, and that mosquitto/acl.example has a user block for it
     (so the users on the server match the planned access rules);
  2. refuses a user that already exists, unless --rotate;
  3. backs up the password file (passwords.bak-<UTC time>, next to it);
  4. adds the user with a random password (mosquitto_passwd), then restores the
     password file's owner, group and mode (Mosquitto reads it as its own user);
  5. reloads Mosquitto (no client is disconnected);
  6. prints the MQTT_USER / MQTT_PASSWORD lines for the device's secrets.h, which
     lives on the development machine, not here.

The password is shown once, on this terminal only. Copy it into the device's
secrets.h; do not paste it into chats, tickets or git.

Options for tests and dry runs: --passwords FILE, --acl FILE,
--mosquitto-passwd CMD, --no-reload.
"""

import argparse
import os
import re
import secrets
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PASSWORDS = Path("/etc/mosquitto/passwords")
DEFAULT_ACL = ROOT / "mosquitto" / "acl.example"
HUB_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}[0-9]{2}$")  # <location>00, a DNS host name


def location_of(hub: str) -> str:
    """backyard00 -> backyard (the topic location is the hub name without its number)."""
    return hub.rstrip("0123456789")


def acl_rules(text: str) -> dict[str, list[tuple[str, str]]]:
    """user -> [(access, topic)] from a Mosquitto ACL file."""
    rules: dict[str, list[tuple[str, str]]] = {}
    user = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        words = line.split()
        if words[0] == "user" and len(words) == 2:
            user = words[1]
            rules.setdefault(user, [])
        elif words[0] == "topic" and user is not None:
            access, topic = (words[1], words[2]) if len(words) == 3 else ("readwrite", words[1])
            rules[user].append((access, topic))
    return rules


def check_acl(hub: str, acl_text: str) -> str | None:
    """None if acl.example plans this device user correctly, else the problem."""
    rules = acl_rules(acl_text).get(hub)
    if rules is None:
        return f"no 'user {hub}' block in the ACL plan: add it to mosquitto/acl.example first"
    loc = location_of(hub)
    if ("write", f"sensor/{loc}/#") not in rules or ("read", f"cmd/{loc}/#") not in rules:
        return f"'user {hub}' in the ACL plan does not write sensor/{loc}/# and read cmd/{loc}/#"
    return None


def existing_users(passwords: Path) -> set[str]:
    if not passwords.exists():
        return set()
    return {line.split(":", 1)[0] for line in passwords.read_text().splitlines() if ":" in line}


def new_password() -> str:
    # 32 URL-safe characters: no quotes or backslashes, safe inside a C string literal
    return secrets.token_urlsafe(24)


def secrets_lines(hub: str, password: str) -> str:
    return f'#define MQTT_USER "{hub}"\n#define MQTT_PASSWORD "{password}"\n'


def backup(passwords: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    copy = passwords.with_name(f"{passwords.name}.bak-{stamp}")
    shutil.copy2(passwords, copy)
    os.chmod(copy, 0o600)
    return copy


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("hub", help="hub name, e.g. backyard00 (also the MQTT client id)")
    parser.add_argument(
        "--rotate", action="store_true", help="set a new password for an existing user"
    )
    parser.add_argument("--passwords", type=Path, default=DEFAULT_PASSWORDS)
    parser.add_argument("--acl", type=Path, default=DEFAULT_ACL, help="ACL plan to check against")
    parser.add_argument("--mosquitto-passwd", default="mosquitto_passwd")
    parser.add_argument("--no-reload", action="store_true", help="do not reload Mosquitto")
    args = parser.parse_args(argv)

    hub = args.hub
    if not HUB_RE.match(hub):
        print(
            f"error: '{hub}' is not a hub name like backyard00 (lower case, digits at the end)",
            file=sys.stderr,
        )
        return 2
    problem = check_acl(hub, args.acl.read_text())
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2
    if args.passwords == DEFAULT_PASSWORDS and os.geteuid() != 0:
        print("error: run as root: sudo python3 tools/add_device.py ...", file=sys.stderr)
        return 2

    if not args.passwords.exists():
        print(
            f"error: {args.passwords} not found (password_file in conf.d/local.conf)",
            file=sys.stderr,
        )
        return 2

    exists = hub in existing_users(args.passwords)
    if exists and not args.rotate:
        print(
            f"error: user '{hub}' already exists; use --rotate for a new password "
            "(the device is locked out until it gets the new one)",
            file=sys.stderr,
        )
        return 1

    before = args.passwords.stat()
    copy = backup(args.passwords)
    password = new_password()
    cmd = [args.mosquitto_passwd, "-b", str(args.passwords), hub, password]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"error: mosquitto_passwd failed: {result.stderr.strip()}", file=sys.stderr)
        return 1
    # mosquitto_passwd may rewrite the file as root; Mosquitto reads it as its own user
    os.chown(args.passwords, before.st_uid, before.st_gid)
    os.chmod(args.passwords, before.st_mode & 0o777)

    if not args.no_reload:
        reload = subprocess.run(
            ["systemctl", "reload", "mosquitto"], capture_output=True, text=True
        )
        if reload.returncode != 0:
            print(
                f"warning: 'systemctl reload mosquitto' failed: {reload.stderr.strip()}",
                file=sys.stderr,
            )

    print(
        f"{'Rotated' if exists else 'Added'} MQTT user '{hub}' in {args.passwords}"
        + f" (backup: {copy.name})"
    )
    if not args.no_reload:
        print("Mosquitto reloaded.")
    print("\nOn the development machine, put these two lines in the device's secrets.h")
    print("(replace the existing MQTT_USER and MQTT_PASSWORD lines), then upload over OTA:\n")
    print(secrets_lines(hub, password))
    print("Check afterwards: the broker log shows the device connecting as u'" + hub + "'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
