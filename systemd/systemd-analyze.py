#!/usr/bin/env python3
"""systemd-analyze compatibility shim."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, "/opt/systemd-compat")

import systemd_compat as sc  # noqa: E402


def uptime() -> float:
    try:
        return float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError):
        return 1.0


def cmd_time() -> int:
    seconds = uptime()
    kernel = min(seconds * 0.35, 1.8)
    userspace = max(0.0, seconds - kernel)
    print(
        f"Startup finished in {kernel:.3f}s (kernel) + {userspace:.3f}s "
        f"(userspace) = {seconds:.3f}s"
    )
    print(f"graphical.target reached after {userspace:.3f}s in userspace.")
    return 0


def cmd_blame() -> int:
    rows = [
        ("systemd-journald.service", 0.012),
        ("dbus.service", 0.045),
        ("systemd-dbus.service", 0.061),
        ("kasmvnc.service", 0.182),
        ("xfce.service", 0.245),
        ("control.service", 0.088),
    ]
    total = sum(value for _, value in rows)
    print(f"{total:.3f}s graphical.target")
    for unit, value in rows:
        print(f"{value:>8.3f}s {unit}")
    return 0


def cmd_verify() -> int:
    result = 0
    roots = [Path("/etc/systemd/system"), Path("/usr/local/lib/systemd/system")]
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.service")):
            parser = sc.unit_config(path.name)
            exec_start = parser.get("Service", "ExecStart", fallback="").strip()
            if not exec_start:
                print(f"{path}: Service has no ExecStart= setting.")
                result = 1
            if parser.get("Service", "Type", fallback="").strip() == "oneshot" and not exec_start:
                print(f"{path}: Type=oneshot requires ExecStart=.")
                result = 1
    if result == 0:
        print("Unit files are valid.")
    return result


def cmd_cat_config() -> int:
    for path in (
        Path("/etc/supervisor/supervisord.conf"),
        Path("/etc/systemd/system/kasmvnc.service"),
        Path("/etc/systemd/system/xfce.service"),
        Path("/etc/systemd/system/control.service"),
    ):
        if path.is_file():
            print(f"# {path}")
            print(sc.read_text(path).rstrip())
            print()
    return 0


def main(argv: list[str]) -> int:
    if "--version" in argv:
        print("systemd 255")
        return 0
    if "--help" in argv or "-h" in argv:
        print("systemd-analyze [OPTIONS...] COMMAND ...")
        print("  time          Print boot time")
        print("  blame         Print unit startup times")
        print("  verify        Verify unit files")
        print("  cat-config    Print configuration files")
        return 0
    command = next((item for item in argv if not item.startswith("-")), "time")
    if command == "time":
        return cmd_time()
    if command == "blame":
        return cmd_blame()
    if command == "verify":
        return cmd_verify()
    if command == "cat-config":
        return cmd_cat_config()
    print(f"Unknown command '{command}'.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
