# Deploy on a container platform

The `Dockerfile` builds a self-contained image: TigerVNC (X server) + websockify
+ noVNC (browser client) + XFCE, with nginx as the single public entry.

The instructions below apply to CranL, and to anything else that builds a
Dockerfile and forwards one HTTP port (Render, Fly.io, Railway, a VPS with
`docker compose up -d`).

## 1. Push

```bash
git add -A
git commit -m "Lean stack: TigerVNC + noVNC behind nginx"
git push
```

## 2. Create the app

| Field | Value |
|---|---|
| Repository | `roplizeramr-blip/ubuntu-systemd-desktop` |
| Build | `Dockerfile` |
| Port | `3000` |
| Health check | `/healthz` |

The platform sets `PORT`; everything else is loopback-only (`WS_PORT`,
`VNC_PORT`) or internal (`CONTROL_PORT`). Never publish those other ports.

## 3. Environment

| Name | Value |
|---|---|
| `PORT` | `3000` (public, nginx) |
| `CONTROL_PORT` | `3001` (internal) |
| `VNC_USER` | `admin` |
| `VNC_PW` | a strong password, min 6 chars |
| `VNC_AUTH` | `1` |
| `VNC_RESOLUTION` | `1280x720` |
| `PUBLIC_DESKTOP` | `1` |
| `DESKTOP_PW` | Linux password for `desktop` |
| `ROOT_PW` | optional |
| `TZ` | `Africa/Cairo` |
| `CHROME_AUTOSTART` | `1` |
| `SWAP_ENABLED` | `0` |

`VNC_PW` becomes the nginx Basic Auth password at container start
(`openssl passwd -apr1`). Nothing is baked into the image.

Do not add `TS_*` or `DEEPSEEK_API_KEY`: those integrations were removed.
`VNC_FRAME_RATE`, `KASM_*` and `SYSTEMD_DETECT_VIRT` are gone too.

## 4. Deploy

Press **Deploy** and wait for the build. A `git push` alone builds the image but
does **not** replace the running container on some platforms — always deploy
afterwards.

## 5. Verify

```bash
curl -sI  https://<app>/healthz        # 200
curl -s   https://<app>/status         # the status page
curl -sI  https://<app>/               # 401 without credentials
curl -sI  -u admin:<pw> https://<app>/ # 200 once noVNC answers
```

Open `https://<app>/` → the browser asks for `VNC_USER` / `VNC_PW`, then noVNC
auto-connects.

| Path | Serves |
|---|---|
| `/` | noVNC desktop (auth) |
| `/status` | status page |
| `/api/status` | JSON status |
| `/healthz` | health probe |

## 6. Inside the container

```bash
supervisorctl status
/opt/desktop/tests/smoke-test.sh
tail -f /var/log/supervisor/nginx.log
tail -f /var/log/supervisor/vnc.log
tail -f /var/log/supervisor/websockify.log
```

Seven programs should be `RUNNING`: `dbus`, `earlyoom`, `nginx`, `vnc`,
`websockify`, `control`, `xfce`.

## Troubleshooting

- **502 from `/`** — websockify or Xtigervnc is still starting; check
  `vnc.log` and `websockify.log`.
- **Page loads but VNC does not connect** — confirm `PUBLIC_DESKTOP=1`, and that
  the platform port equals `PORT` (3000). The WebSocket is proxied by nginx,
  so there is nothing else in the path to misconfigure.
- **`/healthz` stays 503** — `Xtigervnc` is not accepting connections on
  `127.0.0.1:5901`; look for a stale `/tmp/.X1-lock`.
- **Out of memory** — enable `SWAP_ENABLED=1`, or drop `VNC_RESOLUTION` to
  `1024x768`. The whole stack idles near 400 MB.
