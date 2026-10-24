#!/bin/bash
set -Eeuo pipefail

export VNC_PW="${VNC_PW:-}"
export VNC_USER="${VNC_USER:-admin}"
export VNC_AUTH="${VNC_AUTH:-1}"
export VNC_RESOLUTION="${VNC_RESOLUTION:-1440x900}"
export VNC_FRAME_RATE="${VNC_FRAME_RATE:-30}"
export KASM_PORT="${KASM_PORT:-6080}"
export KASM_DISPLAY="${KASM_DISPLAY:-1}"
export CONTROL_PORT="${CONTROL_PORT:-${PORT:-3000}}"
export TZ="${TZ:-Africa/Cairo}"
export SYSTEMD_DETECT_VIRT="${SYSTEMD_DETECT_VIRT:-none}"
export SWAP_ENABLED="${SWAP_ENABLED:-0}"
export SWAP_DIR="${SWAP_DIR:-/var/swap}"
export SWAP_FILE="${SWAP_FILE:-${SWAP_DIR}/swapfile}"
export SWAP_SIZE_MB="${SWAP_SIZE_MB:-6144}"
export SWAP_SPARSE="${SWAP_SPARSE:-1}"
export TS_HOSTNAME="${TS_HOSTNAME:-ubuntu-desktop}"
export TS_STATE_DIR="${TS_STATE_DIR:-/var/lib/tailscale}"
export TS_TUN="${TS_TUN:-userspace}"
# VNC is published on the Tailscale link (MagicDNS name over HTTPS on :443) so
# "Open VNC" uses https://<host>.<tailnet>.ts.net/ and no port on the raw
# Tailscale IP is consumed.  The raw TCP rule is opt-in only; leave the IP and
# its ports free for other apps you host on the same node.
export TS_SERVE_HTTPS_PORT="${TS_SERVE_HTTPS_PORT:-443}"
export TS_SERVE_HTTP_PORT="${TS_SERVE_HTTP_PORT:-0}"
export TS_SERVE_FALLBACK_TCP="${TS_SERVE_FALLBACK_TCP:-0}"
# TS_FUNNEL=1 publishes the desktop publicly on the internet with
# `tailscale funnel` (still password protected).  The default, 0, keeps it
# private to devices signed in to the same tailnet.
export TS_FUNNEL="${TS_FUNNEL:-0}"
# PUBLIC_DESKTOP=1 makes the public CranL port serve the KasmVNC desktop at /
# (control page moves to /status).  0 keeps the public port on the lightweight
# control page and leaves the desktop reachable only through Tailscale.
export PUBLIC_DESKTOP="${PUBLIC_DESKTOP:-0}"
export TS_SERVE_INTERVAL="${TS_SERVE_INTERVAL:-10}"

if ! locale -a 2>/dev/null | grep -qi '^en_US\.utf8$'; then
    export LANG="C.UTF-8"
    export LANGUAGE="C.UTF-8"
    export LC_ALL="C.UTF-8"
else
    export LANG="${LANG:-en_US.UTF-8}"
    export LANGUAGE="${LANGUAGE:-en_US:en}"
    export LC_ALL="${LC_ALL:-en_US.UTF-8}"
fi

if [ -f "/usr/share/zoneinfo/${TZ}" ]; then
    ln -snf "/usr/share/zoneinfo/${TZ}" /etc/localtime
    printf '%s\n' "$TZ" > /etc/timezone
fi

# Runtime directories that systemd normally creates.  Remove sockets and X11
# lock files left behind by a previous container run before starting fresh.
rm -f /run/dbus/system_bus_socket /run/dbus/pid /run/supervisor.sock
rm -f "/tmp/.X${KASM_DISPLAY}-lock" "/tmp/.X11-unix/X${KASM_DISPLAY}"
rm -f /run/desktop-tailscale-http.ok /run/desktop-tailscale-https.ok
rm -f /var/run/tailscale/tailscaled.sock
install -d -m 0755 /run/systemd/system /run/systemd/journal /run/dbus
install -d -m 1777 /tmp/.X11-unix
install -d -m 0755 /var/log/supervisor /var/log/journal
install -d -m 0700 -o desktop -g desktop /run/user/1000
install -d -m 0700 -o desktop -g desktop /home/desktop/.vnc
install -d -m 0755 -o desktop -g desktop \
    /home/desktop/.config /home/desktop/.cache /home/desktop/Desktop
install -d -m 0700 /var/lib/tailscale
install -d -m 0755 /var/run/tailscale

# Best-effort kernel memory tuning. /proc/sys is read-only in some containers;
# a failure here must never stop the desktop from starting.
for setting in \
    "vm.swappiness=10" \
    "vm.vfs_cache_pressure=50" \
    "vm.dirty_background_ratio=5" \
    "vm.dirty_ratio=10" \
    "fs.inotify.max_user_watches=524288"; do
    sysctl -w "$setting" >/dev/null 2>&1 || true
done

echo "[desktop] KasmVNC ${VNC_RESOLUTION} on 127.0.0.1:${KASM_PORT} (display :${KASM_DISPLAY}, auth=${VNC_AUTH})"
echo "[desktop] public port :${CONTROL_PORT} -> $(if [ "${PUBLIC_DESKTOP}" = "1" ]; then echo 'KasmVNC at / (control page at /status)'; else echo 'control page at /'; fi); Tailscale $(if [ "${TS_FUNNEL}" = "1" ]; then echo 'Funnel (public)'; else echo 'Serve (tailnet only)'; fi) HTTPS :${TS_SERVE_HTTPS_PORT} -> 127.0.0.1:${KASM_PORT} (raw TCP :${TS_SERVE_HTTP_PORT})"
echo "[desktop] swap enabled=${SWAP_ENABLED} (${SWAP_SIZE_MB} MiB at ${SWAP_FILE})"

# A stable machine-id and boot timestamp make hostnamectl/journalctl useful.
if [ ! -s /etc/machine-id ]; then
    if command -v systemd-machine-id-setup >/dev/null 2>&1; then
        systemd-machine-id-setup 2>/dev/null || true
    fi
    if [ ! -s /etc/machine-id ]; then
        tr -d '-' < /proc/sys/kernel/random/uuid > /etc/machine-id
    fi
fi
date +%s > /run/systemd/system/boot-time

if [ -n "${DESKTOP_HOSTNAME:-}" ]; then
    hostname "$DESKTOP_HOSTNAME" 2>/dev/null || true
    printf '%s\n' "$DESKTOP_HOSTNAME" > /etc/hostname 2>/dev/null || true
fi

# KasmVNC reads a YAML config from the user's home and a credential file with
# one entry per named Basic Auth user.  Keep the HTTP/WebSocket listener on
# loopback: there is no public VNC port in this image.
if [[ "$VNC_PW" == *$'\n'* ]]; then
    echo "[desktop] ERROR: VNC_PW must not contain a newline" >&2
    exit 1
fi
if [ -z "$VNC_PW" ]; then
    echo "[desktop] ERROR: VNC_PW must be set in the deployment environment" >&2
    exit 1
fi
if [ "${#VNC_PW}" -lt 6 ]; then
    echo "[desktop] ERROR: VNC_PW must be at least 6 characters" >&2
    exit 1
fi
case "$VNC_USER" in
    ''|*[!A-Za-z0-9_.-]*)
        echo "[desktop] ERROR: VNC_USER may contain only letters, digits, dot, dash and underscore" >&2
        exit 1
        ;;
esac
case "$VNC_FRAME_RATE" in
    ''|*[!0-9]*) VNC_FRAME_RATE=30 ;;
esac

# KasmVNC's built-in default (1024x768) is merged after the command line, so
# the requested geometry has to live in the config file to win.  Accept only
# WxH; anything else falls back to 1440x900.
case "$VNC_RESOLUTION" in
    [0-9]*x[0-9]*) VNC_WIDTH="${VNC_RESOLUTION%x*}"; VNC_HEIGHT="${VNC_RESOLUTION#*x}" ;;
    *) VNC_WIDTH=1440; VNC_HEIGHT=900 ;;
esac
case "$VNC_WIDTH" in ''|*[!0-9]*) VNC_WIDTH=1440 ;; esac
case "$VNC_HEIGHT" in ''|*[!0-9]*) VNC_HEIGHT=900 ;; esac

# Ensure the KasmVNC web server can read the snakeoil certificate/key installed
# by ssl-cert.  The image adds the user to this group; repeat it at runtime for
# volumes or user-created images.
if getent group ssl-cert >/dev/null 2>&1; then
    usermod -aG ssl-cert desktop >/dev/null 2>&1 || true
fi

cat > /home/desktop/.vnc/kasmvnc.yaml <<EOF
desktop:
  resolution:
    width: $VNC_WIDTH
    height: $VNC_HEIGHT
  allow_resize: true
  pixel_depth: 24
encoding:
  max_frame_rate: ${VNC_FRAME_RATE}
  # Software encoding on a small, shared CPU: let the encoder trade image
  # quality for responsiveness instead of falling behind, never take the
  # expensive lossless rectangle path, and spend less time on each WebP frame.
  rect_encoding_mode:
    min_quality: 4
    max_quality: 8
    consider_lossless_quality: 10
  video_encoding_mode:
    webp_encoding_time: 10
    enter_video_encoding_mode:
      time_threshold: 3
      area_threshold: 40%
  compare_framebuffer: auto
  hextile_improved_compression: true
network:
  protocol: http
  interface: 127.0.0.1
  websocket_port: ${KASM_PORT}
  ssl:
    require_ssl: false
command_line:
  prompt: false
EOF
chown desktop:desktop /home/desktop/.vnc/kasmvnc.yaml
chmod 0640 /home/desktop/.vnc/kasmvnc.yaml

# KasmVNC's vncpasswd writes an obfuscated credential file, not plaintext.
# It reads the password and its confirmation from stdin.  Always create the
# file, even with VNC_AUTH=0, because the server refuses to start without a
# configured user; -disableBasicAuth is applied by start-kasmvnc.sh.
rm -f /home/desktop/.kasmpasswd
if ! printf '%s\n%s\n' "$VNC_PW" "$VNC_PW" \
    | runuser -u desktop -- env HOME=/home/desktop vncpasswd -u "$VNC_USER" -w; then
    echo "[desktop] ERROR: could not create /home/desktop/.kasmpasswd" >&2
    exit 1
fi
chown desktop:desktop /home/desktop/.kasmpasswd
chmod 0600 /home/desktop/.kasmpasswd

# Remove credentials left by the TigerVNC image; they are not used by KasmVNC.
rm -f /home/desktop/.vnc/passwd /home/desktop/.vnc/xstartup

# Linux login/sudo password. Supply DESKTOP_PW from the deployment's private
# environment so the public image and repository never bake a secret. If it is
# absent, the account stays locked and only the VNC password is usable.
if [ -n "${DESKTOP_PW:-}" ]; then
    if printf 'desktop:%s\n' "$DESKTOP_PW" | chpasswd 2>/dev/null; then
        usermod -U desktop 2>/dev/null || true
        echo "[desktop] Linux password updated from DESKTOP_PW"
    else
        echo "[desktop] WARNING: could not set Linux password from DESKTOP_PW"
    fi
else
    echo "[desktop] DESKTOP_PW is not set; Linux account desktop remains locked"
fi

# Root login.  The image ships with the root account locked; supply ROOT_PW in
# the deployment environment to set (and unlock) it at start.  Leaving it unset
# keeps the default locked account and keeps `sudo` as the only path to root.
if [ -n "${ROOT_PW:-}" ]; then
    if printf 'root:%s\n' "$ROOT_PW" | chpasswd 2>/dev/null; then
        echo "[desktop] root password updated from ROOT_PW"
    else
        echo "[desktop] WARNING: could not set the root password from ROOT_PW"
    fi
else
    echo "[desktop] ROOT_PW is not set; root login stays locked"
fi

# /dev/log is expected by syslog-style applications.  The journald shim
# creates the real socket at /run/systemd/journal/dev-log.
rm -f /dev/log 2>/dev/null || true
ln -sfn /run/systemd/journal/dev-log /dev/log 2>/dev/null || true

# Swap is deliberately opt-in.  KasmVNC uses less memory than the old
# TigerVNC/noVNC stack, and a 6 GiB file wastes disk on the small CranL plan.
if [ "${SWAP_ENABLED:-0}" = "1" ]; then
    /usr/local/bin/setup-swap.sh >/var/log/swap.log 2>&1 &
fi

# Optional compatibility switch: removing /.dockerenv makes some simple
# container checks less noisy. It is disabled by default and is not a security
# boundary. Set HIDE_CONTAINER=1 only if you understand that.
if [ "${HIDE_CONTAINER:-0}" = "1" ]; then
    rm -f /.dockerenv
fi

exec "$@"
