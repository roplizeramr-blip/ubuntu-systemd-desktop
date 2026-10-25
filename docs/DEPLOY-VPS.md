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
- Outbound internet during install (APT, Google Chrome, KasmVNC).

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
| `VNC_USER` | `admin` | KasmVNC Basic Auth user |
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
2. installs XFCE, Chrome, KasmVNC 1.5.0 (SHA-256 pinned), Python and fonts;
3. creates the `desktop` user (uid 1000, `sudo`, `ssl-cert`);
4. writes `/etc/cloud-desktop/desktop.env` (mode 0640) and the KasmVNC
   credential file (mode 0600);
5. installs the units from `vps/systemd/` into `/etc/systemd/system/`;
6. enables `cloud-desktop.target`.

## What runs

```
cloud-desktop.target
├── kasmvnc.service   User=desktop  Xvnc on :1, HTTP on 127.0.0.1:6080
├── xfce.service      User=desktop  startxfce4 on :1
└── control.service   root          public port, status page, reverse proxy
```

Each unit uses `Restart=always`, `KillMode=control-group`, `PAMName=login` and
`XDG_RUNTIME_DIR=/run/user/1000`. `loginctl enable-linger desktop` gives the
user a real `systemd --user` instance and a session bus at
`/run/user/1000/bus`, which XFCE and KasmVNC expect.

## Verify

```bash
systemctl status cloud-desktop.target
systemctl status kasmvnc xfce control
journalctl -u kasmvnc -u xfce -u control -n 50 --no-pager
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

KasmVNC's WebSocket upgrade is forwarded by Caddy without extra configuration.
With nginx add `proxy_set_header Upgrade $http_upgrade;` and
`proxy_set_header Connection "upgrade";`.

## Operations

```bash
systemctl restart cloud-desktop.target   # restart the whole desktop
systemctl restart xfce                   # restart only the session
systemctl stop cloud-desktop.target      # stop everything
journalctl -u control -f                 # follow one unit
```

Credentials live in `/etc/cloud-desktop/desktop.env` (KasmVNC user and
resolution) and `/home/desktop/.kasmpasswd` (hashed password). To change the
VNC password, rerun the credential step:

```bash
read -rsp 'New VNC password: ' P; echo
printf '%s\n%s\n' "$P" "$P" | runuser -u desktop -- env HOME=/home/desktop \
    vncpasswd -u admin -w
systemctl restart kasmvnc
```

## Updating

```bash
cd ubuntu-systemd-desktop && git pull
./vps/install.sh          # idempotent: packages, files, units, then restart
```

The installer keeps `/etc/cloud-desktop/desktop.env` and
`/home/desktop/.kasmpasswd` unless you pass `VNC_PW` again.

## Notes

- This path was written for and reviewed against Ubuntu 24.04, but it has not been
  executed on a live VPS in this repository's CI. Run it on a disposable
  machine first and report failures.
- The container image and this native install share the same `scripts/` and
  `web/` directories; only the supervision layer differs.
- On a VPS you can safely delete `systemd/` (the compatibility layer) — it is
  only used by the container image.
