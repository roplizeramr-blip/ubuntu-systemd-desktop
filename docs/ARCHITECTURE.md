# Architecture

## What the container runs

Seven processes, nothing more:

```
┌────────── public port (PORT, nginx) ──────────────────────────────┐
│  auth_basic (VNC_USER / VNC_PW)                                   │
│  /            → vnc.html  (noVNC, autoconnect + reconnect)        │
│  /websockify  → WebSocket upgrade                                 │
│  /status      │                                                  │
│  /api/status  └─→ control-server.py (loopback, JSON + /healthz)   │
└───────────────────────────────────────────────────────────────────┘
        │ proxied to
┌─────── loopback ────────────────────────────────────────────────┐
│  6080  websockify   RFB ⇄ WebSocket, serves /usr/share/novnc    │
│  5901  Xtigervnc :1 X server, RFB only on 127.0.0.1             │
│         ├─ xfwm4, xfce4-panel, xfsettingsd (start-xfce.sh)      │
│         └─ the desktop user, one session bus                    │
└─────────────────────────────────────────────────────────────────┘
```

## Why this shape

**KasmVNC was removed.** One process that owns X, the web server and the encoder
looks simpler, but on two shared vCPUs the adaptive encoder is the most
expensive thing in the container, and behind it sat a Python reverse proxy that
owned the public socket. Two things followed: high CPU while the screen moved,
and a WebSocket that any hiccup in the proxy turned into a disconnect.

The stack now splits along the lines each component is actually good at:

- `Xtigervnc` is one C process: framebuffer + RFB. `-noreset` keeps the desktop
  alive when the last client goes away.
- `websockify` is the smallest RFB→WebSocket bridge, and `--heartbeat 20` keeps
  the connection warm through any proxy in front.
- noVNC decodes in the browser, so the server spends almost nothing on encoding.
- `nginx` is the only public listener. Its WebSocket proxying is not custom code,
  and `proxy_read_timeout 7d` means an idle desktop is never dropped.
- `control-server.py` now only computes: JSON and a health check. It never
  touches screen data and holds no long-lived socket.

Lean XFCE, no session manager (`xfce4-session`) and no backdrop daemon
(`xfdesktop`); the wallpaper is painted once with `feh`.

## Startup

1. `entrypoint.sh` validates `VNC_PW`, hashes it into `/etc/nginx/.htpasswd`
   with `openssl passwd -apr1`, renders `/etc/nginx/nginx.conf` from
   `scripts/nginx.conf.template`, applies `DESKTOP_PW`/`ROOT_PW`, then
   `nginx -t` — a broken config stops the container immediately rather than
   at the first request.
2. Supervisor starts, in priority order: session `dbus-daemon`, `Xtigervnc`,
   `websockify`, `start-xfce.sh`, `nginx`.
3. `start-xfce.sh` waits for the X socket (up to 30 s) before starting the panel
   and the window manager, so a restart never consumes its restart budget on a
   display that is not yet there.

## Files that matter

| Path | Role |
|---|---|
| `scripts/nginx.conf.template` | public listener, rendered at start |
| `scripts/start-vnc.sh` | Xtigervnc, `-noreset`, loopback RFB |
| `scripts/start-websockify.sh` | noVNC bundle + WebSocket bridge |
| `scripts/control-server.py` | `/api/status`, `/healthz`, `/status` |
| `scripts/start-xfce.sh` | lean XFCE session |
| `scripts/setup-desktop.sh` | xfconf prefs, wallpaper, Chrome defaults |
| `supervisor/supervisord.conf` | the seven programs |
| `vps/systemd/*.service` | the real units, used on a VPS |

| Generated file | Content |
|---|---|
| `/etc/nginx/.htpasswd` | `VNC_USER` + apr1 hash of `VNC_PW` |
| `/etc/nginx/nginx.conf` | rendered template |
| `/run/user/1000/bus` | session bus socket |

## The desktop session

XFCE is assembled by hand, and prefs come from `xfconf-query`:

- xfwm4: no compositor, no shadows, no snapping, one workspace, no snap frames.
- xsettings: `Adwaita-dark`, `Noto Sans 10`, no event sounds, no animations.
- Keyboard `us,ara`, `Alt+Shift` toggle; `xset s off -dpms`.
- Wallpaper `/usr/share/backgrounds/desktop.png` painted once by `feh`.
- Chrome autostarts once with the welcome page unless `CHROME_AUTOSTART=0`.

TigerVNC also honours RandR client resizes, so the browser can resize the
desktop instead of letterboxing it.

## Idle footprint

| Process | RSS (measured) |
|---|---|
| Xtigervnc | ~35 MB |
| websockify | ~30 MB |
| xfce4-panel | ~89 MB |
| xfwm4 | ~87 MB |
| xfsettingsd | ~76 MB |
| nginx | ~10 MB |
| control server | ~25 MB |
| dbus-daemon | ~5 MB |

Supervisor + earlyoom add the remainder; the whole container sits near 400 MB.

## Real systemd

The container has no init system beyond Supervisor, and it does not emulate one.
Therefore there are no `systemctl`/`journalctl` shims and no D-Bus facade; a
process that checks for `org.freedesktop.systemd1` simply does not find it.

Real systemd is the VPS path: `vps/install.sh` writes
`vps/systemd/{cloud-desktop.target,vnc,websockify,xfce,control}.service` into
`/etc/systemd/system/` and runs them under PID 1. See [`DEPLOY-VPS.md`](DEPLOY-VPS.md).
