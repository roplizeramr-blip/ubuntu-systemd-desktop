#!/usr/bin/env python3
"""systemctl compatibility shim backed by Supervisor.

This is intentionally a *facade*: it implements the observable systemctl
interface, unit-file discovery and Supervisor process control.  It is not a
replacement for the systemd PID 1.
"""
from __future__ import annotations

import os
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, "/opt/systemd-compat")

import systemd_compat as sc  # noqa: E402


HELP = """systemctl [OPTIONS...] COMMAND [UNIT...]

Unit Commands:
  start UNIT...             Start (activate) one or more units
  stop UNIT...              Stop (deactivate) one or more units
  restart UNIT...           Start or restart one or more units
  reload UNIT...            Reload one or more units (restart fallback)
  status [UNIT...]          Show runtime status of one or more units
  is-active UNIT...         Check whether units are active
  is-failed UNIT...         Check whether units are failed
  is-enabled UNIT...        Check whether unit files are enabled
  enable UNIT...            Enable one or more unit files
  disable UNIT...           Disable one or more unit files
  mask UNIT...              Mask one or more units
  unmask UNIT...            Unmask one or more units
  show [UNIT...]            Show properties of one or more units
  cat UNIT...               Show unit file contents
  list-units [PATTERN...]   List loaded units
  list-unit-files           List installed unit files
  list-dependencies [UNIT]  Show unit dependency tree

System Commands:
  is-system-running         Check whether the system is fully operational
  daemon-reload             Reload Supervisor configuration
  get-default               Show the default target
  set-default TARGET        Set the default target
  isolate TARGET            Start a target and its dependencies
  kill UNIT...              Send a signal to a unit's main process
  reset-failed [UNIT...]    Reset failed state of units
  --version                 Show compatibility version

This compatibility layer is powered by Supervisor.  See README.md for
limitations.
"""


def out(message: str = "") -> None:
    print(message)


def err(message: str) -> None:
    print(message, file=sys.stderr)


def parse_argv(argv: list[str]) -> tuple[str, list[str], dict[str, object]]:
    opts: dict[str, object] = {
        "no_pager": False,
        "no_legend": False,
        "all": False,
        "quiet": False,
        "value": False,
        "now": False,
        "force": False,
        "runtime": False,
        "full": False,
        "failed": False,
        "plain": False,
        "type": None,
        "state": None,
        "lines": 10,
        "property": None,
        "signal": "SIGTERM",
        "kill_whom": "all",
        "wait": False,
        "user": False,
    }
    positionals: list[str] = []
    value_options = {
        "--type": "type",
        "-t": "type",
        "--state": "state",
        "--lines": "lines",
        "-n": "lines",
        "--property": "property",
        "-p": "property",
        "--signal": "signal",
        "-s": "signal",
        "--kill-whom": "kill_whom",
        "--output": "output",
        "-o": "output",
        "--job-mode": "job_mode",
        "--root": "root",
        "--host": "host",
        "-H": "host",
        "--machine": "machine",
        "-M": "machine",
    }
    flag_options = {
        "--no-pager": "no_pager",
        "--no-legend": "no_legend",
        "--all": "all",
        "-a": "all",
        "--quiet": "quiet",
        "-q": "quiet",
        "--value": "value",
        "--now": "now",
        "--force": "force",
        "-f": "force",
        "--runtime": "runtime",
        "--full": "full",
        "-l": "full",
        "--failed": "failed",
        "--plain": "plain",
        "--wait": "wait",
        "--user": "user",
        "--system": None,
        "--no-block": None,
        "--no-wall": None,
        "--no-reload": None,
        "--no-ask-password": None,
        "--show-types": None,
        "--legend": None,
    }
    i = 0
    while i < len(argv):
        token = argv[i]
        if token in value_options:
            key = value_options[token]
            if i + 1 < len(argv):
                value = argv[i + 1]
                if key in ("lines",):
                    try:
                        value = int(value)
                    except ValueError:
                        value = 10
                if key == "property" and opts.get("property"):
                    # systemctl accepts repeated -p/--property flags and shows
                    # every requested property; fold them into one list.
                    value = f"{opts['property']},{value}"
                opts[key] = value
                i += 2
                continue
            i += 1
            continue
        if token.startswith("--") and "=" in token:
            key_text, value = token.split("=", 1)
            if key_text in value_options:
                key = value_options[key_text]
                if key == "lines":
                    try:
                        value = int(value)
                    except ValueError:
                        value = 10
                if key == "property" and opts.get("property"):
                    value = f"{opts['property']},{value}"
                opts[key] = value
                i += 1
                continue
        if token in flag_options:
            key = flag_options[token]
            if key:
                opts[key] = True
            i += 1
            continue
        if token in ("--help", "-h", "--version"):
            positionals.append(token)
            i += 1
            continue
        if token.startswith("-"):
            # Unknown switches are ignored for forward compatibility.
            i += 1
            continue
        positionals.append(token)
        i += 1

    if not positionals:
        return "list-units", [], opts
    command = positionals[0]
    units = [u for u in positionals[1:] if not u.startswith("-")]
    return command, units, opts


def format_bytes(value: int) -> str:
    amount = float(max(0, value))
    for suffix in ("B", "K", "M", "G", "T"):
        if amount < 1024.0 or suffix == "T":
            if suffix == "B":
                return f"{int(amount)}B"
            return f"{amount:.1f}{suffix}"
        amount /= 1024.0
    return f"{int(value)}B"


def format_timestamp(usec: int) -> str:
    if not usec:
        return ""
    try:
        return time.strftime(
            "%a %Y-%m-%d %H:%M:%S %Z", time.localtime(usec / 1_000_000)
        )
    except (OverflowError, OSError, ValueError):
        return ""


def age_string(usec: int) -> str:
    if not usec:
        return ""
    seconds = max(0, int(time.time() - usec / 1_000_000))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}min {seconds}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}min"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def unit_matches_type(unit: str, wanted: str | None) -> bool:
    if not wanted:
        return True
    wanted = str(wanted).lower()
    if wanted in ("service", "services"):
        return unit.endswith(".service")
    if wanted in ("target", "targets"):
        return unit.endswith(".target")
    if wanted in ("socket", "sockets"):
        return unit.endswith(".socket")
    if wanted in ("timer", "timers"):
        return unit.endswith(".timer")
    if wanted in ("path", "paths"):
        return unit.endswith(".path")
    if wanted in ("mount", "mounts"):
        return unit.endswith(".mount")
    return True


def state_matches(unit: str, wanted: str | None) -> bool:
    if not wanted:
        return True
    allowed = {item.strip() for item in str(wanted).split(",") if item.strip()}
    if not allowed:
        return True
    active, sub = sc.unit_active_state(unit)
    return active in allowed or sub in allowed


def print_list_units(units: list[str], opts: dict[str, object]) -> int:
    rows: list[tuple[str, str, str, str, str]] = []
    candidates = sc.list_all_units()
    if units:
        patterns = [item for item in units if not item.startswith("-")]
        candidates = [
            unit for unit in candidates if any(pattern in unit for pattern in patterns)
        ]
    for unit in candidates:
        if not unit_matches_type(unit, opts.get("type")):  # type: ignore[arg-type]
            continue
        if opts.get("failed") and sc.unit_active_state(unit)[0] != "failed":
            continue
        active, sub = sc.unit_active_state(unit)
        if not opts.get("all") and active == "inactive":
            continue
        if not state_matches(unit, opts.get("state")):  # type: ignore[arg-type]
            continue
        load = "masked" if sc.is_masked(unit) else ("loaded" if sc.unit_exists(unit) else "not-found")
        rows.append((unit, load, active, sub, sc.unit_description(unit)))
    if not opts.get("no_legend"):
        out(f"{'UNIT':<42} {'LOAD':<10} {'ACTIVE':<12} {'SUB':<14} DESCRIPTION")
    for unit, load, active, sub, description in rows:
        out(f"{unit:<42} {load:<10} {active:<12} {sub:<14} {description}")
    if not opts.get("no_legend"):
        out("")
        out(f"{len(rows)} loaded units listed.")
    return 0


def print_list_unit_files(units: list[str], opts: dict[str, object]) -> int:
    rows = sc.list_unit_files()
    filtered: list[tuple[str, str]] = []
    patterns = [item for item in units if not item.startswith("-")]
    for unit, state in rows:
        if patterns and not any(pattern in unit for pattern in patterns):
            continue
        if not unit_matches_type(unit, opts.get("type")):
            continue
        if opts.get("state") and state not in str(opts.get("state")).split(","):
            continue
        filtered.append((unit, state))
    if not opts.get("no_legend"):
        out(f"{'UNIT FILE':<48} {'STATE':<12} PRESET")
    for unit, state in filtered:
        out(f"{unit:<48} {state:<12} enabled")
    if not opts.get("no_legend"):
        out("")
        out(f"{len(filtered)} unit files listed.")
    return 0


def print_system_status(opts: dict[str, object]) -> int:
    state = sc.system_state()
    units = sc.list_all_units()
    services = [u for u in units if u.endswith(".service")]
    active = sum(1 for u in services if sc.unit_active_state(u)[0] == "active")
    failed = sum(1 for u in services if sc.unit_active_state(u)[0] == "failed")
    out("● ubuntu-desktop")
    out(f"    State: {state}")
    out(f"     Units: {len(services)} loaded ({active} active, {failed} failed)")
    out(f"   Default: {sc.get_default_target()}")
    hostname = Path("/etc/hostname")
    if hostname.is_file():
        out(f"  Hostname: {hostname.read_text(encoding='utf-8').strip()}")
    out("")
    for unit in ("graphical.target", "multi-user.target"):
        active_state, sub = sc.unit_active_state(unit)
        out(f"{unit:<24} {active_state:<12} {sub}")
    if not opts.get("no_legend"):
        out("")
        out("Use 'systemctl status UNIT' for details about a unit.")
    return 0 if state == "running" else 1


def print_unit_status(unit: str, opts: dict[str, object]) -> int:
    unit = sc.normalize_unit(unit)
    props = sc.get_properties(unit)
    active = props["ActiveState"]
    sub = props["SubState"]
    marker = "●" if active in ("active", "activating") else ("×" if active == "failed" else "○")
    out(f"{marker} {unit} - {props['Description']}")
    load_state = props["LoadState"]
    fragment = props["FragmentPath"]
    state = props["UnitFileState"]
    if load_state == "masked":
        out(f"     Loaded: masked (Reason: Unit {unit} is masked.)")
    elif load_state == "not-found":
        out(f"     Loaded: not-found (Reason: Unit {unit} not found.)")
    else:
        out(f"     Loaded: loaded ({fragment}; {state}; preset: enabled)")
    if active == "active":
        since = format_timestamp(int(props["ActiveEnterTimestamp"]))
        age = age_string(int(props["ActiveEnterTimestamp"]))
        suffix = f" since {since}; {age} ago" if since else ""
        out(f"     Active: active ({sub}){suffix}")
    else:
        out(f"     Active: {active} ({sub})")
    pid = int(props.get("MainPID") or 0)
    if pid:
        try:
            comm = Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()
        except OSError:
            comm = "?"
        out(f"   Main PID: {pid} ({comm})")
        out(f"      Tasks: {int(props.get('TasksCurrent') or 0)}")
        out(f"     Memory: {format_bytes(int(props.get('MemoryCurrent') or 0))}")
    out(f"     CGroup: /system.slice/{unit}")
    docs = props.get("Documentation") or []
    if docs:
        out(f"       Docs: {' '.join(docs)}")
    lines = int(opts.get("lines") or 0)
    if lines > 0 and props["LoadState"] == "loaded":
        log_lines: list[str] = []
        program = sc.program_for(unit)
        for suffix in (".log", ".err.log"):
            path = sc.LOG_DIR / f"{program}{suffix}"
            if path.is_file():
                try:
                    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
                    log_lines.extend(content[-lines:])
                except OSError:
                    pass
        if log_lines:
            out("")
            for line in log_lines[-lines:]:
                out(f"    {line}")
    return 0 if active == "active" else 3


def cmd_start_stop(units: list[str], action: str) -> int:
    if not units:
        err(f"Too few arguments for {action}.")
        return 1
    result = 0
    for unit in units:
        unit = sc.normalize_unit(unit)
        if action == "start":
            ok, message = sc.start_unit(unit)
        elif action == "stop":
            ok, message = sc.stop_unit(unit)
        elif action == "restart":
            ok, message = sc.restart_unit(unit)
        elif action in ("reload", "force-reload"):
            ok, message = sc.reload_unit(unit)
        elif action == "try-restart":
            active, _ = sc.unit_active_state(unit)
            if active != "active":
                continue
            ok, message = sc.restart_unit(unit)
        elif action == "reload-or-restart":
            ok, message = sc.reload_unit(unit)
        else:
            ok, message = False, f"Unsupported action {action}."
        if not ok:
            err(f"Failed to {action} {unit}: {message}")
            result = 1
    return result


def cmd_is_active(units: list[str], opts: dict[str, object]) -> int:
    if not units:
        err("Too few arguments for is-active.")
        return 1
    result = 0
    for unit in units:
        unit = sc.normalize_unit(unit)
        active, _ = sc.unit_active_state(unit)
        if opts.get("quiet"):
            pass
        elif opts.get("value"):
            out(active)
        else:
            out(active)
        if active != "active":
            result = 3
    return result


def cmd_is_failed(units: list[str], opts: dict[str, object]) -> int:
    if not units:
        err("Too few arguments for is-failed.")
        return 1
    result = 1
    for unit in units:
        active, _ = sc.unit_active_state(sc.normalize_unit(unit))
        if not opts.get("quiet"):
            out(active)
        if active == "failed":
            result = 0
    return result


def cmd_is_enabled(units: list[str], opts: dict[str, object]) -> int:
    if not units:
        err("Too few arguments for is-enabled.")
        return 1
    result = 0
    for unit in units:
        unit = sc.normalize_unit(unit)
        state = sc.unit_enabled_state(unit)
        if not opts.get("quiet"):
            out(state)
        if state not in ("enabled", "static"):
            result = 1
    return result


def cmd_enable_disable(units: list[str], action: str, opts: dict[str, object]) -> int:
    if not units:
        err(f"Too few arguments for {action}.")
        return 1
    result = 0
    for unit in units:
        unit = sc.normalize_unit(unit)
        if action == "enable":
            ok, message = sc.enable_unit(unit)
            if ok and message != "static":
                out(f"Enabled unit {unit}.")
            elif message == "static":
                out(f"Unit {unit} is static; no enablement required.")
        elif action == "disable":
            ok, message = sc.disable_unit(unit)
            if ok:
                out(f"Disabled unit {unit}.")
        elif action == "mask":
            ok, message = sc.mask_unit(unit)
            if ok:
                out(f"Masked unit {unit}.")
        elif action == "unmask":
            ok, message = sc.unmask_unit(unit)
            if ok:
                out(f"Unmasked unit {unit}.")
        else:
            ok, message = False, f"Unsupported action {action}."
        if not ok:
            err(f"Failed to {action} {unit}: {message}")
            result = 1
            continue
        if opts.get("now") and action == "enable":
            started, start_message = sc.start_unit(unit)
            if not started:
                err(f"Failed to start {unit}: {start_message}")
                result = 1
        if opts.get("now") and action == "disable":
            stopped, stop_message = sc.stop_unit(unit)
            if not stopped:
                err(f"Failed to stop {unit}: {stop_message}")
                result = 1
    return result


def manager_properties() -> dict[str, object]:
    """The manager properties `systemctl show` reports when no unit is given.

    `systemctl show` without a unit argument inspects the *manager*, not a unit.
    Tools rely on this to discover the unit load path (UnitPath), so the value
    must be the manager property set rather than a target's.
    """
    machine = platform.machine() or "x86_64"
    architecture = {"x86_64": "x86-64", "amd64": "x86-64"}.get(machine, machine)
    hostname_file = Path("/etc/hostname")
    hostname = platform.node()
    if hostname_file.exists():
        hostname = sc.read_text(hostname_file).strip() or hostname
    return {
        "Version": sc.SYSTEMD_VERSION,
        "Architecture": architecture,
        "NFailedUnits": sc.count_failed_units(),
        "NJobs": 0,
        "NInstalledJobs": 0,
        "NNames": len(sc.list_all_units()),
        "UnitPath": [str(path) for path in sc.UNIT_DIRS],
        "Virtualization": os.environ.get("SYSTEMD_DETECT_VIRT", "none"),
        "SystemState": sc.system_state(),
        "Tainted": "",
        "DefaultStandardOutput": "journal",
        "DefaultStandardError": "inherit",
        "LogLevel": "info",
        "LogTarget": "journal",
        "Hostname": hostname,
        "StaticHostname": hostname,
        "KernelRelease": platform.release(),
        "KernelVersion": platform.version(),
        "DefaultTimeoutStartUSec": 90_000_000,
        "DefaultTimeoutStopUSec": 90_000_000,
        "DefaultRestartUSec": 100_000,
        "DefaultStartLimitIntervalUSec": 10_000_000,
        "DefaultStartLimitBurst": 5,
        "TimerSlackNSec": 50_000,
        "DefaultOOMPolicy": "stop",
    }


def select_properties(
    props: dict[str, object], opts: dict[str, object]
) -> dict[str, object]:
    prop_filter = opts.get("property")
    if prop_filter and str(prop_filter) not in ("All", "all"):
        names = [name.strip() for name in str(prop_filter).split(",") if name.strip()]
        return {name: props.get(name, "") for name in names}
    return props


def emit_properties(props: dict[str, object], opts: dict[str, object]) -> None:
    for key in sorted(props):
        value = props[key]
        if isinstance(value, list):
            text = " ".join(str(item) for item in value)
        elif key == "ActiveEnterTimestamp" and value:
            text = format_timestamp(int(value))
        elif isinstance(value, bool):
            text = "yes" if value else "no"
        else:
            text = str(value)
        if opts.get("value"):
            out(text)
        else:
            out(f"{key}={text}")


def cmd_show(units: list[str], opts: dict[str, object]) -> int:
    if not units:
        emit_properties(select_properties(manager_properties(), opts), opts)
        return 0
    result = 0
    for index, unit in enumerate(units):
        unit = sc.normalize_unit(unit)
        if not sc.unit_exists(unit):
            err(f"Unit {unit} could not be found.")
            result = 1
            continue
        props = sc.get_properties(unit)
        selected = select_properties(props, opts)
        if index and not opts.get("value"):
            out("")
        emit_properties(selected, opts)
    return result


def cmd_cat(units: list[str]) -> int:
    if not units:
        err("Too few arguments for cat.")
        return 1
    result = 0
    for unit in units:
        unit = sc.normalize_unit(unit)
        path = sc.unit_path(unit)
        if path is None:
            err(f"No files found for {unit}.")
            result = 1
            continue
        if sc.is_masked(unit):
            out(f"# {unit} is masked to /dev/null")
            continue
        out(f"# {path}")
        out(sc.read_text(path).rstrip())
    return result


def cmd_list_dependencies(unit: str) -> int:
    unit = sc.normalize_unit(unit)
    seen: set[str] = set()

    def walk(item: str, depth: int) -> None:
        if item in seen:
            return
        seen.add(item)
        active, sub = sc.unit_active_state(item)
        out(f"{'  ' * depth}{item} ({active}/{sub})")
        for dep in sc.target_dependencies(item):
            walk(dep, depth + 1)

    walk(unit, 0)
    return 0


def cmd_daemon_reload() -> int:
    rc, output, error = sc.supervisorctl("reread")
    if rc != 0:
        err(f"Failed to reload daemon: {error or output}")
        return 1
    rc, output, error = sc.supervisorctl("update")
    sc.invalidate_status_cache()
    if rc != 0:
        err(f"Failed to reload daemon: {error or output}")
        return 1
    return 0


def cmd_reset_failed(units: list[str]) -> int:
    targets = units or [
        unit
        for unit in sc.list_all_units()
        if sc.unit_active_state(unit)[0] == "failed"
    ]
    result = 0
    for unit in targets:
        unit = sc.normalize_unit(unit)
        rc, output, error = sc.supervisorctl("clear", sc.program_for(unit))
        if rc != 0:
            err(f"Failed to reset failed state of {unit}: {error or output}")
            result = 1
    return result


def cmd_kill(units: list[str], opts: dict[str, object]) -> int:
    if not units:
        err("Too few arguments for kill.")
        return 1
    result = 0
    signal_name = str(opts.get("signal") or "SIGTERM")
    for unit in units:
        unit = sc.normalize_unit(unit)
        ok, message = sc.kill_unit(unit, signal_name)
        if not ok:
            err(f"Failed to kill {unit}: {message}")
            result = 1
    return result


def main(argv: list[str]) -> int:
    if "--version" in argv:
        out(sc.systemd_version_string())
        return 0
    if "--help" in argv or "-h" in argv:
        out(HELP)
        return 0
    command, units, opts = parse_argv(argv)
    # A plain invocation talks to the system manager; "--user" selects the
    # per-user manager.  Keep their unit search paths separate, like real
    # systemd, so a user unit never looks like a system unit (and vice versa).
    sc.set_scope("user" if opts.get("user") else "system")
    if command in ("--version", "version"):
        out(sc.systemd_version_string())
        return 0
    if command in ("--help", "help"):
        out(HELP)
        return 0

    if command == "list-units":
        return print_list_units(units, opts)
    if command == "list-unit-files":
        return print_list_unit_files(units, opts)
    if command == "status":
        if not units:
            return print_system_status(opts)
        result = 0
        for index, unit in enumerate(units):
            if index:
                out("")
            result = max(result, print_unit_status(unit, opts))
        return result
    if command in ("start", "stop", "restart", "reload", "force-reload", "try-restart", "reload-or-restart"):
        return cmd_start_stop(units, command)
    if command == "is-active":
        return cmd_is_active(units, opts)
    if command == "is-failed":
        return cmd_is_failed(units, opts)
    if command == "is-enabled":
        return cmd_is_enabled(units, opts)
    if command == "is-system-running":
        state = sc.system_state()
        if opts.get("wait"):
            deadline = time.time() + 60
            while state != "running" and time.time() < deadline:
                time.sleep(0.5)
                state = sc.system_state()
        out(state)
        return 0 if state == "running" else 1
    if command in ("enable", "disable", "mask", "unmask"):
        return cmd_enable_disable(units, command, opts)
    if command == "show":
        return cmd_show(units, opts)
    if command == "cat":
        return cmd_cat(units)
    if command == "daemon-reload":
        return cmd_daemon_reload()
    if command in ("daemon-reexec", "daemon-reexecute"):
        return 0
    if command == "get-default":
        out(sc.get_default_target())
        return 0
    if command == "set-default":
        if len(units) != 1:
            err("Too few arguments for set-default.")
            return 1
        target = sc.normalize_unit(units[0])
        if target not in sc.TARGET_UNITS:
            err(f"Unit {target} is not a target.")
            return 1
        sc.set_default_target(target)
        out(f"Created symlink /etc/systemd/system/default.target → /etc/systemd/system/{target}.")
        return 0
    if command == "isolate":
        if len(units) != 1:
            err("Too few arguments for isolate.")
            return 1
        ok, message = sc.start_unit(units[0])
        if not ok:
            err(f"Failed to isolate {units[0]}: {message}")
            return 1
        return 0
    if command == "kill":
        return cmd_kill(units, opts)
    if command == "reset-failed":
        return cmd_reset_failed(units)
    if command == "list-dependencies":
        return cmd_list_dependencies(units[0] if units else "default.target")
    if command in ("list-timers", "list-sockets", "list-paths", "list-jobs", "list-machines"):
        if not opts.get("no_legend"):
            out("No units found.")
        return 0
    if command in ("reboot", "poweroff", "halt", "suspend", "hibernate", "hybrid-sleep", "kexec"):
        err(f"Failed to {command}: operation is not supported inside this container.")
        return 1
    if command in ("emergency", "rescue", "switch-root"):
        err(f"Failed to {command}: operation is not supported inside this container.")
        return 1
    if command in ("edit", "set-property", "mount", "umount", "preset", "preset-all", "revert"):
        err(f"Command '{command}' is not supported by the systemd compatibility layer.")
        return 1
    if command in ("cancel",):
        out("No jobs running.")
        return 0
    if command in ("log-level", "log-target", "service-watchdogs"):
        return 0
    err(f"Unknown command verb '{command}'.")
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        raise SystemExit(130)
