# Ubuntu Cloud Desktop — KasmVNC + Tailscale, systemd-compatible

A lightweight Ubuntu 24.04 XFCE desktop with Google Chrome, **KasmVNC**,
**Tailscale**, and a **systemd compatibility layer** backed by Supervisor.

The public CranL port serves only a small control/status page. The desktop
listens on loopback, and Tailscale Serve forwards it to your **tailnet** — there
is no public VNC port. Applications that check the usual systemd interfaces
(`systemctl`, `/run/systemd/system`, `systemd-detect-virt`, `hostnamectl`,
journald sockets, the `org.freedesktop.systemd1` D-Bus service) see a coherent
Linux system, while Supervisor remains the real process manager.

> **Honest scope:** this is a compatibility facade, not the real systemd PID 1.
> It is not a security boundary and will not fool software that deliberately
> inspects `/proc/1/exe`, cgroups, container runtimes or kernel namespaces.
> See [Known limitations](#known-limitations).

---

## Why this stack

- **KasmVNC** replaces TigerVNC + noVNC + websockify with one HTTP/WebSocket
  server. It adds adaptive JPEG/WebP encoding, dynamic resize and clipboard
  support, while using fewer moving parts and less memory.
- **Tailscale** provides private access over WireGuard. The public app page can
  generate a login link, but the desktop itself is reachable only from devices
  signed into the same tailnet.
- **Supervisor** keeps the image small and predictable; the systemd facade keeps
  ordinary Linux applications happy.

## Features

- Ubuntu 24.04 LTS, XFCE, Google Chrome from Google's APT repository, Arabic
  localization.
- KasmVNC `1.5.0` from the official release, checksum-pinned and loopback-only.
- OpenClaw desktop `2026.10.6` preinstalled from the official release,
  checksum-pinned (`--build-arg INSTALL_OPENCLAW=0` skips it), with
  `gnome-keyring` for libsecret and a small `logind` facade so sleep-inhibitor
  calls succeed.
- Node.js `24.22.1` (LTS) from the official tarball and Python `3.15` from the
  deadsnakes PPA, both installed next to the system packages.
- Tailscale in userspace networking mode by default (no `/dev/net/tun` or
  `NET_ADMIN` required); `TS_TUN=kernel` is opt-in.
- Tailscale Serve publishes KasmVNC on the **MagicDNS link** as
  `https://<host>.<tailnet>.ts.net/` (an HTTPS reverse proxy with WebSocket
  support), so **Open VNC** never uses the raw Tailscale IP and no port on that
  IP is taken. A raw-TCP rule (`TS_SERVE_HTTP_PORT`, off by default) remains as
  a fallback.
- Public control page with **Open VNC**, the Tailscale link, connection status,
  **Connect Tailscale**, and a login-link fallback.
- Optional `TS_AUTHKEY` auto-join; otherwise the login link is opened in a new
  tab from the public page and approved by the user.
- Ubuntu package upgrade at build time; KasmVNC checksum verification.
- No default swap file. Swap is opt-in (`SWAP_ENABLED=1`) and never blocks boot.
- `earlyoom` protection, best-effort memory tuning and Chrome flags for a small
  container.
- Supervisor as PID 1; the container command is `/usr/local/bin/systemd`
  (a symlink to Supervisord) so `/proc/1/comm` reads `systemd`.
- `systemctl`, `systemd-detect-virt`, `hostnamectl`, `timedatectl`, `loginctl`,
  `journalctl`, `systemd-analyze` shims, a journald socket sink and a D-Bus
  facade.
- GitHub Actions workflow that builds and publishes to GHCR.
- Docker Compose file for local testing.

## Architecture

```
CranL public HTTPS
        │
        ▼
  control page :3000  ──►  /api/status, /healthz, Tailscale login helper
        │
        │  (login link, approval in a new tab)
        ▼
  tailscaled ── Serve HTTPS (MagicDNS link) ──► 127.0.0.1:6080 (KasmVNC)
                                              │
                                              ▼ DISPLAY :1
                                    XFCE + Chrome + apps
                                              │
                    ┌─────────────────────────┴─────────────────────────┐
                    │ Supervisor (PID 1, named "systemd")               │
                    │ dbus · journald · systemd-dbus · earlyoom         │
                    │ kasmvnc · xfce · tailscaled · tailscale-serve     │
                    │ tailscale-autoconnect · control                   │
                    └─────────────────────────┬─────────────────────────┘
                                              │
                          systemctl shim ◄────┴────► org.freedesktop.systemd1
```

Ports:

| Port | Bound to | Purpose |
|---|---|---|
| `3000` | `0.0.0.0` | Public control page; CranL health path `/healthz` |
| `6080` | `127.0.0.1` | KasmVNC HTTP/WebSocket |
| `443` (tailnet, via Serve) | Tailscale Serve | HTTPS reverse proxy to `127.0.0.1:6080` on the MagicDNS link |
| `3000` (tailnet, opt-in) | Tailscale Serve | Raw-TCP fallback; disabled by default to keep the IP free |

## Quick start (Docker)

```bash
docker compose up --build -d
# Open http://localhost:3000, click Connect Tailscale, approve the link,
# then click Open VNC.
```

Or with plain Docker:

```bash
docker build -t ubuntu-systemd-desktop .
docker run -d --name ubuntu-systemd-desktop \
  -p 3000:3000 \
  --shm-size=1g \
  -e VNC_PW='Ch4ngeM3-strong' \
  -e VNC_RESOLUTION='1440x900' \
  -e TZ='Africa/Cairo' \
  ubuntu-systemd-desktop
```

**Access flow**

1. Open the public `*.cranl.net` URL (or `http://localhost:3000`).
2. Click **Connect Tailscale**. A login link opens in a new tab.
3. Approve the device in Tailscale. The page detects the connection and shows
   the Tailscale link (`<host>.<tailnet>.ts.net`).
4. Click **Open VNC**, then sign in with user `VNC_USER` (default `admin`) and
   `VNC_PW`. The button
   points at `https://<host>.<tailnet>.ts.net/`.
5. To use a phone or another computer, install Tailscale there, sign into the
   same tailnet, and open the same Tailscale link.

The public page is the only CranL-exposed service. The VNC password is still
required on the tailnet as a second layer.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `CONTROL_PORT` | `3000` (or `PORT`) | Public control-page port. Must match the CranL app port. |
| `KASM_PORT` | `6080` | KasmVNC listener, always bound to `127.0.0.1`. |
| `VNC_RESOLUTION` | `1440x900` | Initial desktop geometry; KasmVNC can resize dynamically. |
| `VNC_PW` | *(required)* | KasmVNC Basic Auth password. Set it privately for every deployment. Minimum 6 characters. |
| `VNC_USER` | `admin` | KasmVNC Basic Auth username. |
| `VNC_AUTH` | `1` | `1` requires Basic Auth. `0` disables it; not recommended. |
| `VNC_FRAME_RATE` | `60` | KasmVNC maximum frame rate. Lower it for slow links. |
| `DESKTOP_PW` | *(unset)* | Linux password for `desktop`. Set privately; no password is baked into the image. If unset, the account stays locked. |
| `ROOT_PW` | *(unset)* | Root password. Set privately to unlock the root account at start; when unset, root stays locked and `sudo` is the only path. |
| `TS_HOSTNAME` | `ubuntu-desktop` | Tailscale device name. |
| `TS_AUTHKEY` | *(unset)* | Optional Tailscale auth key for automatic join. Never commit it. |
| `TS_TUN` | `userspace` | `userspace` works without `/dev/net/tun`/NET_ADMIN. Use `kernel` only on a host with a usable TUN device. |
| `TS_STATE_DIR` | `/var/lib/tailscale` | Tailscale state directory. Ephemeral unless you mount a volume. |
| `TS_SERVE_HTTPS_PORT` | `443` | Tailnet HTTPS port for the MagicDNS link. This is what **Open VNC** uses. |
| `TS_SERVE_HTTP_PORT` | `0` | Raw-TCP fallback port on the Tailscale IP. `0` keeps the IP and its ports free for other apps. |
| `TS_SERVE_FALLBACK_TCP` | `0` | If `1`, enable the raw-TCP rule automatically when HTTPS cannot be served. |
| `TS_FUNNEL` | `0` | If `1`, publish the desktop to the **public internet** with `tailscale funnel` instead of the tailnet-only `serve`. The KasmVNC password is still required. |
| `PUBLIC_DESKTOP` | `0` | If `1`, the public app URL (`*.cranl.net`) opens KasmVNC directly and the control page moves to `/status`. `0` keeps the control page on the public port and the desktop Tailscale-only. |
| `TS_SERVE_INTERVAL` | `10` | Seconds between Serve status checks. |
| `SWAP_ENABLED` | `0` | If `1`, create and try to activate a swap file. |
| `SWAP_SIZE_MB` | `6144` | Opt-in swap file size. |
| `SWAP_DIR` / `SWAP_FILE` | `/var/swap` / `/var/swap/swapfile` | Swap location. |
| `SWAP_SPARSE` | `1` | Sparse file for small disks; use `0` on a VPS with enough space. |
| `TZ` | `Africa/Cairo` | Container timezone. |
| `LANG` / `LC_ALL` | `en_US.UTF-8` | Locale; falls back to `C.UTF-8`. |
| `SYSTEMD_DETECT_VIRT` | `none` | Value returned by `systemd-detect-virt`. |
| `HIDE_CONTAINER` | `0` | If `1`, removes `/.dockerenv`. Optional; not a security boundary. |
| `CHROME_NO_SANDBOX` | `1` | `1` starts Chrome with `--no-sandbox`, which is required under most container seccomp profiles. Set `0` if your runtime supports the setuid/user-namespace sandbox. |
| `DESKTOP_HOSTNAME` | `ubuntu-desktop` | Hostname written to `/etc/hostname`. |

## Tailscale details

`start-tailscaled.sh` uses userspace networking by default:

```bash
tailscaled --tun=userspace-networking \
  --socks5-server=localhost:1055 --outbound-http-proxy-listen=localhost:1055 \
  --state=/var/lib/tailscale/tailscaled.state
```

This needs no `/dev/net/tun` and no `NET_ADMIN`; it is the right default for
CranL and most container platforms, and it follows Tailscale's documented
userspace recipe (a localhost-only SOCKS5/HTTP proxy is exposed on `:1055` so
processes in the desktop can reach other tailnet hosts for outbound traffic; it
is never published publicly). Userspace mode still accepts inbound tailnet
connections, and `tailscale-serve.sh` configures the Tailscale link:

```bash
tailscale serve --bg --https=443 http://127.0.0.1:6080
```

The control server waits for the Serve flag before enabling **Open VNC**, which
then points at `https://<magicdns-name>/`. Because the desktop is published on
the MagicDNS name over HTTPS, the raw Tailscale IP and its ports stay free for
other apps on the same node. Serving HTTPS on the `ts.net` name requires
**MagicDNS** and **HTTPS certificates** to be enabled for the tailnet in the
Tailscale admin console (DNS page); without them the rule fails and the page
keeps waiting.

A raw-TCP fallback is available but off by default. Set
`TS_SERVE_HTTP_PORT=3000` (and optionally `TS_SERVE_FALLBACK_TCP=1` to enable it
automatically after HTTPS fails) to forward a port on the Tailscale IP:

```bash
tailscale serve --bg --tcp=3000 tcp://127.0.0.1:6080
```

**Login state is ephemeral on CranL.** A redeploy creates a fresh container and
Tailscale node state, so you will normally need to approve a new login link. To
avoid that, set a Tailscale auth key as the private `TS_AUTHKEY` variable, or
mount a persistent volume at `/var/lib/tailscale`.

## Credentials

| Item | Default |
|---|---|
| VNC user | `admin` (`VNC_USER`) |
| VNC password | Required `VNC_PW`; never baked into the image |
| Linux user | `desktop` |
| Linux password | `DESKTOP_PW` (private; no default) |
| Root password | `ROOT_PW` (private; root stays locked when unset) |
| sudo | user is in the `sudo` group; password required |

Change the Linux password inside the desktop:

```bash
sudo passwd desktop
```

Changing `VNC_PW` or `DESKTOP_PW` requires a container restart. KasmVNC stores
only an obfuscated credential in `/home/desktop/.kasmpasswd`; it never stores the
plaintext password in the image or repository.

## Performance and footprint

- **Single VNC server:** KasmVNC replaces TigerVNC + noVNC + websockify, removing
  a Python proxy and a second browser-facing component.
- **Adaptive encoding:** KasmVNC selects compression based on the link; the
  maximum frame rate is configurable.
- **XFCE:** compositor, shadows, animations, blanking and DPMS are disabled.
- **Chrome:** software rendering, bounded caches, no first-run/restore bubbles,
  and Arabic support through the system fonts.
- **Memory:** `earlyoom` protects KasmVNC/XFCE from a runaway browser tab.
- **Software rendering on purpose:** the session exports
  `LIBGL_ALWAYS_SOFTWARE=1`, `WEBKIT_DISABLE_DMABUF_RENDERER=1` and
  `GTK_A11Y=none`, so Mesa stays on llvmpipe and applications do not log the
  harmless libEGL/DRI3 and AT-SPI warnings caused by the missing GPU and
  accessibility bus.
- **OpenClaw is the heaviest optional piece** (WebKitGTK + GStreamer, roughly
  +350 MB). Build with `--build-arg INSTALL_OPENCLAW=0` for the lean image.
- **Swap:** disabled by default; see below.

For a slow link, lower `VNC_RESOLUTION` (for example `1280x800`) or
`VNC_FRAME_RATE` before adding heavier compression.

## Swap (optional)

Swap is **off by default** because KasmVNC is lighter than the old stack and a
6 GiB file wastes disk on CranL's free plan. To enable it:

```bash
-e SWAP_ENABLED=1 -e SWAP_SIZE_MB=4096
```

`setup-swap.sh` creates and tries to activate `/var/swap/swapfile`, then tries
zram if `swapon` is blocked. It records the result in
`/run/desktop-swap-status.json` and `/var/log/swap.log`. CranL containers do not
grant `CAP_SYS_ADMIN`, so active disk swap normally remains unavailable there;
the file is still prepared and the failure is reported honestly. A VPS that
permits swap can activate it. See [docs/DEPLOY-VPS.md](docs/DEPLOY-VPS.md).

## systemd compatibility matrix

| Interface | Status | Notes |
|---|---|---|
| `/run/systemd/system` | ✅ | Created at startup; `sd_booted()` returns true. |
| `systemctl start/stop/restart/status` | ✅ | Backed by Supervisor. |
| `systemctl enable/disable` | ✅ | Unit symlinks + dynamic Supervisor autostart. |
| `systemctl is-active/is-enabled/is-system-running` | ✅ | Realistic output and exit codes. |
| `systemctl list-units/list-unit-files/show/cat` | ✅ | Reads standard unit directories. |
| Dynamic unit files | ✅ | Generates a Supervisor program on demand. |
| `systemd-detect-virt` | ✅ | Defaults to `none`; configurable. |
| `hostnamectl` / `timedatectl` / `loginctl` | ✅ | Practical subset. |
| `journalctl` | ✅ | Reads the journald sink and Supervisor logs. |
| `/run/systemd/journal/socket`, `/dev/log` | ✅ | Datagram sink writes to `/var/log/journal/compat.log`. |
| D-Bus `org.freedesktop.systemd1` | ✅ | Manager/Unit/Service/Target/Job subset. |
| `/proc/1/comm` | ✅ | PID 1 is a Supervisord process named `systemd`. |
| Real systemd cgroup hierarchy | ❌ | Supervisor cgroups only. |
| `systemd-analyze` full feature set | ⚠️ | `time`, `blame`, `verify`, `cat-config`. |
| `systemctl reboot/poweroff` | ❌ | Refused inside the container. |
| Snap packages | ❌ | Snapd does not work in this image. |
| Kernel/namespace spoofing | ❌ | Not attempted. |

## How the Supervisor mapping works

Core units have static Supervisor programs with matching names:

| Unit | Supervisor program |
|---|---|
| `dbus.service` | `dbus` |
| `systemd-journald.service` | `systemd-journald` |
| `systemd-dbus.service` | `systemd-dbus` |
| `kasmvnc.service` | `kasmvnc` |
| `xfce.service` | `xfce` |
| `control.service` | `control` |
| `tailscaled.service` | `tailscaled` |
| `tailscale-serve.service` | `tailscale-serve` |

For any other `.service` file, `systemctl start` parses `ExecStart=`,
`Environment=`, `User=`, `WorkingDirectory=` and `Restart=` and writes a
generated program into `/etc/supervisor/conf.d/`, then runs
`supervisorctl reread && supervisorctl update`.

## Deploying on CranL

See [docs/DEPLOY-CRANL.md](docs/DEPLOY-CRANL.md). The short version:

1. Push this repository to GitHub.
2. Let GitHub Actions build the GHCR image, **or** let CranL build the
   Dockerfile directly from the repository.
3. Create a CranL app with port `3000`, health path `/healthz`, and the
   environment variables above.
4. Open the generated `*.cranl.net` URL, connect Tailscale, approve the login,
   then click **Open VNC**.

The free plan allows two apps. Keep the old desktop running until the new app
passes its health check and the Tailscale path has been verified; delete the old
one only after that.

## GitHub Actions / GHCR

The workflow `.github/workflows/build.yml` builds `linux/amd64` and pushes:

```
ghcr.io/<owner>/ubuntu-systemd-desktop:latest
ghcr.io/<owner>/ubuntu-systemd-desktop:sha-<commit>
```

Make the GHCR package public after the first build, or create a GitHub PAT with
`read:packages` and use it as the CranL registry credential.

## Testing

Inside the container:

```bash
/opt/desktop/tests/smoke-test.sh
/opt/desktop/tests/validate.sh
```

Manual checks:

```bash
systemctl --version
systemctl is-system-running
systemctl status kasmvnc
systemctl status tailscaled
systemd-detect-virt
hostnamectl
journalctl -n 20
busctl --system introspect org.freedesktop.systemd1 /org/freedesktop/systemd1
ps -p 1 -o comm=
google-chrome --version
node --version
python --version
curl -fsS http://127.0.0.1:3000/healthz
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:6080/
tailscale status
tailscale serve status
```

See [docs/TESTING.md](docs/TESTING.md) for expected output and browser checks.

## Project layout

```
.
├── Dockerfile
├── docker-compose.yml
├── supervisor/supervisord.conf
├── systemd/
│   ├── systemd_compat.py
│   ├── systemctl.py
│   ├── systemd_dbus.py
│   ├── systemd-journald.py
│   ├── systemd-detect-virt.py
│   ├── hostnamectl.py / timedatectl.py / loginctl.py / journalctl.py
│   ├── units/
│   └── dbus/
├── scripts/                 # KasmVNC, Tailscale, control server, startup
├── desktop/                 # wallpaper, browser defaults, launchers
├── web/                     # public control page + internal welcome page
├── tests/
├── docs/
└── .github/workflows/build.yml
```

## Known limitations

- **Not real systemd.** PID 1 is Supervisord; there is no systemd manager
  process, no cgroup delegation and no systemd-native socket activation.
- **D-Bus is a facade.** Common Manager/Unit methods work, but advanced
  transaction/job semantics and all properties are not implemented.
- **Container checks can still succeed.** `/.dockerenv` (unless
  `HIDE_CONTAINER=1`), `/proc/1/cgroup`, overlayfs and the Docker network remain
  visible. This project does not attempt to defeat security software, anti-cheat
  or licence enforcement.
- **No snap.** Google Chrome comes from Google's APT repository instead.
- **Python 3.15 is the newest interpreter, not the system one.** `/usr/bin/python3`
  stays 3.12 because apt, Supervisor and the D-Bus facade are linked against it;
  the new interpreter is `python3.15` (also `python`).
- **No audio device / no GPU acceleration.** It is a VNC desktop; GL clients
  run on llvmpipe with the DRI3 probe disabled.
- **`logind` is a stub.** `org.freedesktop.login1` answers `Inhibit()` and the
  capability queries so applications can register a sleep inhibitor, but there
  is no session/seat manager behind it (`loginctl` is a shim as well).
- **Tailscale state is ephemeral by default.** A redeploy requires a fresh login
  link unless you provide `TS_AUTHKEY` or persist `/var/lib/tailscale`.
- **CranL apps cannot activate swap.** `swapon` needs a capability the runtime
  does not grant; the file is prepared but normally inactive.
- **Data is ephemeral.** Files live in the container; redeploys can erase them.
- **Tailscale access is not a substitute for desktop security.** Keep KasmVNC
  Basic Auth enabled and use a strong `VNC_PW`.

## Security recommendations

1. Set a strong, unique `VNC_PW` before deployment; keep `VNC_AUTH=1`.
2. Set a strong `DESKTOP_PW` in the private app environment; never commit it.
3. Never commit `TS_AUTHKEY`; use the deployment's private environment.
4. Do not store SSH keys, tokens or personal data in the container.
5. Keep the public control URL reasonably private; the tailnet is the real
   access boundary.
6. Rebuild regularly to pick up Ubuntu, Chrome, KasmVNC and Tailscale updates.

## License

MIT. See [LICENSE](LICENSE).

---

## البدء السريع بالعربية

1. ارفع المجلد إلى GitHub، ثم أنشئ تطبيقًا على CranL من المستودع باستخدام
   `build: dockerfile` والمنفذ `3000` ومسار الصحة `/healthz`.
2. اضبط `VNC_USER` (الافتراضي `admin`) و`VNC_PW` بكلمة مرور قوية، واضبط
   `DESKTOP_PW` و`ROOT_PW` إن أردت تسجيل الدخول إلى لينكس أو الجذر. لا تضع أي
   أسرار في المستودع.
3. افتح رابط التطبيق، اضغط **Connect Tailscale**، ووافق على رابط الدخول في
   التبويب الجديد.
4. بعد ظهور رابط Tailscale (اسم MagicDNS)، اضغط **Open VNC** ثم سجّل الدخول
   باسم `VNC_USER` (الافتراضي `admin`) وكلمة مرور `VNC_PW`. للعمل يجب تفعيل MagicDNS وشهادات HTTPS
   لشبكتك من لوحة تحكم Tailscale.
5. لاستخدامه من الهاتف أو جهاز آخر، ثبّت Tailscale هناك وسجّل الدخول إلى نفس
   الشبكة، ثم افتح نفس رابط Tailscale. عنوان الـIP ومنافذه تبقى حرة لتطبيقاتك.

طبقة systemd هنا **توافقية** وليست systemd الحقيقي؛ راجع قسم القيود أعلاه.
