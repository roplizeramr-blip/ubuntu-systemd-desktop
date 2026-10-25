#!/usr/bin/env bash
# Native install of the cloud desktop on a CLEAN Ubuntu 24.04 VPS.
#
# Unlike the container image, this path uses the real systemd that ships with
# Ubuntu: systemd is PID 1, the units in vps/systemd/ are real unit files, and
# no Supervisor process and no systemctl facade are installed.
#
# Run as root on a fresh Ubuntu 24.04 machine:
#
#   VNC_PW='strong-password' ./vps/install.sh
#
# Optional environment:
#   VNC_USER=admin VNC_RESOLUTION=1440x900 VNC_FRAME_RATE=30
#   PUBLIC_DESKTOP=1 CONTROL_PORT=3000 DESKTOP_PW=... ROOT_PW=...
set -Eeuo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KASM_VERSION="${KASM_VERSION:-1.5.0}"
KASM_SHA256="${KASM_SHA256:-f599fe02e2175b9817b6165f74a5d2bebdc73118dde9181ba3410963bed7ae1e}"
VNC_USER="${VNC_USER:-admin}"
VNC_PW="${VNC_PW:-}"
VNC_AUTH="${VNC_AUTH:-1}"
VNC_RESOLUTION="${VNC_RESOLUTION:-1440x900}"
VNC_FRAME_RATE="${VNC_FRAME_RATE:-30}"
CONTROL_PORT="${CONTROL_PORT:-3000}"
KASM_PORT="${KASM_PORT:-6080}"
PUBLIC_DESKTOP="${PUBLIC_DESKTOP:-1}"
DESKTOP_PW="${DESKTOP_PW:-}"
ROOT_PW="${ROOT_PW:-}"
DESKTOP_HOSTNAME="${DESKTOP_HOSTNAME:-$(hostname -s 2>/dev/null || echo desktop)}"
DESKTOP_UID="${DESKTOP_UID:-1000}"

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- preflight
[ "$(id -u)" -eq 0 ] || die "run as root"
[ -r /etc/os-release ] || die "this installer targets Ubuntu"
[ "$(ps -p 1 -o comm= | tr -d ' ')" = "systemd" ] || die "systemd is not PID 1 on this machine"
. /etc/os-release
[ "${VERSION_ID%%.*}" -ge 24 ] 2>/dev/null || warn "tested on Ubuntu 24.04; ${PRETTY_NAME} may need package names adjusted"
[ -n "$VNC_PW" ] || die "set VNC_PW (min 6 characters)"
[ "${#VNC_PW}" -ge 6 ] || die "VNC_PW must be at least 6 characters"
[ "$PUBLIC_DESKTOP" = "1" ] || warn "PUBLIC_DESKTOP=0: the desktop will not be reachable through the control port"

# ---------------------------------------------------------------- packages
log "Installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get -y upgrade
apt-get install -y --no-install-recommends \
    ca-certificates curl wget gnupg xz-utils libatomic1 git \
    locales tzdata sudo passwd procps psmisc iproute2 net-tools hostname less nano \
    dbus dbus-x11 gnome-keyring \
    python3 python3-dbus python3-gi gir1.2-glib-2.0 \
    ssl-cert earlyoom \
    xfwm4 xfce4-panel xfce4-settings xfconf xfce4-terminal \
    xfce4-screenshooter xfce4-taskmanager xfce4-appfinder \
    thunar mousepad ristretto \
    x11-utils x11-xserver-utils x11-xkb-utils xauth xdg-utils desktop-file-utils \
    xfonts-base xfonts-utils adwaita-icon-theme \
    fonts-dejavu-core fonts-noto-core fonts-noto-color-emoji fonts-liberation \
    librsvg2-common librsvg2-bin feh

log "Installing Google Chrome"
install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://dl.google.com/linux/linux_signing_key.pub -o /etc/apt/keyrings/google-chrome.asc
chmod 0644 /etc/apt/keyrings/google-chrome.asc
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.asc] https://dl.google.com/linux/chrome/deb/ stable main" \
    > /etc/apt/sources.list.d/google-chrome.list
apt-get update
apt-get install -y --no-install-recommends google-chrome-stable

log "Installing KasmVNC ${KASM_VERSION}"
curl -fsSL "https://github.com/kasmtech/KasmVNC/releases/download/v${KASM_VERSION}/kasmvncserver_noble_${KASM_VERSION}_amd64.deb" \
    -o /tmp/kasmvnc.deb
echo "${KASM_SHA256}  /tmp/kasmvnc.deb" | sha256sum -c -
apt-get install -y --no-install-recommends /tmp/kasmvnc.deb
rm -f /tmp/kasmvnc.deb

log "Configuring locale and timezone"
sed -i 's/^# *en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen
locale-gen
update-locale LANG=en_US.UTF-8 LANGUAGE=en_US:en LC_ALL=en_US.UTF-8

# ---------------------------------------------------------------- user
log "Creating the desktop user"
if ! getent group desktop >/dev/null; then
    groupadd -g "$DESKTOP_UID" desktop 2>/dev/null || groupadd desktop
fi
if ! getent passwd desktop >/dev/null; then
    useradd -m -u "$DESKTOP_UID" -g desktop -s /bin/bash -G sudo,ssl-cert desktop
fi
usermod -aG sudo,ssl-cert desktop
install -d -m 0755 -o desktop -g desktop \
    /home/desktop/Desktop /home/desktop/.config /home/desktop/.cache
install -d -m 0700 -o desktop -g desktop /home/desktop/.vnc

if [ -n "$DESKTOP_PW" ]; then
    printf 'desktop:%s\n' "$DESKTOP_PW" | chpasswd
    usermod -U desktop
else
    passwd -l desktop >/dev/null 2>&1 || true
    warn "DESKTOP_PW is unset; the desktop account stays locked (sudo still asks for a password)"
fi
if [ -n "$ROOT_PW" ]; then
    printf 'root:%s\n' "$ROOT_PW" | chpasswd
else
    passwd -l root >/dev/null 2>&1 || true
fi

# A lingering user manager gives us /run/user/1000 and a real session bus.
loginctl enable-linger desktop
install -d -m 0700 -o desktop -g desktop "/run/user/${DESKTOP_UID}"

# ---------------------------------------------------------------- application files
log "Installing application files"
install -d -m 0755 /opt/desktop/web /opt/desktop/applications /opt/desktop/tests \
    /usr/share/backgrounds /etc/cloud-desktop
install -m 0755 "$REPO_DIR"/scripts/*.sh /usr/local/bin/
install -m 0755 "$REPO_DIR/scripts/control-server.py" /usr/local/bin/control-server.py
install -m 0644 "$REPO_DIR"/web/*.html /opt/desktop/web/
install -m 0644 "$REPO_DIR/desktop/wallpaper.svg" /usr/share/backgrounds/ubuntu-cloud.svg
# The lean session paints the wallpaper once with feh, which needs a raster
# image; render it here instead of running a backdrop daemon.
rsvg-convert -w 1920 -h 1080 /usr/share/backgrounds/ubuntu-cloud.svg \
    -o /usr/share/backgrounds/desktop.png
install -m 0644 "$REPO_DIR"/desktop/applications/*.desktop /opt/desktop/applications/
install -m 0644 "$REPO_DIR"/tests/*.sh /opt/desktop/tests/ 2>/dev/null || true

# Chrome must honour CHROME_NO_SANDBOX and live at the expected path.
ln -sfn /usr/local/bin/google-chrome-wrapper.sh /usr/local/bin/google-chrome
ln -sfn /usr/local/bin/google-chrome-wrapper.sh /usr/local/bin/google-chrome-stable
ln -sfn /usr/local/bin/google-chrome-wrapper.sh /usr/local/bin/chromium
rm -f /usr/share/applications/org.freedesktop.systemd1.service 2>/dev/null || true

# ---------------------------------------------------------------- credentials and config
log "Writing KasmVNC configuration for ${VNC_USER}"
cat > "/etc/cloud-desktop/desktop.env" <<EOF
VNC_USER=${VNC_USER}
VNC_AUTH=${VNC_AUTH}
VNC_RESOLUTION=${VNC_RESOLUTION}
VNC_FRAME_RATE=${VNC_FRAME_RATE}
CONTROL_PORT=${CONTROL_PORT}
KASM_PORT=${KASM_PORT}
PUBLIC_DESKTOP=${PUBLIC_DESKTOP}
DESKTOP_HOSTNAME=${DESKTOP_HOSTNAME}
TZ=${TZ:-Africa/Cairo}
EOF
chmod 0640 /etc/cloud-desktop/desktop.env
chown root:desktop /etc/cloud-desktop/desktop.env

case "$VNC_RESOLUTION" in
    [0-9]*x[0-9]*) WIDTH="${VNC_RESOLUTION%x*}"; HEIGHT="${VNC_RESOLUTION#*x}" ;;
    *) WIDTH=1440; HEIGHT=900 ;;
esac

cat > /home/desktop/.vnc/kasmvnc.yaml <<EOF
desktop:
  resolution:
    width: $WIDTH
    height: $HEIGHT
  allow_resize: true
  pixel_depth: 24
encoding:
  max_frame_rate: ${VNC_FRAME_RATE}
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

rm -f /home/desktop/.kasmpasswd
printf '%s\n%s\n' "$VNC_PW" "$VNC_PW" \
    | runuser -u desktop -- env HOME=/home/desktop vncpasswd -u "$VNC_USER" -w
chown desktop:desktop /home/desktop/.kasmpasswd
chmod 0600 /home/desktop/.kasmpasswd

cp /opt/desktop/applications/*.desktop /home/desktop/Desktop/ 2>/dev/null || true
chmod 0755 /home/desktop/Desktop/*.desktop 2>/dev/null || true
chown -R desktop:desktop /home/desktop

# ---------------------------------------------------------------- real systemd units
log "Installing real systemd units"
install -m 0644 "$REPO_DIR"/vps/systemd/*.service "$REPO_DIR"/vps/systemd/*.target /etc/systemd/system/

systemctl daemon-reload
systemctl enable --now cloud-desktop.target

# ---------------------------------------------------------------- firewall hint
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    warn "ufw is active; allow the control port: ufw allow ${CONTROL_PORT}/tcp"
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
cat <<EOF

$(printf '\033[1;32m✓\033[0m') Cloud desktop installed with real systemd.

  Desktop   http://${IP:-<server-ip>}:${CONTROL_PORT}/
  Status    http://${IP:-<server-ip>}:${CONTROL_PORT}/status
  Health    http://${IP:-<server-ip>}:${CONTROL_PORT}/healthz

  VNC user  ${VNC_USER}
  VNC pass  (the VNC_PW you supplied)
  Linux     desktop${DESKTOP_PW:+ (DESKTOP_PW set)}

  systemctl status kasmvnc xfce control
  journalctl -u kasmvnc -u xfce -u control -f
  systemctl restart cloud-desktop.target

Put TLS in front of the control port (Caddy, nginx or a Cloudflare tunnel)
before exposing it to the internet.
EOF
