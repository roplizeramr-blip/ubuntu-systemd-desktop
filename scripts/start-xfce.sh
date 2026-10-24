#!/bin/bash
set -Eeuo pipefail

export HOME="${HOME:-/home/desktop}"
export DISPLAY="${DISPLAY:-:1}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/1000}"
export XDG_SESSION_TYPE="x11"
export XDG_CURRENT_DESKTOP="XFCE"
export XDG_SESSION_DESKTOP="xfce"
export GDK_BACKEND="x11"
export NO_AT_BRIDGE="1"
# There is no GPU in the container: keep Mesa on llvmpipe instead of letting it
# probe DRI3 (which logs "libEGL warning: DRI3 error" on Xvnc) and keep
# WebKitGTK off its DMA-BUF renderer, which cannot work without a DRM device.
# GTK_A11Y=none silences the AT-SPI bridge: there is no accessibility bus for
# it to attach to, and screen readers cannot reach this desktop anyway.
export LIBGL_ALWAYS_SOFTWARE="${LIBGL_ALWAYS_SOFTWARE:-1}"
export WEBKIT_DISABLE_DMABUF_RENDERER="${WEBKIT_DISABLE_DMABUF_RENDERER:-1}"
export GTK_A11Y="${GTK_A11Y:-none}"
# Chrome is always started through /usr/local/bin/google-chrome, which adds the
# container-friendly flags; see scripts/google-chrome-wrapper.sh and
# CHROME_NO_SANDBOX in the image environment.

mkdir -p "$XDG_RUNTIME_DIR"
chmod 0700 "$XDG_RUNTIME_DIR" 2>/dev/null || true

unset SESSION_MANAGER
# The session bus lives at $XDG_RUNTIME_DIR/bus, started by Supervisor's
# user-dbus program.  systemd-user aware tools (busctl --user,
# `systemctl --user`, ...) look for org.freedesktop.systemd1 there, and
# dbus-run-session would start a private bus under /tmp that they cannot see.
export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"

# Supervisor starts this unit shortly after KasmVNC; allow Xvnc a few seconds
# to create the display socket instead of burning restart attempts.
for _ in $(seq 1 150); do
    if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
        break
    fi
    sleep 0.2
done

# Run setup-desktop inside the same session bus as XFCE.  xfconf-query talks
# to xfconfd over DBUS_SESSION_BUS_ADDRESS, so a separate Supervisor program
# would not see the running session.  gnome-keyring is started here as well:
# it has to live on this bus so libsecret clients (Chromium and friends, ...)
# find org.freedesktop.secrets, and unlocking the login keyring with the
# desktop password keeps the "unlock keyring" dialog from appearing.  The
# keyring file lives in $HOME, so it is created anew with each container.
exec bash -c '
    if [ -n "${DESKTOP_PW:-}" ] && command -v gnome-keyring-daemon >/dev/null 2>&1; then
        printf "%s" "$DESKTOP_PW" | gnome-keyring-daemon --unlock --components=secrets >/dev/null 2>&1 || true
    fi
    startxfce4 &
    xfce_pid=$!
    (sleep 2; /usr/local/bin/setup-desktop.sh) &
    wait "$xfce_pid"
'
