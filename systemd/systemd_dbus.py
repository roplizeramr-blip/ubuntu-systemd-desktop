#!/usr/bin/env python3
"""Minimal org.freedesktop.systemd1 D-Bus facade.

This exposes the parts of the systemd manager and unit interfaces that
desktop applications, busctl and common systemd-aware programs query.
It is backed by the Supervisor-based compatibility layer in
systemd_compat.py, not by the real systemd PID 1.
"""
from __future__ import annotations

import os
import platform
import shlex
import signal
import sys
import time

import dbus
import dbus.exceptions
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

sys.path.insert(0, "/opt/systemd-compat")

import systemd_compat as sc  # noqa: E402

SYSTEMD1 = "org.freedesktop.systemd1"
MANAGER_PATH = "/org/freedesktop/systemd1"
MANAGER_IFACE = "org.freedesktop.systemd1.Manager"
UNIT_IFACE = "org.freedesktop.systemd1.Unit"
SERVICE_IFACE = "org.freedesktop.systemd1.Service"
TARGET_IFACE = "org.freedesktop.systemd1.Target"
PROPS_IFACE = "org.freedesktop.DBus.Properties"

BOOL_KEYS = {
    "CanStart",
    "CanStop",
    "CanReload",
    "CanIsolate",
    "ConditionResult",
    "AssertResult",
    "ConfirmNo",
    "DefaultDependencies",
    "AllowIsolate",
    "IgnoreOnIsolate",
    "SurviveFinalKillSignal",
    "RemainAfterExit",
    "GuessMainPID",
    "NeedDaemonReload",
    "RefuseManualStart",
    "Delegate",
    "PermissionsStartOnly",
    "RootDirectoryStartOnly",
    "SendSIGHUP",
    "SendSIGKILL",
    "DefaultCPUAccounting",
    "DefaultBlockIOAccounting",
    "DefaultMemoryAccounting",
    "DefaultTasksAccounting",
}
UINT32_KEYS = {
    "Id",
    "MainPID",
    "ControlPID",
    "TasksCurrent",
    "NFailedUnits",
    "NJobs",
    "NInstalledJobs",
    "NNames",
    "DefaultStartLimitBurst",
}
UINT64_KEYS = {
    "MemoryCurrent",
    "CPUUsageNSec",
    "ActiveEnterTimestamp",
    "ActiveEnterTimestampMonotonic",
    "InactiveEnterTimestamp",
    "InactiveEnterTimestampMonotonic",
    "TimeoutStartUSec",
    "TimeoutStopUSec",
    "RuntimeMaxUSec",
    "WatchdogUSec",
    "RuntimeWatchdogUSec",
    "DefaultTimeoutStartUSec",
    "DefaultTimeoutStopUSec",
    "DefaultRestartUSec",
    "DefaultStartLimitIntervalUSec",
    "DefaultLimitNOFILE",
    "DefaultLimitNPROC",
    "DefaultLimitCORE",
    "DefaultLimitMEMLOCK",
    "DefaultTasksMax",
    "TimerSlackNSec",
}
OBJECT_PATH_KEYS = {"Job", "Unit"}
LIST_KEYS = {
    "Names",
    "Requires",
    "Wants",
    "Conflicts",
    "Before",
    "After",
    "Documentation",
    "WantedBy",
    "RequiredBy",
    "Alias",
    "Also",
    "UnitPath",
    "Environment",
    "DropInPaths",
    "UnsetEnvironment",
}

# systemd reports ExecStart as an array of (sasbttttuii) structs:
#   binary path, argv, ignore-failure flag, four timestamps, then three
#   integers.  Systemd-aware desktop apps decode exactly that layout, so
#   the facade must not collapse it to a plain string.
EXEC_START_ELEMENT_SIGNATURE = "sasbttttuii"


def exec_start_value(value: object) -> object:
    """Render an ExecStart= command line as systemd's (sasbttttuii) array."""
    element_signature = "(" + EXEC_START_ELEMENT_SIGNATURE + ")"
    command = str(value or "").strip()
    try:
        argv = shlex.split(command)
    except ValueError:
        argv = command.split()
    if not argv:
        return dbus.Array([], signature=element_signature)
    entry = dbus.Struct(
        (
            dbus.String(argv[0]),
            dbus.Array([dbus.String(part) for part in argv], signature="s"),
            dbus.Boolean(False),
            dbus.UInt64(0),
            dbus.UInt64(0),
            dbus.UInt64(0),
            dbus.UInt64(0),
            dbus.UInt32(0),
            dbus.Int32(0),
            dbus.Int32(0),
        ),
        signature=EXEC_START_ELEMENT_SIGNATURE,
    )
    return dbus.Array([entry], signature=element_signature)


def dbus_value(key: str, value: object) -> object:
    if key == "ExecStart":
        return exec_start_value(value)
    if key == "EnvironmentFiles":
        # systemd exposes EnvironmentFiles as a list of (path, ignore-failure)
        # pairs; the compatibility units never use them.
        return dbus.Array([], signature="(sb)")
    if key in LIST_KEYS:
        if isinstance(value, (list, tuple)):
            return dbus.Array([dbus.String(str(item)) for item in value], signature="s")
        return dbus.Array([], signature="s")
    if key in BOOL_KEYS:
        return dbus.Boolean(str(value).lower() in ("yes", "true", "1", "on"))
    if key in UINT32_KEYS:
        try:
            return dbus.UInt32(int(value))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return dbus.UInt32(0)
    if key in UINT64_KEYS:
        try:
            return dbus.UInt64(int(value))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return dbus.UInt64(0)
    if key in OBJECT_PATH_KEYS:
        text = str(value)
        return dbus.ObjectPath(text if text.startswith("/") else "/")
    return dbus.String(str(value))


def read_os_release() -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        for line in open("/etc/os-release", encoding="utf-8"):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                result[key] = value.strip('"')
    except OSError:
        pass
    return result


def read_first(path: str) -> str:
    try:
        return open(path, encoding="utf-8").read().strip()
    except OSError:
        return ""


class NoSuchUnit(dbus.exceptions.DBusException):
    _dbus_error_name = "org.freedesktop.systemd1.NoSuchUnit"


class UnitExists(dbus.exceptions.DBusException):
    _dbus_error_name = "org.freedesktop.systemd1.UnitExists"


class JobNotFound(dbus.exceptions.DBusException):
    _dbus_error_name = "org.freedesktop.systemd1.JobNotFound"


class UnitFileNotFound(dbus.exceptions.DBusException):
    _dbus_error_name = "org.freedesktop.systemd1.NoSuchUnit"


class UnknownProperty(dbus.exceptions.DBusException):
    _dbus_error_name = "org.freedesktop.DBus.Error.UnknownProperty"


class UnknownInterface(dbus.exceptions.DBusException):
    _dbus_error_name = "org.freedesktop.DBus.Error.UnknownInterface"


class PropertyReadOnly(dbus.exceptions.DBusException):
    _dbus_error_name = "org.freedesktop.DBus.Error.PropertyReadOnly"


class Manager(dbus.service.Object):
    def __init__(self, bus, path: str):
        super().__init__(bus, path)
        self._connection = bus
        self.units: dict[str, Unit] = {}
        self.jobs: dict[int, Job] = {}
        self.job_counter = 0
        self.bus_name = None
        self.start_time = time.time()
        self.environment: list[str] = []
        self.os_release = read_os_release()

    # ------------------------------------------------------------------ props
    def _props(self) -> dict[str, object]:
        return {
            "Version": sc.SYSTEMD_VERSION,
            "Architecture": "x86-64",
            "Features": "+PAM +AUDIT +SELINUX +APPARMOR +IMA +SMACK +SECCOMP "
            "+GCRYPT +GNUTLS +OPENSSL +ACL +BLKID +CURL +ELFUTILS +FIDO2 "
            "+IDN2 +IPTC +KMOD +LIBCRYPTSETUP +LIBFDISK +PCRE2 +PWQUALITY "
            "+P11KIT +QRENCODE +TPM2 +BZIP2 +LZ4 +XZ +ZLIB +ZSTD",
            "ConfirmNo": True,
            "NFailedUnits": sc.count_failed_units(),
            "NJobs": 0,
            "NInstalledJobs": 0,
            "NNames": len(sc.list_all_units()),
            "UnitPath": [str(path) for path in sc.UNIT_DIRS],
            "Environment": list(self.environment),
            "Virtualization": os.environ.get("SYSTEMD_DETECT_VIRT", "none"),
            "SystemState": sc.system_state(),
            "Tainted": "",
            "DefaultStandardOutput": "journal",
            "DefaultStandardError": "inherit",
            "LogLevel": "info",
            "LogTarget": "journal",
            "RuntimeWatchdogUSec": 0,
            "ServiceWatchdogs": "yes",
            "Hostname": platform.node(),
            "StaticHostname": read_first("/etc/hostname") or platform.node(),
            "MachineID": read_first("/etc/machine-id"),
            "BootID": read_first("/proc/sys/kernel/random/boot_id"),
            "KernelRelease": platform.release(),
            "KernelVersion": platform.version(),
            "OperatingSystemPrettyName": self.os_release.get(
                "PRETTY_NAME", "Ubuntu 24.04 LTS"
            ),
            "OperatingSystemCPEName": "cpe:2.3:o:canonical:ubuntu_linux:24.04:*:*:*:*:*:*:*",
            "HomeURL": "https://www.ubuntu.com/",
            "DefaultTimeoutStartUSec": 90_000_000,
            "DefaultTimeoutStopUSec": 90_000_000,
            "DefaultRestartUSec": 100_000,
            "DefaultStartLimitIntervalUSec": 10_000_000,
            "DefaultStartLimitBurst": 5,
            "DefaultCPUAccounting": False,
            "DefaultBlockIOAccounting": False,
            "DefaultMemoryAccounting": False,
            "DefaultTasksAccounting": False,
            "DefaultLimitNOFILE": 1024,
            "DefaultLimitNPROC": 1024,
            "DefaultLimitCORE": 0,
            "DefaultLimitMEMLOCK": 0,
            "DefaultTasksMax": 0,
            "TimerSlackNSec": 50_000,
            "DefaultOOMPolicy": "stop",
        }

    @dbus.service.method(PROPS_IFACE, in_signature="ss", out_signature="v")
    def Get(self, interface, prop):
        if interface not in (MANAGER_IFACE, PROPS_IFACE):
            raise UnknownInterface(f"Unknown interface {interface}")
        props = self._props()
        if str(prop) not in props:
            raise UnknownProperty(f"Unknown property {prop}")
        return dbus_value(str(prop), props[str(prop)])

    @dbus.service.method(PROPS_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        if interface not in (MANAGER_IFACE, PROPS_IFACE):
            raise UnknownInterface(f"Unknown interface {interface}")
        return dbus.Dictionary(
            {key: dbus_value(key, value) for key, value in self._props().items()},
            signature="sv",
        )

    @dbus.service.method(PROPS_IFACE, in_signature="ssv")
    def Set(self, interface, prop, value):
        raise PropertyReadOnly(f"Property {prop} is read-only")

    @dbus.service.signal(PROPS_IFACE, signature="sa{sv}as")
    def PropertiesChanged(self, interface, changed, invalidated):
        pass

    # ----------------------------------------------------------------- signals
    @dbus.service.signal(MANAGER_IFACE, signature="so")
    def UnitNew(self, unit, path):
        pass

    @dbus.service.signal(MANAGER_IFACE, signature="so")
    def UnitRemoved(self, unit, path):
        pass

    @dbus.service.signal(MANAGER_IFACE, signature="uos")
    def JobNew(self, job_id, job_path, unit):
        pass

    @dbus.service.signal(MANAGER_IFACE, signature="uoss")
    def JobRemoved(self, job_id, job_path, unit, result):
        pass

    @dbus.service.signal(MANAGER_IFACE, signature="b")
    def Reloading(self, reloading):
        pass

    # ----------------------------------------------------------------- helpers
    def _unit_path(self, name: str) -> dbus.ObjectPath:
        name = sc.normalize_unit(name)
        if not sc.unit_exists(name):
            raise NoSuchUnit(f"Unit {name} not found.")
        if name not in self.units:
            path = f"{MANAGER_PATH}/unit/{sc.escape_unit(name)}"
            if any(unit.path == path for unit in self.units.values()):
                path = f"{path}_{len(self.units)}"
            self.units[name] = Unit(self, name, path)
            self.UnitNew(name, dbus.ObjectPath(path))
        return dbus.ObjectPath(self.units[name].path)

    def _job_path(self, unit: str, job_type: str) -> dbus.ObjectPath:
        unit = sc.normalize_unit(unit)
        if not sc.unit_exists(unit):
            raise NoSuchUnit(f"Unit {unit} not found.")
        self.job_counter += 1
        job_id = self.job_counter
        job = Job(self, job_id, unit, job_type)
        self.jobs[job_id] = job
        path = dbus.ObjectPath(job.path)
        self.JobNew(dbus.UInt32(job_id), path, unit)
        # Complete the job after returning the job path to the caller.  This
        # mirrors systemd's asynchronous job semantics and ensures clients can
        # subscribe to JobRemoved before the job finishes.
        GLib.idle_add(self._complete_job, job_id, unit, job_type, path)
        return path

    def _complete_job(
        self, job_id: int, unit: str, job_type: str, path: dbus.ObjectPath
    ) -> bool:
        job = self.jobs.get(job_id)
        try:
            if job_type == "start":
                ok, message = sc.start_unit(unit)
            elif job_type == "stop":
                ok, message = sc.stop_unit(unit)
            elif job_type == "restart":
                ok, message = sc.restart_unit(unit)
            elif job_type == "reload":
                ok, message = sc.reload_unit(unit)
            else:
                ok, message = False, f"Unsupported job type {job_type}"
        except Exception as exc:  # keep the D-Bus service alive
            ok, message = False, str(exc)
        if job is not None:
            job.state = "done" if ok else "failed"
            job.error = message
        self.JobRemoved(
            dbus.UInt32(job_id), path, unit, "done" if ok else "failed"
        )
        return False

    # ----------------------------------------------------------------- methods
    @dbus.service.method(MANAGER_IFACE, in_signature="s", out_signature="o")
    def GetUnit(self, name):
        return self._unit_path(str(name))

    @dbus.service.method(MANAGER_IFACE, in_signature="u", out_signature="o")
    def GetUnitByPID(self, pid):
        target = int(pid)
        for unit in sc.list_all_units():
            if sc.unit_pid(unit) == target:
                return self._unit_path(unit)
        raise NoSuchUnit(f"No unit found for PID {pid}")

    @dbus.service.method(MANAGER_IFACE, in_signature="s", out_signature="o")
    def LoadUnit(self, name):
        return self._unit_path(str(name))

    @dbus.service.method(MANAGER_IFACE, in_signature="s", out_signature="s")
    def GetUnitFileState(self, name):
        return sc.unit_enabled_state(str(name))

    @dbus.service.method(MANAGER_IFACE, in_signature="ss", out_signature="o")
    def StartUnit(self, name, mode):
        return self._job_path(str(name), "start")

    @dbus.service.method(MANAGER_IFACE, in_signature="ss", out_signature="o")
    def StopUnit(self, name, mode):
        return self._job_path(str(name), "stop")

    @dbus.service.method(MANAGER_IFACE, in_signature="ss", out_signature="o")
    def RestartUnit(self, name, mode):
        return self._job_path(str(name), "restart")

    @dbus.service.method(MANAGER_IFACE, in_signature="ss", out_signature="o")
    def ReloadUnit(self, name, mode):
        return self._job_path(str(name), "reload")

    @dbus.service.method(MANAGER_IFACE, in_signature="ss", out_signature="o")
    def TryRestartUnit(self, name, mode):
        if sc.unit_active_state(str(name))[0] == "active":
            return self._job_path(str(name), "restart")
        raise UnitExists(f"Unit {name} is not active.")

    @dbus.service.method(MANAGER_IFACE, in_signature="ss", out_signature="o")
    def ReloadOrRestartUnit(self, name, mode):
        return self._job_path(str(name), "reload")

    @dbus.service.method(MANAGER_IFACE, in_signature="ss", out_signature="o")
    def ReloadOrTryRestartUnit(self, name, mode):
        return self._job_path(str(name), "reload")

    @dbus.service.method(MANAGER_IFACE, in_signature="ssi")
    def KillUnit(self, name, whom, signal_number):
        try:
            signum = int(signal_number)
            name_text = signal.Signals(signum).name
        except (ValueError, KeyError):
            name_text = "SIGTERM"
        ok, message = sc.kill_unit(str(name), name_text)
        if not ok:
            raise NoSuchUnit(message)

    @dbus.service.method(MANAGER_IFACE, in_signature="s")
    def ResetFailedUnit(self, name):
        sc.supervisorctl("clear", sc.program_for(str(name)))
        sc.invalidate_status_cache()

    @dbus.service.method(MANAGER_IFACE)
    def ResetFailed(self):
        for unit in sc.list_all_units():
            if sc.unit_active_state(unit)[0] == "failed":
                sc.supervisorctl("clear", sc.program_for(unit))
        sc.invalidate_status_cache()

    @dbus.service.method(MANAGER_IFACE, in_signature="u", out_signature="o")
    def GetJob(self, job_id):
        job = self.jobs.get(int(job_id))
        if job is None:
            raise JobNotFound(f"Job {job_id} not found.")
        return dbus.ObjectPath(job.path)

    @dbus.service.method(MANAGER_IFACE, in_signature="u")
    def CancelJob(self, job_id):
        job = self.jobs.get(int(job_id))
        if job:
            job.state = "canceled"
            self.JobRemoved(
                dbus.UInt32(job.job_id), dbus.ObjectPath(job.path), job.unit, "canceled"
            )
        else:
            raise JobNotFound(f"Job {job_id} not found.")

    @dbus.service.method(MANAGER_IFACE)
    def ClearJobs(self):
        self.jobs.clear()

    @dbus.service.method(MANAGER_IFACE, out_signature="a(usssoo)")
    def ListJobs(self):
        return dbus.Array([], signature="(usssoo)")

    @dbus.service.method(MANAGER_IFACE, out_signature="a(ssssssouso)")
    def ListUnits(self):
        result = []
        for name in sc.list_all_units():
            props = sc.get_properties(name)
            path = self._unit_path(name)
            result.append(
                (
                    str(name),
                    str(props["Description"]),
                    str(props["LoadState"]),
                    str(props["ActiveState"]),
                    str(props["SubState"]),
                    "",
                    path,
                    dbus.UInt32(0),
                    "",
                    dbus.ObjectPath("/"),
                )
            )
        return dbus.Array(result, signature="(ssssssouso)")

    @dbus.service.method(MANAGER_IFACE, in_signature="asas", out_signature="a(ss)")
    def ListUnitFilesByPatterns(self, patterns, states):
        patterns = [str(item) for item in patterns]
        allowed_states = {str(item) for item in states}
        result = []
        for name, state in sc.list_unit_files():
            if allowed_states and state not in allowed_states:
                continue
            if not patterns or any(pattern in name for pattern in patterns):
                result.append((str(name), str(state)))
        return dbus.Array(
            [(dbus.String(a), dbus.String(b)) for a, b in result], signature="(ss)"
        )

    @dbus.service.method(MANAGER_IFACE, out_signature="a(ss)")
    def ListUnitFiles(self):
        return self.ListUnitFilesByPatterns([], [])

    @dbus.service.method(MANAGER_IFACE, in_signature="asbb", out_signature="bas")
    def EnableUnitFiles(self, files, runtime, force):
        changes = []
        for name in files:
            ok, message = sc.enable_unit(str(name))
            if ok:
                changes.append(f"Created symlink for {name}")
        return dbus.Boolean(True), dbus.Array(changes, signature="s")

    @dbus.service.method(MANAGER_IFACE, in_signature="asb", out_signature="as")
    def DisableUnitFiles(self, files, runtime):
        changes = []
        for name in files:
            ok, _ = sc.disable_unit(str(name))
            if ok:
                changes.append(f"Removed symlink for {name}")
        return dbus.Array(changes, signature="s")

    @dbus.service.method(MANAGER_IFACE, in_signature="asbb", out_signature="bas")
    def ReenableUnitFiles(self, files, runtime, force):
        for name in files:
            sc.disable_unit(str(name))
            sc.enable_unit(str(name))
        return dbus.Boolean(True), dbus.Array([], signature="s")

    @dbus.service.method(MANAGER_IFACE, in_signature="asbb", out_signature="bas")
    def MaskUnitFiles(self, files, runtime, force):
        for name in files:
            sc.mask_unit(str(name))
        return dbus.Boolean(True), dbus.Array([], signature="s")

    @dbus.service.method(MANAGER_IFACE, in_signature="asb", out_signature="as")
    def UnmaskUnitFiles(self, files, runtime):
        changes = []
        for name in files:
            sc.unmask_unit(str(name))
            changes.append(f"Unmasked {name}")
        return dbus.Array(changes, signature="s")

    @dbus.service.method(MANAGER_IFACE, in_signature="asbb", out_signature="bas")
    def LinkUnitFiles(self, files, runtime, force):
        return dbus.Boolean(True), dbus.Array([], signature="s")

    @dbus.service.method(MANAGER_IFACE, in_signature="asbb", out_signature="bas")
    def PresetUnitFiles(self, files, runtime, force):
        for name in files:
            sc.enable_unit(str(name))
        return dbus.Boolean(True), dbus.Array([], signature="s")

    @dbus.service.method(MANAGER_IFACE, in_signature="assbb", out_signature="bas")
    def PresetUnitFilesWithMode(self, files, mode, runtime, force):
        for name in files:
            sc.enable_unit(str(name))
        return dbus.Boolean(True), dbus.Array([], signature="s")

    @dbus.service.method(MANAGER_IFACE, in_signature="asb", out_signature="as")
    def RevertUnitFiles(self, files, runtime):
        return dbus.Array([], signature="s")

    @dbus.service.method(MANAGER_IFACE)
    def Reload(self):
        sc.supervisorctl("reread")
        sc.supervisorctl("update")
        sc.invalidate_status_cache()

    @dbus.service.method(MANAGER_IFACE, out_signature="s")
    def GetDefaultTarget(self):
        return sc.get_default_target()

    @dbus.service.method(MANAGER_IFACE, in_signature="sb")
    def SetDefaultTarget(self, name, force):
        sc.set_default_target(str(name))

    @dbus.service.method(MANAGER_IFACE, in_signature="as")
    def SetEnvironment(self, assignments):
        for item in assignments:
            text = str(item)
            self.environment = [existing for existing in self.environment if existing.split("=", 1)[0] != text.split("=", 1)[0]]
            self.environment.append(text)

    @dbus.service.method(MANAGER_IFACE, in_signature="as")
    def UnsetEnvironment(self, names):
        lowered = {str(name) for name in names}
        self.environment = [
            item for item in self.environment if item.split("=", 1)[0] not in lowered
        ]

    @dbus.service.method(MANAGER_IFACE, in_signature="asas")
    def UnsetAndSetEnvironment(self, names, assignments):
        self.UnsetEnvironment(names)
        self.SetEnvironment(assignments)

    @dbus.service.method(MANAGER_IFACE, out_signature="s")
    def Dump(self):
        return "\n".join(
            f"{unit} {sc.unit_active_state(unit)[0]}/{sc.unit_active_state(unit)[1]}"
            for unit in sc.list_all_units()
        )

    @dbus.service.method(MANAGER_IFACE)
    def Subscribe(self):
        pass

    @dbus.service.method(MANAGER_IFACE)
    def Unsubscribe(self):
        pass


class Unit(dbus.service.Object):
    def __init__(self, manager: Manager, name: str, path: str):
        super().__init__(manager._connection, path)
        self.manager = manager
        self.name = name
        self.path = path

    def _base_props(self) -> dict[str, object]:
        return sc.get_properties(self.name)

    def _service_props(self) -> dict[str, object]:
        props = self._base_props()
        return {
            key: props.get(key, "")
            for key in (
                "Type",
                "Restart",
                "ExecStart",
                "ExecStop",
                "ExecReload",
                "PIDFile",
                "User",
                "Group",
                "WorkingDirectory",
                "Environment",
                "EnvironmentFiles",
                "UnsetEnvironment",
                "StandardOutput",
                "StandardError",
                "RemainAfterExit",
                "GuessMainPID",
                "KillMode",
                "KillSignal",
                "TimeoutStartUSec",
                "TimeoutStopUSec",
                "RuntimeMaxUSec",
                "WatchdogUSec",
                "Delegate",
                "PermissionsStartOnly",
                "RootDirectoryStartOnly",
                "SendSIGHUP",
                "SendSIGKILL",
                "MainPID",
                "ControlPID",
                "StatusText",
                "MemoryCurrent",
                "TasksCurrent",
                "CPUUsageNSec",
            )
        }

    def _target_props(self) -> dict[str, object]:
        props = self._base_props()
        return {
            key: props.get(key, "")
            for key in (
                "Requires",
                "Wants",
                "Conflicts",
                "Before",
                "After",
                "AllowIsolate",
                "DefaultDependencies",
                "SurviveFinalKillSignal",
                "IgnoreOnIsolate",
            )
        }

    def _props_for(self, interface: str) -> dict[str, object]:
        if interface == UNIT_IFACE:
            return self._base_props()
        if interface == SERVICE_IFACE and self.name.endswith(".service"):
            return self._service_props()
        if interface == TARGET_IFACE and self.name.endswith(".target"):
            return self._target_props()
        if interface == PROPS_IFACE:
            return {}
        raise UnknownInterface(f"Unknown interface {interface}")

    @dbus.service.method(PROPS_IFACE, in_signature="ss", out_signature="v")
    def Get(self, interface, prop):
        props = self._props_for(str(interface))
        if str(prop) not in props:
            raise UnknownProperty(f"Unknown property {prop}")
        return dbus_value(str(prop), props[str(prop)])

    @dbus.service.method(PROPS_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        props = self._props_for(str(interface))
        return dbus.Dictionary(
            {key: dbus_value(key, value) for key, value in props.items()},
            signature="sv",
        )

    @dbus.service.method(PROPS_IFACE, in_signature="ssv")
    def Set(self, interface, prop, value):
        raise PropertyReadOnly(f"Property {prop} is read-only")

    @dbus.service.signal(PROPS_IFACE, signature="sa{sv}as")
    def PropertiesChanged(self, interface, changed, invalidated):
        pass

    @dbus.service.method(UNIT_IFACE, in_signature="s", out_signature="o")
    def Start(self, mode):
        return self.manager._job_path(self.name, "start")

    @dbus.service.method(UNIT_IFACE, in_signature="s", out_signature="o")
    def Stop(self, mode):
        return self.manager._job_path(self.name, "stop")

    @dbus.service.method(UNIT_IFACE, in_signature="s", out_signature="o")
    def Restart(self, mode):
        return self.manager._job_path(self.name, "restart")

    @dbus.service.method(UNIT_IFACE, in_signature="s", out_signature="o")
    def Reload(self, mode):
        return self.manager._job_path(self.name, "reload")

    @dbus.service.method(UNIT_IFACE, in_signature="si")
    def Kill(self, whom, signal_number):
        try:
            signame = signal.Signals(int(signal_number)).name
        except (ValueError, KeyError):
            signame = "SIGTERM"
        ok, message = sc.kill_unit(self.name, signame)
        if not ok:
            raise NoSuchUnit(message)

    @dbus.service.method(UNIT_IFACE)
    def ResetFailed(self):
        sc.supervisorctl("clear", sc.program_for(self.name))

    @dbus.service.method(UNIT_IFACE, in_signature="a(sv)b")
    def SetProperties(self, properties, runtime):
        raise PropertyReadOnly("Unit properties are read-only in this compatibility layer.")


class Job(dbus.service.Object):
    def __init__(self, manager: Manager, job_id: int, unit: str, job_type: str):
        self.manager = manager
        self.job_id = job_id
        self.unit = unit
        self.job_type = job_type
        self.state = "running"
        self.error = ""
        self.path = f"{MANAGER_PATH}/job/{job_id}"
        super().__init__(manager._connection, self.path)

    def _props(self) -> dict[str, object]:
        return {
            "Id": self.job_id,
            "Unit": self.manager._unit_path(self.unit),
            "JobType": self.job_type,
            "State": self.state,
        }

    @dbus.service.method(PROPS_IFACE, in_signature="ss", out_signature="v")
    def Get(self, interface, prop):
        props = self._props()
        if str(prop) not in props:
            raise UnknownProperty(f"Unknown property {prop}")
        return dbus_value(str(prop), props[str(prop)])

    @dbus.service.method(PROPS_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        return dbus.Dictionary(
            {key: dbus_value(key, value) for key, value in self._props().items()},
            signature="sv",
        )

    @dbus.service.method(PROPS_IFACE, in_signature="ssv")
    def Set(self, interface, prop, value):
        raise PropertyReadOnly(f"Property {prop} is read-only")

    @dbus.service.signal(PROPS_IFACE, signature="sa{sv}as")
    def PropertiesChanged(self, interface, changed, invalidated):
        pass

    @dbus.service.method("org.freedesktop.systemd1.Job")
    def Cancel(self):
        self.state = "canceled"


def target_address(argv: list[str]) -> str | None:
    """Return the bus address requested with --address/--address= (or None)."""
    for index, argument in enumerate(argv):
        if argument.startswith("--address="):
            return argument.split("=", 1)[1]
        if argument == "--address" and index + 1 < len(argv):
            return argv[index + 1]
    return None


def main() -> int:
    address = target_address(sys.argv[1:])
    if address:
        # dbus-python can only open the "system" or the "session" bus, so point
        # the system bus at the requested address.  This is how a second
        # instance serves the desktop user's session bus, where
        # org.freedesktop.systemd1 is what "systemctl --user" and
        # "busctl --user" look for.  (The session bus only accepts connections
        # from the user that owns it, so this instance runs as that user.)
        os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = address
        # Real systemd keeps the system and per-user unit search paths separate,
        # so this instance must not describe system units as the user manager's.
        sc.set_scope("user")
    else:
        sc.set_scope("system")
    DBusGMainLoop(set_as_default=True)
    bus = None
    last_error: Exception | None = None
    deadline = time.monotonic() + 120.0
    while bus is None and time.monotonic() < deadline:
        try:
            bus = dbus.SystemBus()
        except dbus.exceptions.DBusException as exc:
            last_error = exc
            time.sleep(0.5)
    if bus is None:
        print(f"systemd-dbus: cannot connect to the bus: {last_error}", file=sys.stderr)
        return 1

    manager = Manager(bus, MANAGER_PATH)
    # Keep a strong reference on the manager so the bus name is not released.
    manager._connection = bus
    manager.bus_name = None

    def acquire_name() -> bool:
        """Own SYSTEMD1, retrying while another instance still holds it."""
        if manager.bus_name is not None:
            return False
        try:
            manager.bus_name = dbus.service.BusName(SYSTEMD1, bus, do_not_queue=True)
        except dbus.exceptions.DBusException:
            return True
        return False

    if acquire_name():
        # Another instance (started by D-Bus activation) owns the name.  Stay
        # alive instead of exiting: a crash loop would leave the name unowned
        # whenever that instance stops.  Take over if it ever disappears.
        print(
            f"systemd-dbus: {SYSTEMD1} is already owned on this bus; retrying",
            file=sys.stderr,
        )
        GLib.timeout_add_seconds(5, acquire_name)

    loop = GLib.MainLoop()
    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
