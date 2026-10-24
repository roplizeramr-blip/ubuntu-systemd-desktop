#!/usr/bin/env python3
"""Minimal org.freedesktop.login1 facade for this container.

The image runs Supervisor as PID 1, so there is no logind.  The systemd
package still ships logind's D-Bus activation file, which means applications
that ask for a sleep inhibitor (a desktop app's "Keep computer awake", for
example) fail with org.freedesktop.DBus.Error.Spawn.ChildExited instead of
getting an answer.

This service takes over that bus name, reports that suspend/hibernate are not
available, and hands back a real (inert) file descriptor from Inhibit().  The
descriptor is kept open for the lifetime of the process, which is exactly what
a sleep inhibitor is supposed to do.
"""
from __future__ import annotations

import os
import sys

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

BUS_NAME = "org.freedesktop.login1"
OBJECT_PATH = "/org/freedesktop/login1"
MANAGER = "org.freedesktop.login1.Manager"
PROPS = "org.freedesktop.DBus.Properties"

_UNIX_FD = getattr(dbus, "UnixFd", None)
if _UNIX_FD is None:  # pragma: no cover - older dbus-python layouts
    from dbus.types import UnixFd as _UNIX_FD  # type: ignore


class NotSupported(dbus.exceptions.DBusException):
    _dbus_error_name = "org.freedesktop.login1.NotSupported"


class UnknownProperty(dbus.exceptions.DBusException):
    _dbus_error_name = "org.freedesktop.DBus.Error.UnknownProperty"


class UnknownInterface(dbus.exceptions.DBusException):
    _dbus_error_name = "org.freedesktop.DBus.Error.UnknownInterface"


class Manager(dbus.service.Object):
    def __init__(self, bus):
        super().__init__(bus, OBJECT_PATH)
        self._inhibitors: list[int] = []

    # --------------------------------------------------------------- inhibitors
    @dbus.service.method(MANAGER, in_signature="ssss", out_signature="h")
    def Inhibit(self, what, who, why, mode):
        fd = os.open(os.devnull, os.O_RDONLY)
        self._inhibitors.append(fd)
        return _UNIX_FD(fd)

    # ------------------------------------------------------------- capabilities
    @dbus.service.method(MANAGER, out_signature="s")
    def CanSuspend(self):
        return "no"

    @dbus.service.method(MANAGER, out_signature="s")
    def CanHibernate(self):
        return "no"

    @dbus.service.method(MANAGER, out_signature="s")
    def CanHybridSleep(self):
        return "no"

    @dbus.service.method(MANAGER, out_signature="s")
    def CanSuspendThenHibernate(self):
        return "no"

    @dbus.service.method(MANAGER, out_signature="s")
    def CanPowerOff(self):
        return "yes"

    @dbus.service.method(MANAGER, out_signature="s")
    def CanReboot(self):
        return "yes"

    @dbus.service.method(MANAGER, in_signature="b")
    def Suspend(self, interactive):
        raise NotSupported("Suspend is not available in this container")

    @dbus.service.method(MANAGER, in_signature="b")
    def Hibernate(self, interactive):
        raise NotSupported("Hibernate is not available in this container")

    @dbus.service.method(MANAGER, in_signature="b")
    def HybridSleep(self, interactive):
        raise NotSupported("Hybrid sleep is not available in this container")

    @dbus.service.method(MANAGER, in_signature="bb")
    def SuspendThenHibernate(self, interactive, wakeup):
        raise NotSupported("Suspend is not available in this container")

    @dbus.service.method(MANAGER, in_signature="b")
    def PowerOff(self, interactive):
        raise NotSupported("Power off must be done by the platform")

    @dbus.service.method(MANAGER, in_signature="b")
    def Reboot(self, interactive):
        raise NotSupported("Reboot must be done by the platform")

    # ----------------------------------------------------------------- sessions
    @dbus.service.method(MANAGER, out_signature="a(susso)")
    def ListSessions(self):
        return dbus.Array([], signature="(susso)")

    @dbus.service.method(MANAGER, out_signature="a(uso)")
    def ListUsers(self):
        return dbus.Array([], signature="(uso)")

    @dbus.service.method(MANAGER, out_signature="a(so)")
    def ListSeats(self):
        return dbus.Array([], signature="(so)")

    @dbus.service.method(MANAGER, out_signature="a(ss)")
    def ListInhibitors(self):
        return dbus.Array([], signature="(ss)")

    # --------------------------------------------------------------- properties
    def _props(self) -> dict[str, object]:
        return {
            "IdleHint": False,
            "IdleSinceHint": dbus.UInt64(0),
            "IdleSinceHintMonotonic": dbus.UInt64(0),
            "PreparingForSleep": False,
            "BlockInhibited": "",
            "DelayInhibited": "",
            "InhibitDelayMaxUSec": dbus.UInt64(0),
            "NCurrentSessions": dbus.UInt32(0),
            "NCurrentUsers": dbus.UInt32(0),
        }

    @dbus.service.method(PROPS, in_signature="ss", out_signature="v")
    def Get(self, interface, prop):
        if interface not in (MANAGER, PROPS):
            raise UnknownInterface(f"Unknown interface {interface}")
        props = self._props()
        if prop not in props:
            raise UnknownProperty(f"Unknown property {prop}")
        return props[prop]

    @dbus.service.method(PROPS, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        if interface not in (MANAGER, PROPS):
            raise UnknownInterface(f"Unknown interface {interface}")
        return dbus.Dictionary(self._props(), signature="sv")

    @dbus.service.method(PROPS, in_signature="ssv")
    def Set(self, interface, prop, value):
        raise NotSupported("Properties are read-only in this container")

    @dbus.service.signal(PROPS, signature="sa{sv}as")
    def PropertiesChanged(self, interface, changed, invalidated):
        pass


def main() -> int:
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    try:
        dbus.service.BusName(BUS_NAME, bus, do_not_queue=True)
    except dbus.exceptions.DBusException as exc:
        print(f"login1-stub: cannot own {BUS_NAME}: {exc}", file=sys.stderr)
        return 1

    manager = Manager(bus)
    # Keep a reference so the bus name is not released by the garbage collector.
    manager._connection = bus
    GLib.MainLoop().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
