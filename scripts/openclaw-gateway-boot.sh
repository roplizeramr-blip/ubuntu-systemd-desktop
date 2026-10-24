#!/bin/bash
# Restore the OpenClaw Gateway service at container start.
#
# The Gateway is a systemd *user* service owned by the desktop account.  Its
# unit file, the <target>.wants enablement symlink and the generated Supervisor
# program that actually runs the process all live below /home/desktop and
# /etc/supervisor/conf.d, so they are image content: rebuilding the image or
# recreating the container discards them and the app then reports the Gateway
# as "not installed".
#
# This helper runs the same `openclaw gateway install` the app's UI runs, once,
# as the desktop user.  It is best effort by design: the desktop must start
# even when OpenClaw cannot be installed, and the app can always retry the
# install from its own UI.
set -u

CLI=/home/desktop/.openclaw/bin/openclaw
UNIT=openclaw-gateway.service

export HOME=/home/desktop
export USER=desktop
export LOGNAME=desktop
export XDG_RUNTIME_DIR=/run/user/1000
export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/1000/bus"
export DISPLAY="${DISPLAY:-:1}"
export PATH="/home/desktop/.openclaw/bin:/usr/local/bin:/usr/bin:/bin"

log() { printf '[openclaw-gateway-boot] %s\n' "$*"; }

if [ ! -x "$CLI" ]; then
    log "OpenClaw CLI is not installed; nothing to do"
    exit 0
fi

# The CLI inspects the per-user manager over D-Bus.  Wait (bounded) for the
# compatibility facade to own org.freedesktop.systemd1 on the session bus
# instead of racing Supervisord's own start-up order.
session_manager_ready() {
    [ -S /run/user/1000/bus ] || return 1
    if command -v busctl >/dev/null 2>&1; then
        busctl --user --no-pager list 2>/dev/null | grep -q '^org\.freedesktop\.systemd1 '
        return $?
    fi
    return 0
}

ready=0
for _ in $(seq 1 60); do
    if session_manager_ready; then
        ready=1
        break
    fi
    sleep 1
done
if [ "$ready" != "1" ]; then
    log "systemd user session did not come up in time; leaving the install to the app"
    exit 0
fi

run_install() {
    if command -v timeout >/dev/null 2>&1; then
        timeout 300 "$CLI" gateway install --force
    else
        "$CLI" gateway install --force
    fi
}

installed=0
for attempt in 1 2 3 4 5; do
    log "gateway install attempt ${attempt}"
    if run_install; then
        installed=1
        break
    fi
    sleep 5
done

if [ "$installed" != "1" ]; then
    log "could not install the Gateway service; the app can retry from its UI"
    exit 0
fi

# A no-op install leaves the service stopped; make sure it is running.
if systemctl --user restart "$UNIT"; then
    log "Gateway service is running"
else
    log "Gateway service was installed but could not be started"
fi
exit 0
