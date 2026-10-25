# Testing

## Inside the container

```bash
docker compose exec desktop /opt/desktop/tests/smoke-test.sh
docker compose exec desktop /opt/desktop/tests/test-systemd.sh
docker compose exec desktop /opt/desktop/tests/validate.sh
```

`smoke-test.sh` checks the parts that actually matter:

| Area | Checks |
|---|---|
| systemd facade | `systemctl` works, `is-system-running` = running, units active |
| KasmVNC | Xvnc runs, config and credentials exist, loopback answers 401 |
| Control plane | `/healthz`, `/` page, `/api/status` JSON |
| Removed stack | no `tailscale`, no `tailscaled`, no `~/.hermes` |
| Legacy stack | no noVNC, no TigerVNC package |
| Helpers | earlyoom, dashboard launcher, swap helper |

## From the host

```bash
curl -fsS http://localhost:3000/healthz
curl -fsS http://localhost:3000/api/status | python3 -m json.tool
curl -sI  http://localhost:3000/          # 200 + text/html
```

With `PUBLIC_DESKTOP=1`:

```bash
curl -sI http://localhost:3000/           # 401 (KasmVNC Basic Auth)
curl -sI http://localhost:3000/status     # 200 (control page)
curl -fsS http://localhost:3000/healthz   # 200 ok
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
      "running": true, "engine": "KasmVNC", "host": "127.0.0.1",
      "port": 6080, "auth": true, "user": "admin", "resolution": "1440x900"
    },
    "urls": { "open": "https://app.example/", "public": "https://app.example/" }
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

## Removing the facade

On a real VPS the facade does not exist. Instead:

```bash
systemctl status cloud-desktop.target
systemctl is-system-running
journalctl -u kasmvnc -u xfce -u control -n 30 --no-pager
```

See [`DEPLOY-VPS.md`](DEPLOY-VPS.md).

## Rebuilding

```bash
docker compose build --no-cache
docker compose up -d
docker compose logs -f desktop
```
