# Ubuntu Cloud Desktop — KasmVNC + XFCE

A small, fast Ubuntu 24.04 desktop you can use from a browser. KasmVNC streams a
real XFCE X11 session over HTTPS; a tiny Python control server owns the public
port, serves a status page and reverse-proxies HTTP and WebSocket traffic to
KasmVNC on loopback.

No Tailscale. No agent framework. No extra web server.

## Stack

| Layer | Choice | Why |
|---|---|---|
| Base | Ubuntu 24.04 LTS (noble) | current stable, small |
| Desktop | XFCE 4 | full session, low memory |
| Remote | KasmVNC 1.5.0 | one HTTP/WebSocket server, adaptive encoding |
| Browser | Google Chrome | from Google's APT repository |
| Runtime | Python 3.15 + Node.js 24 LTS | pinned, checksum-verified |
| Supervisor | supervisord | PID 1 inside the container |
| Public port | `control-server.py` | status page + reverse proxy |

## Features

- 1440x900 XFCE desktop at 30 fps by default, resizeable from the client.
- KasmVNC listens on `127.0.0.1:6080` only; the public port is the only entry.
- Chrome, a terminal, Thunar, a screenshot tool and a task manager.
- Arabic + US keyboard layouts (`Alt+Shift` to switch).
- Wallpaper-only desktop (no backdrop icons) to cut repaints. Set
  `DESKTOP_ICONS=1` to restore them.
- Optional swap file, off by default.
- A systemd-compatible CLI layer (`systemctl`, `journalctl`, `hostnamectl`, …)
  backed by Supervisor, so desktop software that checks those interfaces works.

## Quick start (Docker)

```bash
git clone https://github.com/roplizeramr-blip/ubuntu-systemd-desktop.git
cd ubuntu-systemd-desktop
cp .env.example .env          # then edit VNC_PW
docker compose up -d --build
```

Open <http://localhost:3000>. With `PUBLIC_DESKTOP=1`, `/` is the desktop and
the status page moves to `/status`.

## Access model

`PUBLIC_DESKTOP=1` (what the hosted app uses):

- `https://<app-url>/` → KasmVNC, protected by HTTP Basic Auth
  (`VNC_USER` / `VNC_PW`).
- `https://<app-url>/status` → the lightweight status page.
- `https://<app-url>/healthz` → `200 ok` once KasmVNC is accepting connections.

`PUBLIC_DESKTOP=0` (default for local Docker):

- `/` serves the status page only; the desktop is not published.
- Reach KasmVNC from the host with `docker compose exec desktop curl ...` or by
  adding a port mapping for `6080` yourself.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `VNC_PW` | — (required) | KasmVNC Basic Auth password, min 6 chars |
| `VNC_USER` | `admin` | KasmVNC Basic Auth user |
| `VNC_AUTH` | `1` | `0` disables HTTP Basic Auth |
| `VNC_RESOLUTION` | `1440x900` | desktop geometry |
| `VNC_FRAME_RATE` | `30` | max frames per second |
| `CONTROL_PORT` | `3000` | public port |
| `PUBLIC_DESKTOP` | `0` | `1` publishes the desktop at `/` |
| `DESKTOP_PW` | — | Linux password for `desktop` |
| `ROOT_PW` | — | root password (otherwise locked) |
| `DESKTOP_ICONS` | `0` | `1` restores backdrop icons |
| `CHROME_AUTOSTART` | `1` | `0` skips the welcome page on login |
| `CHROME_NO_SANDBOX` | `1` | container-friendly Chrome flags |
| `SWAP_ENABLED` | `0` | create a swap file at start |
| `SWAP_SIZE_MB` | `6144` | swap file size |
| `TZ` | `Africa/Cairo` | container timezone |
| `HIDE_CONTAINER` | `0` | remove `/.dockerenv` (cosmetic) |

Never commit `VNC_PW`, `DESKTOP_PW` or `ROOT_PW`. The image contains no
credentials; the entrypoint applies them at container start.

## systemd compatibility

The container has no init system: supervisord is PID 1. The `systemd/` directory
ships a compatibility layer that maps the usual systemd commands onto Supervisor
programs:

```bash
systemctl status
systemctl is-system-running
systemctl list-units --type=service
systemctl restart xfce
journalctl -n 30
hostnamectl
systemd-analyze blame
```

This is a facade, not real systemd. It exists so applications that probe the
standard interfaces behave normally. It is not a security boundary, and it
cannot manage kernel units, cgroups or timers.

**Real systemd requires a VM or a VPS.** See [`docs/DEPLOY-VPS.md`](docs/DEPLOY-VPS.md)
for the native path where systemd is PID 1 and the units in `systemd/units/` are
real unit files.

## Deploy on CranL

1. Push the repository to GitHub.
2. In CranL create an app from the repository; the build uses `Dockerfile`.
3. Set the port to `3000`, health check `/healthz`, region `netherlands-1`.
4. Add the environment variables you need (`VNC_PW`, `DESKTOP_PW`, `ROOT_PW`,
   `PUBLIC_DESKTOP=1`, …).
5. Deploy, then open the app URL.

A `git push` triggers a build but does not swap the running container; run a
deploy after the build finishes.

Full details: [`docs/DEPLOY-CRANL.md`](docs/DEPLOY-CRANL.md).

## Testing

```bash
docker compose exec desktop /opt/desktop/tests/smoke-test.sh
docker compose exec desktop /opt/desktop/tests/test-systemd.sh
```

Or from the host:

```bash
curl -fsS http://localhost:3000/healthz
curl -fsS http://localhost:3000/api/status | python3 -m json.tool
```

See [`docs/TESTING.md`](docs/TESTING.md).

## Project layout

```
Dockerfile              image build
docker-compose.yml      local run
scripts/
  entrypoint.sh         runtime setup, credentials, kasmvnc.yaml
  control-server.py     public port: status page + reverse proxy
  start-kasmvnc.sh      KasmVNC/Xvnc lifecycle
  start-xfce.sh         XFCE session
  setup-desktop.sh      XFCE, GTK and Chrome preferences
  system-status.sh      terminal dashboard
  healthcheck.sh        container health probe
supervisor/             supervisord programs
systemd/                compatibility layer and unit files
web/                    status page and welcome page
desktop/                wallpaper and launchers
tests/                  smoke and systemd tests
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
- `PUBLIC_DESKTOP=1` exposes KasmVNC to the internet behind Basic Auth only;
  treat the URL itself as a secret and rotate the password if it leaks.

## License

MIT. See `LICENSE`.
