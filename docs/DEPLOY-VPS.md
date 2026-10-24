# Deploying on a VPS

Use a VPS when you need more CPU/RAM, persistent storage, outbound internet, or
active host swap. The image still uses Tailscale for desktop access; the public
VPS port serves only the control page.

Recommended minimum: 2 vCPU, 4 GiB RAM, 40 GiB disk, Ubuntu 24.04.

## 1. Optional: create host swap

If the VPS has enough disk, create swap on the **host** so every container can
use it. Run as root from the repository:

```bash
sudo SWAP_SIZE_MB=6144 bash scripts/host-swap.sh
```

Verify:

```bash
swapon --show
free -h
cat /proc/sys/vm/swappiness
```

The helper adds the entry to `/etc/fstab` and writes
`/etc/sysctl.d/99-cloud-desktop-swap.conf` (`vm.swappiness=10`). Set
`SWAP_ENABLED=0` in the container so it does not create a second file.

## 2. Run the desktop container

```bash
git clone https://github.com/roplizeramr-blip/ubuntu-systemd-desktop.git
cd ubuntu-systemd-desktop

VNC_PW='Ch4ngeM3-strong' \
DESKTOP_PW='Ch4ngeMe!2026' \
TS_AUTHKEY='tskey-auth-...' \
SWAP_ENABLED=0 \
docker compose up -d --build
```

Or plain Docker:

```bash
docker build -t ubuntu-systemd-desktop .

docker run -d --name ubuntu-desktop \
  --restart unless-stopped \
  -p 3000:3000 \
  --shm-size=1g \
  -e VNC_PW='Ch4ngeM3-strong' \
  -e DESKTOP_PW='Ch4ngeMe!2026' \
  -e VNC_RESOLUTION='1440x900' \
  -e VNC_FRAME_RATE='30' \
  -e TS_AUTHKEY='tskey-auth-...' \
  -e TS_HOSTNAME='ubuntu-vps' \
  -e SWAP_ENABLED='0' \
  -e TZ='Africa/Cairo' \
  ubuntu-systemd-desktop
```

Open `http://<vps-ip>:3000` to link Tailscale if you did not provide an auth
key. After the node is online, use **Open VNC** from the control page: it opens
the Tailscale MagicDNS link (`https://<host>.<tailnet>.ts.net/`). For that to
work, enable MagicDNS and HTTPS certificates for the tailnet. Alternatively set
`TS_SERVE_HTTP_PORT=3000` to use `http://<tailscale-ip>:3000/` from a device on
the tailnet.

If you expose port `3000` to the internet, put Caddy/Nginx/Traefik with TLS in
front of it. Never expose KasmVNC port `6080`; it is bound to loopback.

## 3. Persist data and Tailscale state (optional)

```bash
docker run ... \
  -v /srv/ubuntu-desktop/home:/home/desktop \
  -v /srv/ubuntu-desktop/tailscale:/var/lib/tailscale \
  ubuntu-systemd-desktop
```

Persisting `/var/lib/tailscale` keeps the node authenticated across redeploys.
Without it, use `TS_AUTHKEY` or approve a fresh login link.

## 4. Verify

```bash
curl -fsS http://127.0.0.1:3000/healthz
docker exec ubuntu-desktop supervisorctl status
docker exec ubuntu-desktop pgrep -a Xvnc
docker exec ubuntu-desktop tailscale status
docker exec ubuntu-desktop tailscale serve status
docker exec ubuntu-desktop free -h
```

The swap status file is `/run/desktop-swap-status.json` inside the container.
With host swap and `SWAP_ENABLED=0`, check `swapon --show` on the host instead.

## 5. Performance notes

- Keep the default `1440x900`; lower it to `1280x800` on a slow link.
- `VNC_FRAME_RATE=30` is the default; lower it to `20` if CPU/bandwidth is tight.
- KasmVNC chooses adaptive JPEG/WebP encoding; there is no noVNC quality query
  string to tune.
- `SWAP_SPARSE=0` on a VPS creates a fully allocated file; a sparse file can be
  rejected by the kernel with “Swapfile has holes”.
- The desktop is CPU-rendered; a VPS with 2 vCPU is enough for XFCE + one
  Chrome window. More vCPUs help browser workloads, not VNC encoding.

## 6. Security

- Use a strong, unique `VNC_PW` and keep `VNC_AUTH=1`.
- Set `DESKTOP_PW` and `TS_AUTHKEY` only in the private environment; never commit
  them.
- Use a firewall; expose only the public control port if remote bootstrap is
  needed.
- Tailscale is the network boundary, but it is not a substitute for the VNC
  password. Treat the tailnet as trusted infrastructure.
- The container's data is ephemeral unless you mount a volume.
