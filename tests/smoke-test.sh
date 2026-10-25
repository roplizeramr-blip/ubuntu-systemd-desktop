#!/bin/bash
# Run inside the running container:
#   docker compose exec desktop /opt/desktop/tests/smoke-test.sh
set -u

PASS=0
FAIL=0
PORT="${CONTROL_PORT:-${PORT:-3000}}"

check() {
    local label="$1"
    shift
    if "$@" >/tmp/compat-check.out 2>&1; then
        echo "PASS  $label"
        PASS=$((PASS + 1))
    else
        echo "FAIL  $label"
        sed 's/^/      /' /tmp/compat-check.out || true
        FAIL=$((FAIL + 1))
    fi
}

check_contains() {
    local label="$1" needle="$2"
    shift 2
    if "$@" >/tmp/compat-check.out 2>&1 && grep -q "$needle" /tmp/compat-check.out; then
        echo "PASS  $label"
        PASS=$((PASS + 1))
    else
        echo "FAIL  $label"
        sed 's/^/      /' /tmp/compat-check.out || true
        FAIL=$((FAIL + 1))
    fi
}

echo "== Ubuntu Cloud Desktop smoke test =="
check "systemctl exists" command -v systemctl
check "systemctl --version" systemctl --version
check "/run/systemd/system exists" test -d /run/systemd/system
check "PID 1 is named systemd" bash -c '[ "$(ps -p 1 -o comm= | tr -d " ")" = "systemd" ]'
check "system is running" bash -c '[ "$(systemctl is-system-running)" = "running" ]'
check "kasmvnc is active" bash -c '[ "$(systemctl is-active kasmvnc.service)" = "active" ]'
check "xfce is active" bash -c '[ "$(systemctl is-active xfce.service)" = "active" ]'
check "control page is active" bash -c '[ "$(systemctl is-active control.service)" = "active" ]'
check "detect-virt reports none" bash -c '[ "$(systemd-detect-virt)" = "none" ]'
check_contains "hostnamectl reports Ubuntu" "Ubuntu" hostnamectl
check_contains "systemctl list-units includes kasmvnc" "kasmvnc.service" systemctl list-units --type=service --no-pager
check_contains "Google Chrome is installed" "Google Chrome" google-chrome --version
check "Node.js is installed" node --version
check "npm is installed" npm --version
check "Python 3.15 is installed" python3.15 --version
check "D-Bus manager is introspectable" bash -c 'busctl --system introspect org.freedesktop.systemd1 /org/freedesktop/systemd1 >/dev/null 2>&1'

# KasmVNC is loopback-only and requires Basic Auth by default.
check "Xvnc is running" pgrep -x Xvnc
check "KasmVNC config exists" test -s /home/desktop/.vnc/kasmvnc.yaml
check "KasmVNC credentials exist" test -s /home/desktop/.kasmpasswd
check "KasmVNC is listening on loopback" bash -c 'code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 4 http://127.0.0.1:6080/); [ "$code" = "401" ]'
if [ "${VNC_AUTH:-1}" != "0" ] && [ -n "${VNC_PW:-}" ]; then
    check "KasmVNC Basic Auth accepts VNC_PW" curl -fsS --max-time 5 -u "${VNC_USER:-admin}:${VNC_PW}" http://127.0.0.1:6080/ >/dev/null
fi

# Public control plane.
for _ in $(seq 1 30); do
    curl -fsS --max-time 3 "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1 && break
    sleep 1
done
check "health endpoint" curl -fsS --max-time 5 "http://127.0.0.1:${PORT}/healthz"
check "control page is served" curl -fsS --max-time 5 "http://127.0.0.1:${PORT}/" >/tmp/control-page.html
check_contains "control page has Open desktop" "Open desktop" cat /tmp/control-page.html
check_contains "control page names KasmVNC" "KasmVNC" cat /tmp/control-page.html
check "status API is valid JSON" bash -c "curl -fsS --max-time 5 http://127.0.0.1:${PORT}/api/status | python3 -m json.tool >/dev/null"
check_contains "status API reports KasmVNC" '"engine":"KasmVNC"' curl -fsS --max-time 5 "http://127.0.0.1:${PORT}/api/status"

# The removed stack must not come back.
check "Tailscale is absent" bash -c '! command -v tailscale >/dev/null 2>&1'
check "no tailscaled process" bash -c '! pgrep -x tailscaled >/dev/null 2>&1'
check "Hermes is absent" bash -c '[ ! -e /home/desktop/.hermes ] && ! command -v hermes >/dev/null 2>&1'

# Old stack should not be present.
check "noVNC directory is absent" bash -c '[ ! -d /usr/share/novnc ]'
check "TigerVNC package is absent" bash -c '! dpkg-query -W -f="\${Status}" tigervnc-standalone-server 2>/dev/null | grep -q "install ok installed"'

# Performance helpers.
check "earlyoom is running" pgrep -x earlyoom
check "system dashboard launcher exists" test -f /home/desktop/Desktop/system-dashboard.desktop
check "host swap helper is executable" test -x /usr/local/bin/host-swap.sh

# Swap is opt-in.  Only verify the status file when it was requested.
if [ "${SWAP_ENABLED:-0}" = "1" ]; then
    for _ in $(seq 1 30); do
        [ -s /run/desktop-swap-status.json ] && break
        sleep 1
    done
    check "swap status JSON exists" test -s /run/desktop-swap-status.json
    check "swap status JSON is valid" python3 -m json.tool /run/desktop-swap-status.json
    check "swap file exists" test -f /var/swap/swapfile
fi

if swapon --show=NAME --noheadings 2>/dev/null | grep -q .; then
    echo "PASS  active swap is available"
    PASS=$((PASS + 1))
else
    echo "INFO  swap is not active (expected on CranL)"
fi

echo
echo "Passed: $PASS  Failed: $FAIL"
[ "$FAIL" -eq 0 ]
