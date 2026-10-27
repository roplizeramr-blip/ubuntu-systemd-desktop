# Testing

## Inside the container

```bash
docker compose exec desktop /opt/desktop/tests/validate.sh
docker compose exec desktop /opt/desktop/tests/smoke-test.sh
```

`smoke-test.sh` checks the parts that actually matter:

| Area | Checks |
|---|---|
| Software | Chrome, noVNC bundle, websockify, Xtigervnc, nginx |
| Processes | Xtigervnc, websockify, nginx, panel, WM, settings daemon, session bus |
| Lean session | no `xfce4-session`, no `xfdesktop` |
| Desktop | wallpaper raster present, WM owns the root window |
| Control plane | `/healthz`, `/status` page, `/api/status` JSON, WebSocket `101` |
| Removed stack | no `tailscale`, no `tailscaled`, no `~/.hermes`, no KasmVNC |
| Helpers | earlyoom, dashboard launcher, swap helper |

## From the host

```bash
curl -fsS http://localhost:3000/healthz
curl -sI  http://localhost:3000/vnc.html  # 401 without credentials
curl -fsS http://localhost:3000/status
```

With `PUBLIC_DESKTOP=1` (the default):

```bash
curl -sI http://localhost:3000/           # 401 (nginx Basic Auth)
curl -sI http://localhost:3000/status     # 200 (status page)
curl -fsS http://localhost:3000/healthz   # 200 ok
```

The internal control server is on `CONTROL_PORT` (3001) and answers the same
JSON — use it when you want the status without the auth prompt:

```bash
curl -fsS http://127.0.0.1:3001/api/status | python3 -m json.tool
```

Expected `/api/status` shape:

```json
{
  "ok": true,
  "status": {
    "ready": true,
    "public": true,
    "uptime": 42,
    "hostname": "ubuntu-desktop",
    "desktop": {
      "running": true,
      "engine": "TigerVNC + noVNC",
      "resolution": "1280x720",
      "url": "/"
    },
    "urls": { "open": "/", "status": "/status", "health": "/healthz" }
  }
}
```

There is no `tailscale` key: that integration was removed.

## Desktop session

```bash
runuser -u desktop -- env HOME=/home/desktop DISPLAY=:1 \
  XDG_RUNTIME_DIR=/run/user/1000 \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
  xfconf-query -c xfce4-desktop -p /desktop-icons/style
```

Expect `0`. The lean session no longer runs a backdrop daemon, so
`pgrep -x xfdesktop` returns nothing and `xfce4-session` is absent.

```bash
runuser -u desktop -- env HOME=/home/desktop DISPLAY=:1 \
  XDG_RUNTIME_DIR=/run/user/1000 \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
  xfconf-query -c xfwm4 -p /general/use_compositing
```

Expect `false`: no compositor on a software-rendered desktop.

## On a real VPS

The container has no systemd facade; on a VPS the units are real:

```bash
systemctl status cloud-desktop.target
systemctl is-system-running
journalctl -u vnc -u websockify -u xfce -u control -n 30 --no-pager
```

See [`DEPLOY-VPS.md`](DEPLOY-VPS.md).

## Rebuilding

```bash
docker compose build --no-cache
docker compose up -d
docker compose logs -f desktop
```
