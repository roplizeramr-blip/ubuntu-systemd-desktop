# Architecture

## Processes

```
                     public :3000
                          │
                 ┌────────▼─────────┐
                 │ control-server.py│  root
                 │ status + proxy   │
                 └────────┬─────────┘
                          │ 127.0.0.1:6080
                 ┌────────▼─────────┐
                 │ Xvnc (KasmVNC)   │  desktop
                 │ HTTP + WebSocket │
                 └────────┬─────────┘
                          │ X11 :1
                 ┌────────▼─────────┐
                 │ XFCE session     │  desktop
                 │ xfwm4, panel, …  │
                 └──────────────────┘
```

supervisord is PID 1. It starts, in priority order: dbus, the journald shim, the
system-bus facade, earlyoom, KasmVNC, the user session bus, the user-bus facade,
XFCE, and finally the control server.

## Why the control server exists

KasmVNC binds to loopback so the desktop is never directly reachable. The
control server is the only listener on the public port. It serves:

| Path | Purpose |
|---|---|
| `/` | desktop (when `PUBLIC_DESKTOP=1`) or status page |
| `/status` | status page (when `PUBLIC_DESKTOP=1`) |
| `/api/status` | JSON: desktop state, credentials user, URLs |
| `/healthz` | `200 ok` when KasmVNC accepts connections |

When `PUBLIC_DESKTOP=1` every other request is reverse-proxied to
`127.0.0.1:6080`, including the WebSocket upgrade used by the VNC client. There
is no nginx, no websockify and no second container.

## Startup sequence

1. `entrypoint.sh` validates `VNC_PW`, generates `~/.vnc/kasmvnc.yaml`, writes
   the KasmVNC credential file with `vncpasswd`, applies `DESKTOP_PW`/`ROOT_PW`,
   prepares `/run` and execs supervisord.
2. KasmVNC starts Xvnc on `:1` with `-noxstartup`.
3. `start-xfce.sh` waits for the X socket, then starts `xfsettingsd`, `xfwm4`
   and `xfce4-panel` directly on the user session bus. `xfce4-session` and
   `xfdesktop` are not used; `setup-desktop.sh` paints the wallpaper once with
   `feh`.
4. `setup-desktop.sh` applies the wallpaper, disables compositing, animations,
   screensaver and power management, and starts Chrome on the welcome page.
5. The control server starts and `/healthz` flips to 200.

## systemd compatibility layer

`systemd/` contains a Python implementation of the CLI surface that desktop
software and users expect: `systemctl`, `journalctl`, `hostnamectl`,
`timedatectl`, `loginctl`, `systemd-analyze`, `systemd-detect-virt`, plus a
D-Bus facade for `org.freedesktop.systemd1`.

It maps unit names to Supervisor programs (`CORE_UNITS` in
`systemd/systemd_compat.py`), reads the unit files in `systemd/units/`, and
drives `supervisorctl` underneath. State is reported as systemd would report it
so that `systemctl is-system-running` returns `running`.

The facade is deliberately small and read-mostly. It does not emulate cgroups,
timers, socket activation or kernel units. It is not a security boundary.

## Storage and state

| Path | Contents |
|---|---|
| `/home/desktop` | user files, Chrome profile, XFCE config |
| `/home/desktop/.vnc` | `kasmvnc.yaml` and `.kasmpasswd` (generated at start) |
| `/var/log/supervisor` | per-program logs |
| `/run/systemd/journal` | shim journal socket |
| `/var/swap` | optional swap file |

Everything outside a mounted volume is lost when the container is recreated.

## Resource profile

- Image: ~1.1 GB (Ubuntu + XFCE + Chrome + KasmVNC).
- Idle: roughly 450–600 MB RSS for the full desktop.
- CPU: near zero when idle; KasmVNC uses the CPU only when the screen changes.
- No GPU: llvmpipe software rendering, Chrome runs with `--disable-gpu`.
