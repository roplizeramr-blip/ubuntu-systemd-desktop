#!/usr/bin/env python3
"""
systemd compatibility layer backed by Supervisor.

This module is the shared core used by the systemctl shim, the D-Bus facade
and the diagnostic tools.  It is deliberately stdlib-only.

It is a *compatibility layer*, not a kernel-level init system.  It provides
the interfaces that most applications use to detect and control systemd:
unit files, systemctl semantics, /run/systemd/system and a D-Bus facade.
"""
from __future__ import annotations

import configparser
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# The desktop account whose systemd *user* session this layer also stands in
# for.  A real systemd user session keeps its units in ~/.config/systemd/user
# and advertises the manager on $XDG_RUNTIME_DIR/bus; both halves of that
# convention are honoured so systemd-user-aware desktop software (the OpenClaw
# Gateway companion, busctl --user, ...) finds what it expects.
DESKTOP_USER = "desktop"
DESKTOP_UID = 1000
DESKTOP_HOME = Path("/home/desktop")
DESKTOP_RUNTIME_DIR = Path("/run/user/1000")

SYSTEM_UNIT_DIRS = [
    Path("/etc/systemd/system"),
    Path("/run/systemd/system"),
    Path("/usr/local/lib/systemd/system"),
    Path("/usr/lib/systemd/system"),
    Path("/lib/systemd/system"),
]
USER_UNIT_DIRS = [
    Path("/etc/systemd/user"),
    Path("/run/user/1000/systemd/user"),
    Path("/usr/local/lib/systemd/user"),
    Path("/usr/lib/systemd/user"),
    Path("/lib/systemd/user"),
    DESKTOP_HOME / ".config/systemd/user",
]
UNIT_DIRS = SYSTEM_UNIT_DIRS + USER_UNIT_DIRS

# This compatibility layer fronts both the real system manager and the
# per-user manager.  Real systemd keeps their unit search paths separate: a
# system query must never resolve a user unit, and a user query must never
# resolve a system unit.  A caller selects a scope before doing lookups.
_ALL_UNIT_DIRS = list(UNIT_DIRS)


def set_scope(scope: str) -> None:
    """Restrict unit lookups to one manager scope: "system", "user" or "all"."""
    global UNIT_DIRS
    if scope == "system":
        UNIT_DIRS = list(SYSTEM_UNIT_DIRS)
    elif scope == "user":
        UNIT_DIRS = list(USER_UNIT_DIRS)
    else:
        UNIT_DIRS = list(_ALL_UNIT_DIRS)


SUPERVISOR_CONF_DIR = Path("/etc/supervisor/conf.d")
SUPERVISORCTL = shutil.which("supervisorctl") or "/usr/bin/supervisorctl"
SUPERVISOR_SOCK = "unix:///run/supervisor.sock"
LOG_DIR = Path("/var/log/supervisor")
DYNAMIC_DIR = Path("/usr/local/lib/systemd-compat")
SYSTEMD_VERSION = "255"
SYSTEMD_PACKAGE_VERSION = "255.4-1ubuntu8.10"
BOOT_TIME_FILE = Path("/run/systemd/system/boot-time")
STATUS_CACHE_TTL = 0.5
_STATUS_CACHE: dict[str, dict[str, Any]] | None = None
_STATUS_CACHE_TIME = 0.0

CORE_UNITS: dict[str, str] = {
    "dbus.service": "dbus",
    "systemd-journald.service": "systemd-journald",
    "systemd-dbus.service": "systemd-dbus",
    "kasmvnc.service": "kasmvnc",
    "xfce.service": "xfce",
    "control.service": "control",
    "tailscaled.service": "tailscaled",
    "tailscale-serve.service": "tailscale-serve",
}
CORE_PROGRAMS = set(CORE_UNITS.values())

TARGET_UNITS = {
    "default.target",
    "graphical.target",
    "multi-user.target",
    "basic.target",
    "sysinit.target",
    "network.target",
    "getty.target",
    "remote-fs.target",
    "local-fs.target",
    "sockets.target",
    "timers.target",
    "paths.target",
    "slices.target",
}
TARGET_DEPS: dict[str, list[str]] = {
    "graphical.target": [
        "kasmvnc.service",
        "xfce.service",
        "control.service",
        "tailscaled.service",
        "tailscale-serve.service",
    ],
    "multi-user.target": [
        "dbus.service",
        "systemd-journald.service",
        "systemd-dbus.service",
    ],
    "basic.target": ["sysinit.target"],
    "sysinit.target": [],
    "network.target": [],
    "remote-fs.target": [],
    "local-fs.target": [],
    "sockets.target": [],
    "timers.target": [],
    "paths.target": [],
    "slices.target": [],
    "getty.target": [],
}
UNIT_SUFFIXES = (
    ".service",
    ".target",
    ".socket",
    ".timer",
    ".path",
    ".mount",
    ".automount",
    ".swap",
    ".slice",
    ".scope",
    ".device",
)


def normalize_unit(name: str) -> str:
    """Return a fully qualified unit name (defaults to .service)."""
    name = name.strip()
    if not name:
        return name
    if name.endswith(UNIT_SUFFIXES):
        return name
    return name + ".service"


def unit_base(name: str) -> str:
    for suffix in UNIT_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def safe_program_name(name: str) -> str:
    base = unit_base(normalize_unit(name))
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", base).strip("_")
    return "unit_" + (safe or "unnamed")


def program_for(unit: str) -> str:
    unit = normalize_unit(unit)
    return CORE_UNITS.get(unit, safe_program_name(unit))


def escape_unit(name: str) -> str:
    """Escape a unit name into a valid D-Bus object path element."""
    out: list[str] = []
    for ch in name:
        if ch.isalnum() or ch == "_":
            out.append(ch)
        else:
            out.append("_%02x" % ord(ch))
    return "".join(out)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def unit_path(unit: str) -> Path | None:
    unit = normalize_unit(unit)
    for directory in UNIT_DIRS:
        candidate = directory / unit
        if candidate.exists() or candidate.is_symlink():
            return candidate
    return None


def is_user_unit(unit: str) -> bool:
    """True when the unit's fragment lives in a systemd *user* unit directory."""
    path = unit_path(unit)
    if path is None:
        return False
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    return any(resolved.parent == directory for directory in USER_UNIT_DIRS)


def is_masked(unit: str) -> bool:
    path = unit_path(unit)
    if path is None or not path.is_symlink():
        return False
    try:
        return os.readlink(path) == "/dev/null"
    except OSError:
        return False


def unit_exists(unit: str) -> bool:
    if normalize_unit(unit) in TARGET_UNITS:
        return True
    return unit_path(unit) is not None


def fragment_path(unit: str) -> str:
    path = unit_path(unit)
    return str(path) if path else ""


def unit_config(unit: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(
        strict=False, interpolation=None, delimiters=("=",)
    )
    parser.optionxform = str  # type: ignore[assignment]
    path = unit_path(unit)
    if path is None or is_masked(unit):
        return parser
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError):
        # Keep whatever was parsed before the error.  The shim must never
        # crash because a third-party unit file uses an unusual directive.
        pass
    return parser


def section_values(unit: str, section: str, key: str) -> list[str]:
    """Every value for key= inside section, in file order.

    ConfigParser keeps only the *last* duplicate, but systemd units legally
    repeat Environment= (one assignment per line) and generated units rely on
    that, so the raw fragment is scanned instead.
    """
    path = unit_path(unit)
    if path is None or is_masked(unit):
        return []
    values: list[str] = []
    current = ""
    for raw in read_text(path).splitlines():
        line = raw.strip()
        if not line or line[0] in ("#", ";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            continue
        if current != section or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == key:
            values.append(value.strip())
    return values


def unit_description(unit: str) -> str:
    parser = unit_config(unit)
    description = parser.get("Unit", "Description", fallback="").strip()
    return description or normalize_unit(unit)


def unit_is_oneshot(unit: str) -> bool:
    parser = unit_config(unit)
    return parser.get("Service", "Type", fallback="").strip().lower() == "oneshot"


def unit_remain_after_exit(unit: str) -> bool:
    parser = unit_config(unit)
    value = parser.get("Service", "RemainAfterExit", fallback="no").strip().lower()
    return value in ("yes", "true", "1", "on")


def supervisorctl(*args: str, timeout: float = 20.0) -> tuple[int, str, str]:
    """Run supervisorctl against the local supervisord socket."""
    command = [SUPERVISORCTL, "-s", SUPERVISOR_SOCK]
    command.extend(args)
    try:
        proc = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            env=os.environ.copy(),
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError:
        return 127, "", "supervisorctl not found"
    except subprocess.TimeoutExpired:
        return 124, "", "supervisorctl timed out"


def invalidate_status_cache() -> None:
    global _STATUS_CACHE, _STATUS_CACHE_TIME
    _STATUS_CACHE = None
    _STATUS_CACHE_TIME = 0.0


def supervisor_status() -> dict[str, dict[str, Any]]:
    """Return {program: {state, pid, line}} from supervisorctl status.

    The result is cached briefly so list-units/status/D-Bus property reads do
    not fork supervisorctl once per unit.
    """
    global _STATUS_CACHE, _STATUS_CACHE_TIME
    now = time.monotonic()
    if _STATUS_CACHE is not None and now - _STATUS_CACHE_TIME < STATUS_CACHE_TTL:
        return _STATUS_CACHE
    result: dict[str, dict[str, Any]] = {}
    _, out, _ = supervisorctl("status", timeout=10)
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        name, state = parts[0], parts[1]
        pid: int | None = None
        if "pid" in parts:
            index = parts.index("pid")
            if index + 1 < len(parts):
                try:
                    pid = int(parts[index + 1].rstrip(","))
                except ValueError:
                    pid = None
        result[name] = {"state": state, "pid": pid, "line": line}
    _STATUS_CACHE = result
    _STATUS_CACHE_TIME = time.monotonic()
    return result


def unit_pid(unit: str) -> int | None:
    status = supervisor_status().get(program_for(unit))
    if status and status.get("pid"):
        return int(status["pid"])
    return None


def get_default_target() -> str:
    path = Path("/etc/systemd/system/default.target")
    try:
        if path.is_symlink():
            return Path(os.readlink(path)).name
    except OSError:
        pass
    return "graphical.target"


def set_default_target(target: str) -> None:
    target = normalize_unit(target)
    path = Path("/etc/systemd/system/default.target")
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    path.symlink_to(f"/etc/systemd/system/{target}")


def target_dependencies(target: str) -> list[str]:
    target = normalize_unit(target)
    if target == "default.target":
        target = get_default_target()
    deps = list(TARGET_DEPS.get(target, []))
    # Only administrator/vendor overrides under /etc and /run are considered.
    # Package-owned *.wants links in /usr/lib would reference services this
    # compatibility layer does not manage, making a target appear stuck in
    # "activating" forever.
    for base in (Path("/etc/systemd/system"), Path("/run/systemd/system")):
        for sub in ("wants", "requires"):
            directory = base / f"{target}.{sub}"
            if not directory.is_dir():
                continue
            for entry in sorted(directory.iterdir()):
                if entry.name.endswith(UNIT_SUFFIXES) and entry.name not in deps:
                    deps.append(entry.name)
    return deps


def target_state(target: str) -> tuple[str, str]:
    target = normalize_unit(target)
    if target == "default.target":
        target = get_default_target()
    deps = target_dependencies(target)
    if not deps:
        return ("active", "active")
    states = [unit_active_state(dep) for dep in deps]
    if any(state[0] == "failed" for state in states):
        return ("failed", "failed")
    if all(state[0] == "active" for state in states):
        return ("active", "active")
    if any(state[0] in ("active", "activating") for state in states):
        return ("activating", "start")
    return ("inactive", "dead")


def unit_active_state(unit: str) -> tuple[str, str]:
    unit = normalize_unit(unit)
    if unit == "default.target":
        unit = get_default_target()
    if unit in TARGET_UNITS:
        return target_state(unit)
    if is_masked(unit) or not unit_exists(unit):
        return ("inactive", "dead")
    status = supervisor_status().get(program_for(unit))
    if not status:
        return ("inactive", "dead")
    state = status["state"]
    if state == "RUNNING":
        return ("active", "running")
    if state == "STARTING":
        return ("activating", "start")
    if state == "BACKOFF":
        return ("activating", "auto-restart")
    if state == "FATAL":
        return ("failed", "failed")
    if state == "STOPPING":
        return ("deactivating", "stop")
    if state == "EXITED":
        if unit_is_oneshot(unit) and unit_remain_after_exit(unit):
            return ("active", "exited")
        return ("inactive", "dead")
    return ("inactive", "dead")


def unit_enabled_state(unit: str) -> str:
    unit = normalize_unit(unit)
    if is_masked(unit):
        return "masked"
    path = unit_path(unit)
    if path is None:
        return "not-found"
    if unit in TARGET_UNITS:
        return "static"
    parser = unit_config(unit)
    if not parser.has_section("Install"):
        return "static"
    wanted = (
        parser.get("Install", "WantedBy", fallback="")
        + " "
        + parser.get("Install", "RequiredBy", fallback="")
    ).split()
    if not wanted and not parser.get("Install", "Alias", fallback="").strip():
        return "static"
    for target in wanted:
        for base in (Path("/etc/systemd/system"), Path("/run/systemd/system")):
            for sub in ("wants", "requires"):
                link = base / f"{target}.{sub}" / unit
                if os.path.lexists(link):
                    return "enabled"
    return "disabled"


def list_all_units() -> list[str]:
    # Only report core units that resolve in the active scope: the per-user
    # manager must not list the appliance's system units (and vice versa), or
    # callers that resolve every listed unit fail.
    units: set[str] = set(TARGET_UNITS)
    for unit in CORE_UNITS:
        if unit_path(unit) is not None:
            units.add(unit)
    for base in UNIT_DIRS:
        if not base.is_dir():
            continue
        for entry in base.iterdir():
            if entry.name.endswith(UNIT_SUFFIXES):
                units.add(entry.name)
    return sorted(units)


def list_unit_files() -> list[tuple[str, str]]:
    seen: dict[str, str] = {}
    for base in UNIT_DIRS:
        if not base.is_dir():
            continue
        for entry in base.iterdir():
            if not entry.name.endswith(UNIT_SUFFIXES):
                continue
            if entry.name not in seen:
                seen[entry.name] = unit_enabled_state(entry.name)
    return sorted(seen.items())


def process_start_epoch(pid: int) -> int:
    """Return the process start time as a Unix epoch, or 0."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        fields = stat.rsplit(")", 1)[1].split()
        start_ticks = int(fields[19])
        clock_ticks = os.sysconf("SC_CLK_TCK")
        btime = 0
        for line in Path("/proc/stat").read_text(encoding="utf-8").splitlines():
            if line.startswith("btime "):
                btime = int(line.split()[1])
                break
        if btime:
            return int(btime + start_ticks / clock_ticks)
    except (OSError, ValueError, IndexError):
        pass
    return 0


def process_memory(pid: int) -> int:
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return 0


def process_tasks(pid: int) -> int:
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("Threads:"):
                return int(line.split()[1])
    except (OSError, ValueError):
        pass
    return 0


def boot_time_epoch() -> int:
    try:
        return int(BOOT_TIME_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def get_properties(unit: str) -> dict[str, Any]:
    """Return a systemd-style property dictionary for a unit."""
    unit = normalize_unit(unit)
    path = unit_path(unit)
    exists = unit_exists(unit)
    masked = is_masked(unit)
    parser = unit_config(unit)
    active, sub = unit_active_state(unit)
    pid = unit_pid(unit) if exists else None
    enabled = unit_enabled_state(unit)
    description = unit_description(unit) if exists else unit
    fragment = "" if masked else (str(path) if path else "")
    invocation = ""
    active_enter = process_start_epoch(pid) if pid else 0
    memory = process_memory(pid) if pid else 0
    tasks = process_tasks(pid) if pid else 0

    def prop(section: str, key: str, fallback: str = "") -> str:
        return parser.get(section, key, fallback=fallback).strip()

    props: dict[str, Any] = {
        "Id": unit,
        "Names": [unit],
        "Description": description,
        "LoadState": "masked" if masked else ("loaded" if exists else "not-found"),
        "ActiveState": active,
        "SubState": sub,
        "FragmentPath": fragment,
        "SourcePath": fragment,
        "UnitFileState": enabled,
        "CanStart": "yes" if exists and not masked else "no",
        "CanStop": "yes" if exists and not masked else "no",
        "CanReload": "yes" if prop("Service", "ExecReload") else "no",
        "CanIsolate": "no",
        "Job": "/",
        "Requires": prop("Unit", "Requires").split(),
        "Wants": prop("Unit", "Wants").split(),
        "Conflicts": prop("Unit", "Conflicts").split(),
        "Before": prop("Unit", "Before").split(),
        "After": prop("Unit", "After").split(),
        "Documentation": prop("Unit", "Documentation").split(),
        "WantedBy": prop("Install", "WantedBy").split(),
        "RequiredBy": prop("Install", "RequiredBy").split(),
        "Alias": prop("Install", "Alias").split(),
        "Also": prop("Install", "Also").split(),
        "MainPID": pid or 0,
        "ControlPID": 0,
        "StatusText": "",
        "MemoryCurrent": memory,
        "TasksCurrent": tasks,
        "CPUUsageNSec": 0,
        "ActiveEnterTimestamp": active_enter * 1_000_000 if active_enter else 0,
        "ActiveEnterTimestampMonotonic": 0,
        "InactiveEnterTimestamp": 0,
        "InactiveEnterTimestampMonotonic": 0,
        "ConditionResult": "yes",
        "AssertResult": "yes",
        "InvocationID": invocation,
        "Type": prop("Service", "Type", "simple") or "simple",
        "Restart": prop("Service", "Restart", "no") or "no",
        "ExecStart": prop("Service", "ExecStart"),
        "ExecStop": prop("Service", "ExecStop"),
        "ExecReload": prop("Service", "ExecReload"),
        "PIDFile": prop("Service", "PIDFile"),
        "User": prop("Service", "User"),
        "Group": prop("Service", "Group"),
        "WorkingDirectory": prop("Service", "WorkingDirectory"),
        "Environment": [
            item for item in shlex.split(prop("Service", "Environment")) if "=" in item
        ],
        "StandardOutput": prop("Service", "StandardOutput", "journal") or "journal",
        "StandardError": prop("Service", "StandardError", "inherit") or "inherit",
        "RemainAfterExit": "yes" if unit_remain_after_exit(unit) else "no",
        "GuessMainPID": "yes",
        "KillMode": prop("Service", "KillMode", "control-group") or "control-group",
        "KillSignal": prop("Service", "KillSignal", "SIGTERM") or "SIGTERM",
        "TimeoutStartUSec": 90_000_000,
        "TimeoutStopUSec": 90_000_000,
        "RuntimeMaxUSec": 0,
        "WatchdogUSec": 0,
        "Delegate": "no",
        "PermissionsStartOnly": "no",
        "RootDirectoryStartOnly": "no",
        "SendSIGHUP": "no",
        "SendSIGKILL": "yes",
    }
    return props


def _quote_supervisor_value(value: str) -> str:
    value = str(value).replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{value}"'


def _expand_specifiers(command: str, unit: str) -> str:
    base = unit_base(unit)
    instance = ""
    if "@" in base:
        _, instance = base.split("@", 1)
    replacements = {
        "%%": "%",
        "%n": unit,
        "%N": base,
        "%p": base.split("@", 1)[0],
        "%P": base.split("@", 1)[0],
        "%i": instance,
        "%I": instance,
        "%u": DESKTOP_USER,
        "%U": str(DESKTOP_UID),
        "%h": str(DESKTOP_HOME),
        "%t": str(DESKTOP_RUNTIME_DIR) if is_user_unit(unit) else "/run",
    }
    for key, value in replacements.items():
        command = command.replace(key, value)
    return command


def _build_dynamic_command(unit: str) -> tuple[str, dict[str, str], str, str]:
    parser = unit_config(unit)
    exec_start = parser.get("Service", "ExecStart", fallback="").strip()
    if not exec_start:
        raise ValueError(f"{unit} has no ExecStart=")
    # Real units can have multiple ExecStart= lines.  ConfigParser keeps the
    # last one; that is acceptable for the compatibility layer.
    lines = [line.strip() for line in exec_start.splitlines() if line.strip()]
    command = lines[-1] if lines else exec_start
    while command and command[0] in ("-", "@", "+", "!"):
        command = command[1:].lstrip()
    command = _expand_specifiers(command, unit)

    env: dict[str, str] = {}
    # systemd >= 250 expands specifiers in Environment= values and the units
    # OpenClaw generates escape literal percent signs as %%, so the values pass
    # through the same expansion the command line does.
    for raw in section_values(unit, "Service", "Environment"):
        for item in shlex.split(raw):
            if "=" in item:
                key, value = item.split("=", 1)
                env[key] = _expand_specifiers(value, unit)
    for raw in section_values(unit, "Service", "EnvironmentFile"):
        for token in shlex.split(raw):
            token = token.lstrip("-")
            if not token:
                continue
            candidate = Path(_expand_specifiers(token, unit))
            if not candidate.is_absolute():
                candidate = Path("/") / candidate
            if candidate.is_file():
                for line in read_text(candidate).splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    env[key.strip()] = value.strip().strip('"').strip("'")

    pre = parser.get("Service", "ExecStartPre", fallback="").strip()
    if pre:
        pre_lines: list[str] = []
        for raw in pre.splitlines():
            line = raw.strip()
            while line and line[0] in ("-", "@", "+", "!"):
                line = line[1:].lstrip()
            line = _expand_specifiers(line, unit)
            if line:
                pre_lines.append(line)
        command = " && ".join(pre_lines + [f"exec {command}"])

    user = parser.get("Service", "User", fallback="").strip()
    working_dir = parser.get("Service", "WorkingDirectory", fallback="").strip()
    if working_dir.startswith("-"):
        working_dir = working_dir[1:].lstrip()
    if working_dir:
        working_dir = _expand_specifiers(working_dir, unit)
    if is_user_unit(unit):
        # A systemd user unit inherits the user manager's environment (HOME,
        # the runtime directory and the session bus) and has no User= of its
        # own.  Supervisor launches the process directly, so those have to be
        # supplied explicitly.
        user = user or DESKTOP_USER
        env.setdefault("HOME", str(DESKTOP_HOME))
        env.setdefault("USER", DESKTOP_USER)
        env.setdefault("LOGNAME", DESKTOP_USER)
        env.setdefault("XDG_RUNTIME_DIR", str(DESKTOP_RUNTIME_DIR))
        env.setdefault(
            "DBUS_SESSION_BUS_ADDRESS", f"unix:path={DESKTOP_RUNTIME_DIR}/bus"
        )
    if working_dir:
        directory = Path(working_dir)
        if (
            directory.is_absolute()
            and not directory.is_dir()
            and directory.is_relative_to(DESKTOP_HOME)
        ):
            try:
                directory.mkdir(parents=True, exist_ok=True)
                shutil.chown(directory, DESKTOP_USER, DESKTOP_USER)
            except OSError:
                pass
        if not directory.is_dir():
            # Supervisor refuses to start a program whose directory= is
            # missing; a real user manager would have created it.
            working_dir = ""
    # Supervisor performs %(...)s-style interpolation in command= values.
    # A literal percent sign must be doubled before the command is written.
    command = command.replace("%", "%%")
    return command, env, user, working_dir


def ensure_dynamic_program(
    unit: str, autostart: bool | None = None, apply: bool = True
) -> tuple[bool, str]:
    """Create/update a Supervisor program for an arbitrary unit file."""
    unit = normalize_unit(unit)
    if unit in CORE_UNITS:
        return True, "core unit"
    path = unit_path(unit)
    if path is None:
        return False, f"Unit {unit} not found."
    if is_masked(unit):
        return False, f"Unit {unit} is masked."
    parser = unit_config(unit)
    program = program_for(unit)
    if autostart is None:
        autostart = unit_enabled_state(unit) == "enabled"
    try:
        command, env, user, working_dir = _build_dynamic_command(unit)
    except ValueError as exc:
        return False, str(exc)

    service_type = parser.get("Service", "Type", fallback="simple").strip().lower()
    restart = parser.get("Service", "Restart", fallback="no").strip().lower()
    if restart in ("always", "yes"):
        autorestart = "true"
    elif restart in ("on-failure", "on-abnormal", "on-abort"):
        autorestart = "unexpected"
    else:
        autorestart = "false"
    startsecs = "0" if service_type == "oneshot" else "1"

    lines = [
        "# Generated by systemd-compat.  Do not edit by hand.",
        f"[program:{program}]",
        f"command=/bin/bash -c {shlex.quote(command)}",
        f"autostart={'true' if autostart else 'false'}",
        f"autorestart={autorestart}",
        f"startsecs={startsecs}",
        "startretries=3",
        "stopsignal=TERM",
        "stopwaitsecs=10",
        "stopasgroup=true",
        "killasgroup=true",
        f"stdout_logfile={LOG_DIR / (program + '.log')}",
        f"stderr_logfile={LOG_DIR / (program + '.err.log')}",
        "stdout_logfile_maxbytes=10MB",
        "stderr_logfile_maxbytes=10MB",
        f"priority=100",
    ]
    if user:
        lines.append(f"user={user}")
    if working_dir:
        lines.append(f"directory={working_dir}")
    if env:
        env_value = ",".join(
            f"{key}={_quote_supervisor_value(value)}" for key, value in env.items()
        )
        lines.append(f"environment={env_value}")

    SUPERVISOR_CONF_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    config_path = SUPERVISOR_CONF_DIR / f"{program}.conf"
    new_content = "\n".join(lines) + "\n"
    old_content = read_text(config_path)
    changed = old_content != new_content
    if changed:
        config_path.write_text(new_content, encoding="utf-8")
    if not apply:
        return True, ""
    if changed or program not in supervisor_status():
        rc, out, err = supervisorctl("reread")
        if rc != 0:
            return False, (err or out or "supervisorctl reread failed")
        rc, out, err = supervisorctl("update")
        invalidate_status_cache()
        if rc != 0:
            return False, (err or out or "supervisorctl update failed")
    return True, ""


def start_unit(unit: str) -> tuple[bool, str]:
    unit = normalize_unit(unit)
    if unit == "default.target":
        unit = get_default_target()
    if unit in TARGET_UNITS:
        for dep in target_dependencies(unit):
            ok, message = start_unit(dep)
            if not ok:
                return False, message
        return True, ""
    if is_masked(unit):
        return False, f"Unit {unit} is masked."
    if not unit_exists(unit):
        return False, f"Unit {unit} not found."
    active, _ = unit_active_state(unit)
    if active == "active":
        return True, ""
    if unit not in CORE_UNITS:
        ok, message = ensure_dynamic_program(unit)
        if not ok:
            return False, message
    rc, out, err = supervisorctl("start", program_for(unit))
    invalidate_status_cache()
    if rc != 0 and "already started" not in (out + err).lower():
        return False, f"Failed to start {unit}: {err or out}"
    return True, ""


def stop_unit(unit: str) -> tuple[bool, str]:
    unit = normalize_unit(unit)
    if unit == "default.target":
        unit = get_default_target()
    if unit in TARGET_UNITS:
        for dep in reversed(target_dependencies(unit)):
            ok, message = stop_unit(dep)
            if not ok:
                return False, message
        return True, ""
    if not unit_exists(unit):
        return False, f"Unit {unit} not found."
    active, _ = unit_active_state(unit)
    if active == "inactive":
        return True, ""
    rc, out, err = supervisorctl("stop", program_for(unit))
    invalidate_status_cache()
    if rc != 0 and "not running" not in (out + err).lower():
        return False, f"Failed to stop {unit}: {err or out}"
    return True, ""


def restart_unit(unit: str) -> tuple[bool, str]:
    unit = normalize_unit(unit)
    if unit in TARGET_UNITS:
        ok, message = stop_unit(unit)
        if not ok:
            return False, message
        return start_unit(unit)
    if not unit_exists(unit):
        return False, f"Unit {unit} not found."
    if unit not in CORE_UNITS:
        ok, message = ensure_dynamic_program(unit)
        if not ok:
            return False, message
    active, _ = unit_active_state(unit)
    if active == "inactive":
        return start_unit(unit)
    rc, out, err = supervisorctl("restart", program_for(unit))
    invalidate_status_cache()
    if rc != 0:
        return False, f"Failed to restart {unit}: {err or out}"
    return True, ""


def reload_unit(unit: str) -> tuple[bool, str]:
    unit = normalize_unit(unit)
    if not unit_exists(unit):
        return False, f"Unit {unit} not found."
    # Supervisor does not expose an arbitrary reload hook.  Restarting is a
    # safe fallback and matches the observable behaviour of most services.
    return restart_unit(unit)


def enable_unit(unit: str) -> tuple[bool, str]:
    unit = normalize_unit(unit)
    if is_masked(unit):
        return False, f"Unit {unit} is masked."
    path = unit_path(unit)
    if path is None:
        return False, f"Unit {unit} not found."
    state = unit_enabled_state(unit)
    if state == "static":
        return True, "static"
    parser = unit_config(unit)
    wanted = (
        parser.get("Install", "WantedBy", fallback="")
        + " "
        + parser.get("Install", "RequiredBy", fallback="")
    ).split()
    if not wanted:
        return True, "static"
    for target in wanted:
        sub = "requires" if target in parser.get("Install", "RequiredBy", fallback="").split() else "wants"
        target_dir = Path("/etc/systemd/system") / f"{target}.{sub}"
        target_dir.mkdir(parents=True, exist_ok=True)
        link = target_dir / unit
        if not os.path.lexists(link):
            link.symlink_to(path)
    if unit not in CORE_UNITS:
        ok, message = ensure_dynamic_program(unit, autostart=True, apply=False)
        if not ok:
            return False, message
    return True, ""


def disable_unit(unit: str) -> tuple[bool, str]:
    unit = normalize_unit(unit)
    path = unit_path(unit)
    if path is None:
        return False, f"Unit {unit} not found."
    for base in (Path("/etc/systemd/system"), Path("/run/systemd/system")):
        for target_dir in base.glob("*.wants"):
            link = target_dir / unit
            if os.path.lexists(link):
                try:
                    link.unlink()
                except OSError:
                    pass
        for target_dir in base.glob("*.requires"):
            link = target_dir / unit
            if os.path.lexists(link):
                try:
                    link.unlink()
                except OSError:
                    pass
    if unit not in CORE_UNITS:
        ok, message = ensure_dynamic_program(unit, autostart=False, apply=False)
        if not ok:
            return False, message
    return True, ""


def mask_unit(unit: str) -> tuple[bool, str]:
    unit = normalize_unit(unit)
    path = Path("/etc/systemd/system") / unit
    if path.is_symlink() and os.readlink(path) == "/dev/null":
        return True, ""
    if path.exists() or path.is_symlink():
        backup = path.with_name(path.name + ".masked")
        if backup.exists() or backup.is_symlink():
            backup.unlink()
        path.rename(backup)
    path.symlink_to("/dev/null")
    return True, ""


def unmask_unit(unit: str) -> tuple[bool, str]:
    unit = normalize_unit(unit)
    path = Path("/etc/systemd/system") / unit
    backup = path.with_name(path.name + ".masked")
    if path.is_symlink() and os.readlink(path) == "/dev/null":
        path.unlink()
    if backup.exists() or backup.is_symlink():
        backup.rename(path)
    return True, ""


def kill_unit(unit: str, signal_name: str = "SIGTERM") -> tuple[bool, str]:
    unit = normalize_unit(unit)
    pid = unit_pid(unit)
    if not pid:
        return False, f"Unit {unit} has no main process."
    import signal as signal_module

    name = signal_name.upper()
    if not name.startswith("SIG"):
        name = "SIG" + name
    try:
        signum = int(getattr(signal_module, name))
    except (AttributeError, ValueError):
        return False, f"Unknown signal {signal_name}."
    try:
        os.kill(pid, signum)
    except OSError as exc:
        return False, str(exc)
    return True, ""


def system_state() -> str:
    statuses = supervisor_status()
    if not statuses:
        return "offline"
    core_states = [statuses.get(program, {}).get("state") for program in CORE_PROGRAMS]
    if any(state == "FATAL" for state in core_states):
        return "degraded"
    if any(state in ("STARTING", "BACKOFF") for state in core_states):
        return "starting"
    if all(state == "RUNNING" for state in core_states):
        return "running"
    return "degraded"


def count_failed_units() -> int:
    count = 0
    for unit in list_all_units():
        if unit in TARGET_UNITS:
            continue
        active, _ = unit_active_state(unit)
        if active == "failed":
            count += 1
    return count


def systemd_version_string() -> str:
    return (
        f"systemd {SYSTEMD_VERSION} ({SYSTEMD_PACKAGE_VERSION})\n"
        "+PAM +AUDIT +SELINUX +APPARMOR +IMA +SMACK +SECCOMP +GCRYPT +GNUTLS "
        "+OPENSSL +ACL +BLKID +CURL +ELFUTILS +FIDO2 +IDN2 +IPTC +KMOD "
        "+LIBCRYPTSETUP +LIBFDISK +PCRE2 +PWQUALITY +P11KIT +QRENCODE +TPM2 "
        "+BZIP2 +LZ4 +XZ +ZLIB +ZSTD +BPF_FRAMEWORK -XKBCOMMON +UTMP +SYSVINIT "
        "default-hierarchy=unified"
    )

