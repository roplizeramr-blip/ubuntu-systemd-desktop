# Deploy on CranL

CranL builds the `Dockerfile` from the repository and runs the container behind
HTTPS on `*.cranl.net`.

## 1. Push

```bash
git add -A
git commit -m "Remove Tailscale and Hermes; lighter control page"
git push
```

## 2. Create the app

In CranL → **Create app** → **From repository**:

| Field | Value |
|---|---|
| Repository | `roplizeramr-blip/ubuntu-systemd-desktop` |
| Build | `Dockerfile` |
| Port | `3000` |
| Health check | `/healthz` |
| Region | `netherlands-1` |

## 3. Environment

| Name | Value |
|---|---|
| `CONTROL_PORT` | `3000` |
| `VNC_USER` | `admin` |
| `VNC_PW` | a strong password, min 6 chars |
| `VNC_AUTH` | `1` |
| `VNC_RESOLUTION` | `1440x900` |
| `VNC_FRAME_RATE` | `30` |
| `PUBLIC_DESKTOP` | `1` |
| `DESKTOP_PW` | Linux password for `desktop` |
| `ROOT_PW` | optional |
| `TZ` | `Africa/Cairo` |
| `CHROME_AUTOSTART` | `1` |
| `SWAP_ENABLED` | `0` |

Do not add `TS_*` or `DEEPSEEK_API_KEY`: those integrations were removed.

## 4. Deploy

Press **Deploy** and wait for the build. A `git push` alone builds the image but
does **not** replace the running container — always deploy afterwards.

## 5. Verify

```bash
curl -sI  https://<app>.cranl.net/healthz       # 200
curl -s   https://<app>.cranl.net/api/status    # JSON, no "tailscale" key
```

Open `https://<app>.cranl.net/` → sign in with `VNC_USER` / `VNC_PW`.

| Path | Serves |
|---|---|
| `/` | KasmVNC desktop |
| `/status` | control page |
| `/api/status` | JSON status |
| `/healthz` | health probe |

## 6. Inside the container

```bash
supervisorctl status
/opt/desktop/tests/smoke-test.sh
tail -f /var/log/supervisor/kasmvnc.log
```

## Troubleshooting

- **502 from `/`** — KasmVNC is still starting; check
  `/var/log/supervisor/kasmvnc.err.log`.
- **Page loads but VNC does not connect** — the proxy needs the WebSocket
  upgrade; confirm `PUBLIC_DESKTOP=1` and that port 3000 is the app port.
- **`/healthz` stays 503** — Xvnc is not accepting connections on
  `127.0.0.1:6080`; look for a stale `:1` lock file in `/tmp`.
- **Out of memory** — enable `SWAP_ENABLED=1`, or reduce
  `VNC_RESOLUTION`/`VNC_FRAME_RATE`.
