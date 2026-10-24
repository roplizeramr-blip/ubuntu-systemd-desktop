# Deploying on CranL

CranL runs the container and exposes HTTPS on a `*.cranl.net` domain. The app
must listen on the configured port and bind `0.0.0.0`. This image listens on
`CONTROL_PORT` (default `3000`) with the public control page; KasmVNC stays on
loopback `127.0.0.1:6080` and is reachable only through Tailscale.

## Option A — CranL builds the Dockerfile from GitHub

1. Push this repository to GitHub (public is simplest).
2. In CranL, create an app:
   - Source: GitHub repository (or a public Git URL)
   - Build: Dockerfile
   - Port: `3000`
   - Health check path: `/healthz`
   - Region: `netherlands-1` (free plan)
3. Add the environment variables below in the private app settings.
4. Deploy. The first build installs Ubuntu, Chrome, KasmVNC and Tailscale, so
   it takes longer than a rebuild of the old noVNC image.
5. Open the `*.cranl.net` URL after the health check passes.

## Option B — Build the image in GitHub Actions

`.github/workflows/build.yml` builds `linux/amd64` and publishes:

```
ghcr.io/<owner>/ubuntu-systemd-desktop:latest
ghcr.io/<owner>/ubuntu-systemd-desktop:sha-<commit>
```

Make the GHCR package public, or give CranL a GitHub PAT with `read:packages`.
Then create the app with the image and port `3000` / health `/healthz`.

## Required environment

| Key | Value |
|---|---|
| `CONTROL_PORT` | `3000` |
| `VNC_PW` | a strong unique password (minimum 6 characters) |
| `DESKTOP_PW` | optional strong Linux password for `desktop` |
| `VNC_RESOLUTION` | `1440x900` |
| `VNC_FRAME_RATE` | `30` |
| `DESKTOP_ICONS` | optional; `1` restores the desktop backdrop icons |
| `VNC_USER` | `desktop` |
| `VNC_AUTH` | `1` |
| `TZ` | `Africa/Cairo` |
| `TS_HOSTNAME` | `ubuntu-desktop` (or a unique tailnet name) |

Do **not** set `KASM_PORT` to anything other than the internal default unless
you also update the Serve helper and the control server. Do not expose `6080`
in CranL.

Optional:

| Key | Value | Purpose |
|---|---|---|
| `TS_AUTHKEY` | Tailscale auth key | Automatic tailnet join without a browser login. Keep it private. |
| `TS_TUN` | `userspace` | Keep userspace on CranL; use `kernel` only with a usable TUN device and NET_ADMIN. |
| `TS_SERVE_HTTPS_PORT` | `443` | Tailnet HTTPS port for the MagicDNS link used by **Open VNC**. |
| `TS_SERVE_HTTP_PORT` | `0` | Raw-TCP fallback port on the Tailscale IP; `0` keeps the IP free. |
| `TS_SERVE_FALLBACK_TCP` | `0` | If `1`, enable the raw-TCP rule automatically when HTTPS fails. |
| `TS_FUNNEL` | `0` | If `1`, publish the desktop to the public internet with `tailscale funnel` instead of the tailnet-only `serve`. |
| `PUBLIC_DESKTOP` | `0` | If `1`, the public CranL URL opens KasmVNC at `/` and the control page moves to `/status`. |
| `SWAP_ENABLED` | `0` | Leave off on CranL; enable on a VPS if needed. |
| `HIDE_CONTAINER` | `0` | Compatibility option only; not a security boundary. |

`start-tailscaled.sh` uses userspace networking by default, so it works without
`/dev/net/tun` or `NET_ADMIN`. Set `TS_TUN=kernel` only on a host that provides
a usable TUN device.

## First-run Tailscale flow

1. Open the public app URL.
2. The page checks KasmVNC, Tailscale and the Serve rule.
3. Press **Connect Tailscale**.
   - If `TS_AUTHKEY` is set, the container joins automatically and no browser
     login is needed.
   - Otherwise the control server runs `tailscale up --hostname=... --accept-dns=false --ssh=false`
     and returns a `https://login.tailscale.com/a/...` link. The page opens it
     in a new tab and also shows a fallback link.
4. Sign into Tailscale and approve the device.
5. The control page detects `BackendState=Running`, reads the MagicDNS name,
   and `tailscale-serve.sh` applies:
   `tailscale serve --bg --https=443 http://127.0.0.1:6080`.
6. **Open VNC** becomes enabled. It points at
   `https://<host>.<tailnet>.ts.net/` — the Tailscale link, not the raw IP.
7. On another device, install Tailscale, join the same tailnet, and open the same
   Tailscale link. Enter user `desktop` and `VNC_PW` at the KasmVNC prompt.

Serving HTTPS on the `ts.net` name requires **MagicDNS** and **HTTPS
certificates** to be enabled for the tailnet in the Tailscale admin console
(DNS page). If they are not enabled, the Serve rule fails and **Open VNC** stays
disabled while the status card reports that the link is still being applied.
As a fallback you can set `TS_SERVE_HTTP_PORT=3000` (optionally with
`TS_SERVE_FALLBACK_TCP=1`); that uses a port on the Tailscale IP instead of the
MagicDNS link.

## Health check

`/healthz` returns `200 ok` only when the control page can open a TCP connection
to KasmVNC on `127.0.0.1:6080`. It does not require Tailscale to be logged in,
so the app can be healthy while waiting for you to approve the login link.

## Deploying beside the old app

The free plan allows two apps. The old TigerVNC/noVNC app can stay online while
this one builds:

1. Create the new app with a distinct name (for example
   `ubuntu-kasmvnc-tailscale`).
2. Wait for the health check and open the control page.
3. Connect Tailscale and verify **Open VNC** end-to-end.
4. Only then delete the old app, if desired. Deletion is irreversible and is not
   performed by this repository.

## Troubleshooting

| Symptom | Check |
|---|---|
| Health `502` / page not loading | `supervisorctl status control kasmvnc`; `/var/log/supervisor/control.err.log`, `kasmvnc.err.log` |
| Control page loads, **Open VNC** disabled | Tailscale not linked, or Serve flag not yet written; check `tailscale status`, `tailscale serve status`, `/var/log/supervisor/tailscale-serve.log` |
| Tailscale says `NeedsLogin` | Use **Connect Tailscale**; the login link must be approved in a new tab |
| Login link never appears | `tailscale status --json`; `/tmp/tailscale-login.log`; the daemon may still be starting |
| Tailscale is online but Serve fails | Userspace networking may not have completed; check `tailscale serve status`. If HTTPS fails, enable MagicDNS + HTTPS certificates in the Tailscale admin console, or set `TS_SERVE_HTTP_PORT=3000` as a raw-TCP fallback |
| KasmVNC returns `401` in the container | Expected unauthenticated response; use Basic Auth with `desktop` / `VNC_PW` |
| KasmVNC does not start | Check `/home/desktop/.kasmpasswd`, `/home/desktop/.vnc/kasmvnc.yaml`, and that `desktop` is in `ssl-cert`; see `kasmvnc.err.log` |
| Black screen | `supervisorctl status xfce`; ensure Xvnc is up on `:1`; check `/var/log/supervisor/xfce.err.log` |
| Tailscale state lost after redeploy | Expected without a volume; set `TS_AUTHKEY` or persist `/var/lib/tailscale` |

## Port reminder

- CranL public port: `3000` → control page only.
- KasmVNC: `127.0.0.1:6080` → never exposed publicly.
- Tailscale Serve: tailnet `:443` → KasmVNC over the MagicDNS link
  (`https://<host>.<tailnet>.ts.net/`). This is a different network from CranL's
  public port.
- The raw Tailscale IP and its ports stay free for your own apps; the opt-in
  raw-TCP rule (`TS_SERVE_HTTP_PORT=3000`) uses a port on that IP if you need it.

## Security notes

- The public control page contains no desktop password and no Tailscale auth key.
- KasmVNC Basic Auth remains enabled by default. Tailscale is the network
  boundary; it is not a replacement for the VNC password.
- Never commit `VNC_PW`, `DESKTOP_PW` or `TS_AUTHKEY`. Set them only in CranL's
  private environment.
- Data in the container is ephemeral; back up important files.
