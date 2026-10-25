#!/bin/bash
# Launch KasmVNC as the desktop user.  The web server stays on loopback; the
# public entrypoint (control-server.py) is the only process that listens on the
# public port and reverse-proxies to it.
#
# KasmVNC's vncserver wrapper forks Xvnc and exits (the upstream systemd unit
# uses Type=forking).  Keep a foreground monitor alive so a crashed
# Xvnc is restarted and stopasgroup can tear down the whole session.
set -Eeuo pipefail

export HOME="${HOME:-/home/desktop}"
export DISPLAY="${DISPLAY:-:1}"
export USER="${USER:-desktop}"
export LOGNAME="${LOGNAME:-desktop}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/1000}"

KASM_DISPLAY="${KASM_DISPLAY:-1}"
VNC_RESOLUTION="${VNC_RESOLUTION:-1440x900}"
KASM_PORT="${KASM_PORT:-6080}"

mkdir -p "$HOME/.vnc" "$XDG_RUNTIME_DIR"
chmod 0700 "$HOME/.vnc" "$XDG_RUNTIME_DIR" 2>/dev/null || true

# A crashed Xvnc can leave a stale lock behind; the monitor is only started
# after the previous process group has exited, so it is safe to clear it here.
rm -f "/tmp/.X${KASM_DISPLAY}-lock" "/tmp/.X11-unix/X${KASM_DISPLAY}"
rm -f "$HOME/.vnc/"*":${KASM_DISPLAY}.pid"

# The wrapper consumes ~/.vnc/kasmvnc.yaml, written by entrypoint.sh.  A
# separate XFCE service starts the window manager on the same display;
# -noxstartup keeps KasmVNC focused on X and the web socket.
#
# Loopback confinement comes from `network.interface: 127.0.0.1` in the
# generated kasmvnc.yaml (passed through as Xvnc's -interface).  Do NOT also
# pass -localhost: on KasmVNC 1.5.0 that is a second loopback bind for the same
# listener and Xvnc dies with "vncExtInit: failed to bind socket: Address
# already in use (98)".
args=(
    ":${KASM_DISPLAY}"
    -geometry "$VNC_RESOLUTION"
    -depth 24
    -noxstartup
)
if [ "${VNC_AUTH:-1}" = "0" ]; then
    # No HTTP password: keep the classic RFB password challenge as the only
    # protection in front of the desktop.
    args+=(-disableBasicAuth)
else
    # The web server already asks for the KasmVNC password with HTTP Basic
    # auth (and the reverse proxy forwards that challenge), so the extra RFB
    # password prompt would only ask for the same password twice.
    args+=(-SecurityTypes None)
fi

/usr/bin/vncserver "${args[@]}"

# Wait for the X socket as well as the PID file; XFCE starts immediately after
# the service manager sees this process running.
for _ in $(seq 1 75); do
    if xdpyinfo -display ":${KASM_DISPLAY}" >/dev/null 2>&1; then
        break
    fi
    sleep 0.2
done

pidfile=""
for _ in $(seq 1 30); do
    for candidate in "$HOME/.vnc/"*":${KASM_DISPLAY}.pid"; do
        if [ -s "$candidate" ]; then
            pidfile="$candidate"
            break
        fi
    done
    [ -n "$pidfile" ] && break
    sleep 0.2
done

if [ -z "$pidfile" ]; then
    echo "[kasmvnc] vncserver did not create a PID file" >&2
    exit 1
fi

xvnc_pid="$(cat "$pidfile")"
if ! kill -0 "$xvnc_pid" 2>/dev/null; then
    echo "[kasmvnc] Xvnc exited before the monitor started" >&2
    exit 1
fi

echo "[kasmvnc] Xvnc pid ${xvnc_pid}; HTTP on 127.0.0.1:${KASM_PORT}"
while kill -0 "$xvnc_pid" 2>/dev/null; do
    sleep 2
done

echo "[kasmvnc] Xvnc exited; requesting a restart" >&2
exit 1
