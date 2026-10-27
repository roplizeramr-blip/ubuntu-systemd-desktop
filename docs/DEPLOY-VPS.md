# Deploy on a clean Ubuntu 24.04 VPS (real systemd)

This is the **real systemd** path. On a VPS, systemd is PID 1, the units in
`vps/systemd/` are genuine unit files, and there is no Supervisor and no
`systemctl` facade anywhere on the machine.

Use it when you need real `systemctl`, `journalctl`, cgroups, timers and
`systemd --user`, or when you want the desktop to survive reboots as a normal
service stack.

## Requirements

- Ubuntu 24.04 LTS (noble), clean install, root access.
- 2 vCPU, 2 GB RAM, 20 GB disk minimum. 4 GB RAM is comfortable with Chrome.
- Outbound internet during install (APT, Google Chrome, TigerVNC + noVNC).

## Install

```bash
apt-get update && apt-get -y install git
git clone https://github.com/roplizeramr-blip/ubuntu-systemd-desktop.git
cd ubuntu-systemd-desktop

VNC_PW='a-strong-password' \
DESKTOP_PW='another-strong-password' \
PUBLIC_DESKTOP=1 \
./vps/install.sh
```

Optional variables:

| Variable | Default | Meaning |
|---|---|---|
| `VNC_USER` | `admin` | HTTP Basic Auth user |
| `VNC_PW` | — | required, min 6 chars |
| `VNC_RESOLUTION` | `1440x900` | desktop geometry |
| `VNC_FRAME_RATE` | `30` | max fps |
| `CONTROL_PORT` | `3000` | public port |
| `PUBLIC_DESKTOP` | `1` | publish the desktop at `/` |
| `DESKTOP_PW` | — | Linux password for `desktop` |
| `ROOT_PW` | — | root password (otherwise locked) |
| `DESKTOP_HOSTNAME` | current hostname | used on the status page |

The installer:

1. verifies systemd is PID 1 and the release is Ubuntu 24.04;
2. installs XFCE, Chrome, TigerVNC + noVNC + nginx, Python and fonts;
3. creates the `desktop` user (uid 1000, `sudo`, `ssl-cert`);
4. writes /etc/cloud-desktop/desktop.env (mode 0640) and the nginx
   credential file (mode 0600);
5. installs the units from `vps/systemd/` into `/etc/systemd/system/`;
6. enables `cloud-desktop.target`.

## What runs

```
cloud-desktop.target
 vnc.service        User=desktop  Xtigervnc :1, RFB on 127.0.0.1:5901
 websockify.service User=desktop  noVNC bundle + WebSocket on 127.0.0.1:6080
 xfce.service       User=desktop  start-xfce.sh on :1 (xfwm4 + panel)
 nginx.service      root          public port: auth, status page, WebSocket proxy
 control.service    root          /api/status and /healthz on loopback

Each unit uses `Restart=always`, `KillMode=control-group`, `PAMName=login` and
`XDG_RUNTIME_DIR=/run/user/1000`. `loginctl enable-linger desktop` gives the
user a real `systemd --user` instance and a session bus at
/run/user/1000/bus, which XFCE and noVNC expect.

## Verify

```bash
systemctl status cloud-desktop.target
systemctl status vnc websockify xfce control nginx
journalctl -u vnc -u websockify -u xfce -u control -n 50 --no-pager
systemctl is-system-running            # running
systemctl --user -M desktop@ status    # real user manager
```

```bash
curl -sI http://127.0.0.1:3000/healthz
curl -s  http://127.0.0.1:3000/api/status
```

Open `http://<server-ip>:3000/` and sign in with `VNC_USER` / `VNC_PW`.

## TLS

The control port is plain HTTP. Put a TLS terminator in front of it:

**Caddy** (automatic certificates):

```bash
apt-get install -y caddy
cat > /etc/caddy/Caddyfile <<'EOF'
desktop.example.com {
    reverse_proxy 127.0.0.1:3000
}
EOF
systemctl reload caddy
```

nginx already handles the WebSocket upgrade; Caddy forwards it unchanged.
With nginx add `proxy_set_header Upgrade $http_upgrade;` and
`proxy_set_header Connection "upgrade";`.

## Operations

```bash
systemctl restart cloud-desktop.target   # restart the whole desktop
systemctl restart xfce                   # restart only the session
systemctl stop cloud-desktop.target      # stop everything
journalctl -u control -f                 # follow one unit
```

Credentials live in /etc/cloud-desktop/desktop.env (VNC user and
resolution) and /etc/nginx/.htpasswd (apr1 hash of the password). To
change it, rebuild the entry with openssl passwd -apr1:

```bash
read -rsp 'New VNC password: ' P; echo
printf "%s:%s\n" "${VNC_USER:-admin}" "$(openssl passwd -apr1 "$P")" \> /etc/nginx/.htpasswd
chmod 640 /etc/nginx/.htpasswd && chown root:www-data /etc/nginx/.htpasswd
systemctl reload nginx
```

## Updating

```bash
cd ubuntu-systemd-desktop && git pull
./vps/install.sh          # idempotent: packages, files, units, then restart
```

The installer keeps `/etc/cloud-desktop/desktop.env` and
/etc/nginx/.htpasswd unless you pass VNC_PW again.

## Notes

- This path was written for and reviewed against Ubuntu 24.04, but it has not been
  executed on a live VPS in this repository's CI. Run it on a disposable
  machine first and report failures.
- The container image and this native install share the same `scripts/` and
  `web/` directories; only the supervision layer differs.
- On a VPS you can safely delete `systemd/` (the compatibility layer) — it is
  only used by the container image.
