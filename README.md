# Ubuntu Cloud Desktop — TigerVNC + noVNC + XFCE

A small, fast Ubuntu 24.04 desktop you can use from a browser. TigerVNC provides
the X server, noVNC renders it in the browser, and nginx is the only public
listener: it terminates HTTP Basic Auth, serves the status page and proxies the
WebSocket to the desktop.

No Tailscale. No agent framework. No KasmVNC. No Python socket pump in the
WebSocket path.

## Stack

| Layer | Choice | Why |
|---|---|---|
| Base | Ubuntu 24.04 (trixie) | current stable, small |
| Desktop | XFCE 4 | full session, low memory |
| X server | TigerVNC (`Xtigervnc`) | single process, `-noreset`, loopback RFB |
| Web client | noVNC + websockify | JPEG decoding in the browser, tiny server load |
| Public port | nginx | auth, status page, native WebSocket proxy |
| Browser | Google Chrome | from Google's APT repository |
| Runtime | Python 3 (system) | nothing else is pulled in |
| Supervisor | supervisord | PID 1 inside the container, 7 programs |

## What changed, and why

KasmVNC was removed. It is one process doing X server, web server and adaptive
encoding, which sounds lighter, but the encoder is the most expensive part on a
container with two shared vCPUs. TigerVNC does the cheap encodings, the browser
does the rest, and nginx owns the public socket —
looking like a dead WebSocket and the connection stops dropping.

Seven processes run under Supervisor:

```
dbus         session bus (GTK, Chrome, Thunar)
vnc          Xtigervnc          :1  RFB on 127.0.0.1:5901
websockify   RFB -> WebSocket   127.0.0.1:6080 + serves the noVNC bundle
xfce         xfwm4 + xfce4-panel + xfsettingsd (no session manager)
nginx        public port: auth, /status, WebSocket proxy
control      /healthz and /api/status
earlyoom     protects the 2 GiB cgroup
```

What is gone: the KasmVNC encoder, the Python reverse proxy, the `systemd`
facade daemons (journald, D-Bus activation, user-bus bridge — about 100 MB and
a permanent D-Bus wake-up load) and the `systemd`, Node.js and Python 3.15
packages.

## Features

- 1280x720 XFCE desktop, low noVNC quality settings, auto-resize from the client.
- Desktop socket on `127.0.0.1:5901` only; `nginx` is the single public entry.
- `Xtigervnc -noreset`: a laptop that sleeps, a tab that reloads, a laptop that
  changes network — none of them kill the desktop session.
- `websockify --heartbeat 20` and `proxy_read_timeout 7d`: no idle disconnects.
- Chrome, a terminal, Thunar, a screenshot tool and a task manager.
- Arabic + US keyboard layouts (`Alt+Shift` to switch).
- Lean session: `xfwm4`, `xfsettingsd` and `xfce4-panel` are started directly.
  There is no `xfce4-session` and no `xfdesktop`; the wallpaper is painted once
  with `feh`, so nothing keeps repainting the root window.
- Optional swap file, off by default.

## Quick start (Docker)

```bash
git clone https://github.com/roplizeramr-blip/ubuntu-systemd-desktop.git
cd ubuntu-systemd-desktop
cp .env.example .env          # then edit VNC_PW
docker compose up -d --build
```

Open <http://localhost:3000>. With `PUBLIC_DESKTOP=1` (the default), `/` opens
the desktop and the status page moves to `/status`.

## Access model

`PUBLIC_DESKTOP=1`:

- `https://<url>/` → noVNC, protected by HTTP Basic Auth (`VNC_USER` / `VNC_PW`),
  opening with `autoconnect=1`, `reconnect=1`, `quality=5`.
- `https://<url>/status` → the lightweight status page.
- `https://<url>/healthz` → `200 ok` while the desktop is reachable.
- `https://<url>/api/status` → JSON snapshot.

`PUBLIC_DESKTOP=0`:

- `/` serves the status page only; the desktop lives at `/vnc.html`.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `VNC_PW` | — (required) | HTTP Basic Auth password for `/`, min 6 chars |
| `VNC_USER` | `admin` | HTTP Basic Auth user |
| `VNC_AUTH` | `1` | `0` allows an unauthenticated read of the public port |
| `VNC_RESOLUTION` | `1280x720` | desktop geometry |
| `PORT` | `3000` | public port (nginx) |
| `CONTROL_PORT` | `3001` | internal control server (loopback) |
| `WS_PORT` | `6080` | websockify listener (loopback) |
| `VNC_PORT` | `5901` | TigerVNC RFB listener (loopback) |
| `PUBLIC_DESKTOP` | `1` | `1` publishes the desktop at `/` |
| `DESKTOP_PW` | — | Linux password for `desktop` |
| `ROOT_PW` | — | root password (otherwise locked) |
| `CHROME_AUTOSTART` | `1` | `0` skips the welcome page on login |
| `CHROME_NO_SANDBOX` | `1` | container-friendly Chrome flags |
| `SWAP_ENABLED` | `0` | create a swap file at start |
| `SWAP_SIZE_MB` | `6144` | swap file size |
| `TZ` | `Africa/Cairo` | container timezone |
| `HIDE_CONTAINER` | `0` | remove `/.dockerenv` (cosmetic) |

Never commit `VNC_PW`, `DESKTOP_PW` or `ROOT_PW`. The image contains no
credentials; the entrypoint applies them at container start.

## Real systemd

The container runs Supervisor as PID 1 and does **not** emulate systemd. For
real systemd — units enabled with `systemctl enable`, `systemctl status`,
`journalctl` — use the VPS path:

```bash
VNC_PW='strong-password' PUBLIC_DESKTOP=1 ./vps/install.sh
```

See [`docs/DEPLOY-VPS.md`](docs/DEPLOY-VPS.md).

## Deploy anywhere Docker runs

1. Push the repository to GitHub.
2. Build the image (or use `docker compose build`).
3. Publish port `3000`, set a health check on `/healthz`.
4. Set the environment variables you need (`VNC_PW`, `PUBLIC_DESKTOP=1`, …).
5. Open the app URL.

## Testing

```bash
docker compose exec desktop /opt/desktop/tests/smoke-test.sh
curl -fsS http://localhost:3000/healthz
curl -fsS http://localhost:3000/api/status | python3 -m json.tool
```

See [`docs/TESTING.md`](docs/TESTING.md).

## Project layout

```
Dockerfile              image build
docker-compose.yml      local run
scripts/
  entrypoint.sh         runtime setup, credentials, nginx configuration
  control-server.py     internal status page + JSON API (no proxying)
  start-vnc.sh          Xtigervnc lifecycle
  start-websockify.sh   RFB -> WebSocket bridge
  start-xfce.sh         lean XFCE session
  setup-desktop.sh      XFCE, GTK and Chrome preferences
  nginx.conf.template   public listener, rendered at start
  system-status.sh      terminal dashboard
  healthcheck.sh        container health probe
supervisor/             supervisord programs (7)
vps/                    native install + real systemd units
web/                    status page and welcome page
desktop/                wallpaper and launchers
tests/                  smoke test
docs/                   deployment, architecture, testing
```

## Known limitations

- No GPU: Mesa runs on llvmpipe, Chrome is started with `--disable-gpu`.
- No real systemd inside the container (see above).
- Files live in the container filesystem and are lost on redeploy.
- Audio is not configured.

## Security

- Put a strong `VNC_PW` on every public deployment.
- Keep the app URL private; it is the only entry point.
- Do not store secrets in the desktop home directory.
- `PUBLIC_DESKTOP=1` exposes the desktop behind Basic Auth only; treat the URL
  itself as a secret, rotate the password if it leaks, and put TLS in front of
  it for anything more than a test.

## License

MIT. See `LICENSE`.
