#!/bin/bash
# Container entrypoint.
#
# Responsibilities: prepare the runtime directories, turn VNC_PW into an nginx
# Basic-auth file, render the nginx configuration for the public port, apply the
# optional Linux passwords, and hand PID 1 to Supervisor.
set -Eeuo pipefail

export VNC_PW="${VNC_PW:-}"
export VNC_USER="${VNC_USER:-admin}"
export VNC_AUTH="${VNC_AUTH:-1}"
export VNC_RESOLUTION="${VNC_RESOLUTION:-1280x720}"
export VNC_DISPLAY="${VNC_DISPLAY:-1}"
export VNC_PORT="${VNC_PORT:-5901}"
export WS_PORT="${WS_PORT:-6080}"
# PORT is the public port provided by the platform; CONTROL_PORT is internal.
export PORT="${PORT:-3000}"
export CONTROL_PORT="${CONTROL_PORT:-3001}"
export TZ="${TZ:-Africa/Cairo}"
export SWAP_ENABLED="${SWAP_ENABLED:-0}"
export SWAP_DIR="${SWAP_DIR:-/var/swap}"
export SWAP_FILE="${SWAP_FILE:-${SWAP_DIR}/swapfile}"
export SWAP_SIZE_MB="${SWAP_SIZE_MB:-6144}"
export SWAP_SPARSE="${SWAP_SPARSE:-1}"
# PUBLIC_DESKTOP=1 publishes the desktop at "/" and the status page at /status.
# 0 serves the status page at "/" and keeps the desktop at /vnc.html.
export PUBLIC_DESKTOP="${PUBLIC_DESKTOP:-1}"

if ! locale -a 2>/dev/null | grep -qi '^en_US\.utf8$'; then
    export LANG="C.UTF-8" LANGUAGE="C.UTF-8" LC_ALL="C.UTF-8"
else
    export LANG="${LANG:-en_US.UTF-8}"
    export LANGUAGE="${LANGUAGE:-en_US:en}"
    export LC_ALL="${LC_ALL:-en_US.UTF-8}"
fi

if [ -f "/usr/share/zoneinfo/${TZ}" ]; then
    ln -snf "/usr/share/zoneinfo/${TZ}" /etc/localtime
    printf '%s\n' "$TZ" > /etc/timezone
fi

# ------------------------------------------------------------------ runtime dirs
rm -f /run/supervisor.sock /run/nginx.pid
rm -f "/tmp/.X${VNC_DISPLAY}-lock" "/tmp/.X11-unix/X${VNC_DISPLAY}"
install -d -m 1777 /tmp/.X11-unix
install -d -m 0755 /var/log/supervisor /var/log/nginx /run/nginx
install -d -m 0700 -o desktop -g desktop /run/user/1000
install -d -m 0700 -o desktop -g desktop /home/desktop/.vnc
install -d -m 0755 -o desktop -g desktop \
    /home/desktop/.config /home/desktop/.cache /home/desktop/Desktop

# Best-effort kernel tuning; /proc/sys may be read-only.
for setting in \
    "vm.swappiness=10" \
    "vm.vfs_cache_pressure=50" \
    "vm.dirty_background_ratio=5" \
    "vm.dirty_ratio=10" \
    "fs.inotify.max_user_watches=524288"; do
    sysctl -w "$setting" >/dev/null 2>&1 || true
done

echo "[desktop] TigerVNC ${VNC_RESOLUTION} on 127.0.0.1:${VNC_PORT} (display :${VNC_DISPLAY})"
echo "[desktop] noVNC/websockify 127.0.0.1:${WS_PORT}; nginx public :${PORT}"
echo "[desktop] control API 127.0.0.1:${CONTROL_PORT}"
echo "[desktop] swap enabled=${SWAP_ENABLED} (${SWAP_SIZE_MB} MiB at ${SWAP_FILE})"

# A stable machine-id keeps applications that read it happy.
if [ ! -s /etc/machine-id ]; then
    tr -d '-' < /proc/sys/kernel/random/uuid > /etc/machine-id 2>/dev/null || true
fi

if [ -n "${DESKTOP_HOSTNAME:-}" ]; then
    hostname "$DESKTOP_HOSTNAME" 2>/dev/null || true
    printf '%s\n' "$DESKTOP_HOSTNAME" > /etc/hostname 2>/dev/null || true
fi

# ------------------------------------------------------------------ credentials
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

# nginx answers the password prompt; the desktop socket stays on loopback.
if command -v openssl >/dev/null 2>&1; then
    HASH="$(openssl passwd -apr1 "$VNC_PW")"
else
    echo "[desktop] ERROR: openssl is required to hash VNC_PW" >&2
    exit 1
fi
printf '%s:%s\n' "$VNC_USER" "$HASH" > /etc/nginx/.htpasswd
chown root:www-data /etc/nginx/.htpasswd 2>/dev/null || true
chmod 0640 /etc/nginx/.htpasswd

# ------------------------------------------------------------------ nginx config
sed -e "s/__PORT__/${PORT}/g" \
    -e "s/__WS_PORT__/${WS_PORT}/g" \
    -e "s/__CONTROL_PORT__/${CONTROL_PORT}/g" \
    /usr/local/bin/nginx.conf.template > /etc/nginx/nginx.conf

if ! /usr/sbin/nginx -t -c /etc/nginx/nginx.conf >/var/log/nginx/config-test.log 2>&1; then
    echo "[desktop] ERROR: nginx configuration is invalid" >&2
    cat /var/log/nginx/config-test.log >&2 || true
    exit 1
fi

# ------------------------------------------------------------------ optional bits
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

if [ -n "${ROOT_PW:-}" ]; then
    if printf 'root:%s\n' "$ROOT_PW" | chpasswd 2>/dev/null; then
        echo "[desktop] root password updated from ROOT_PW"
    else
        echo "[desktop] WARNING: could not set the root password from ROOT_PW"
    fi
else
    echo "[desktop] ROOT_PW is not set; root login stays locked"
fi

if [ "${SWAP_ENABLED}" = "1" ]; then
    /usr/local/bin/setup-swap.sh >/var/log/swap.log 2>&1 &
fi

if [ "${HIDE_CONTAINER:-0}" = "1" ]; then
    rm -f /.dockerenv
fi

exec "$@"
