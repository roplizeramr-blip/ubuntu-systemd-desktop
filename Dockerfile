# syntax=docker/dockerfile:1.7
FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG KASM_VERSION=1.5.0
ARG KASM_SHA256=f599fe02e2175b9817b6165f74a5d2bebdc73118dde9181ba3410963bed7ae1e

ENV LANG=en_US.UTF-8 \
    LANGUAGE=en_US:en \
    LC_ALL=en_US.UTF-8 \
    TZ=Africa/Cairo \
    DISPLAY=:1 \
    KASM_PORT=6080 \
    KASM_DISPLAY=1 \
    CONTROL_PORT=3000 \
    VNC_RESOLUTION=1440x900 \
    VNC_USER=admin \
    VNC_AUTH=1 \
    VNC_FRAME_RATE=60 \
    SWAP_ENABLED=0 \
    SWAP_DIR=/var/swap \
    SWAP_FILE=/var/swap/swapfile \
    SWAP_SIZE_MB=6144 \
    SWAP_SPARSE=1 \
    SYSTEMD_DETECT_VIRT=none \
    HIDE_CONTAINER=0 \
    CHROME_NO_SANDBOX=1 \
    DESKTOP_HOSTNAME=ubuntu-desktop \
    TS_HOSTNAME=ubuntu-desktop \
    TS_STATE_DIR=/var/lib/tailscale \
    TS_TUN=userspace \
    TS_SERVE_HTTPS_PORT=443 \
    TS_SERVE_HTTP_PORT=0 \
    TS_SERVE_FALLBACK_TCP=0 \
    TS_FUNNEL=0 \
    PUBLIC_DESKTOP=0 \
    TS_SERVE_INTERVAL=10 \
    DEBIAN_FRONTEND=noninteractive

# Keep the base current.  The desktop is intentionally lean: KasmVNC replaces
# TigerVNC + noVNC + websockify, and Tailscale supplies private access without
# publishing a VNC port.  ssl-cert is required by KasmVNC's bundled web server.
RUN apt-get update \
    && apt-get -y upgrade \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl wget gnupg xz-utils libatomic1 \
        locales tzdata sudo passwd procps psmisc iproute2 net-tools hostname less nano \
        dbus dbus-x11 gnome-keyring \
        python3 python3-dbus python3-gi gir1.2-glib-2.0 \
        supervisor systemd \
        earlyoom kmod ssl-cert \
        xfce4 xfce4-terminal xfce4-screenshooter xfce4-taskmanager xfce4-appfinder \
        thunar mousepad ristretto \
        x11-utils x11-xserver-utils x11-xkb-utils xauth xdg-utils desktop-file-utils \
        xfonts-base xfonts-utils adwaita-icon-theme \
        fonts-dejavu-core fonts-noto-core fonts-noto-color-emoji fonts-liberation \
        librsvg2-common \
    && rm -rf /var/lib/apt/lists/*

# Google Chrome replaces Firefox.  Ubuntu's chromium packages are snap-only and
# snaps do not run in this image, so Chrome comes from Google's own APT
# repository.  The repository stays on the image, so a later `apt upgrade` also
# updates the browser.
RUN install -d -m 0755 /etc/apt/keyrings \
    && curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
        -o /etc/apt/keyrings/google-chrome.asc \
    && chmod 0644 /etc/apt/keyrings/google-chrome.asc \
    && echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.asc] https://dl.google.com/linux/chrome/deb/ stable main" \
        > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends google-chrome-stable \
    && sed -i 's|/usr/bin/google-chrome-stable|/usr/local/bin/google-chrome-stable|g' \
        /usr/share/applications/google-chrome.desktop \
    && rm -rf /var/lib/apt/lists/*

# Node.js from the official tarball, pinned and verified by SHA-256.  It is
# unpacked into /usr/local, so node/npm/npx are the current LTS and never
# collide with anything Ubuntu ships.
ARG NODE_VERSION=24.22.1
ARG NODE_SHA256=6c172a9ab6718f7b801fb5a7bd486af2ee5e32758086a04b84fcce2aa77095e6
RUN curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" \
        -o /opt/node.tar.xz \
    && echo "${NODE_SHA256}  /opt/node.tar.xz" | sha256sum -c - \
    && tar -xJf /opt/node.tar.xz -C /usr/local --strip-components=1 --no-same-owner \
    && rm -f /opt/node.tar.xz \
    && node --version \
    && npm --version

# Latest stable Python from the deadsnakes PPA.  It is installed next to the
# system interpreter on purpose: apt, Supervisor and the D-Bus facade are
# linked against /usr/bin/python3 (3.12) and replacing it would break the
# image.  The new interpreter is available as python3.15 and as `python`.
ARG PYTHON_VERSION=3.15
RUN install -d -m 0755 /etc/apt/keyrings \
    && curl -fsSL "https://keyserver.ubuntu.com/pks/lookup?op=get&search=0xF23C5A6CF475977595C89F51BA6932366A755776&options=mr" \
        | gpg --dearmor -o /etc/apt/keyrings/deadsnakes.gpg \
    && chmod 0644 /etc/apt/keyrings/deadsnakes.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/deadsnakes.gpg] https://ppa.launchpadcontent.net/deadsnakes/ppa/ubuntu noble main" \
        > /etc/apt/sources.list.d/deadsnakes.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        "python${PYTHON_VERSION}" "python${PYTHON_VERSION}-venv" "python${PYTHON_VERSION}-dev" \
    && (python3.15 -m ensurepip --upgrade >/dev/null 2>&1 \
        || (curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py \
            && python3.15 /tmp/get-pip.py --break-system-packages >/dev/null \
            && rm -f /tmp/get-pip.py)) \
    && ln -sfn "/usr/bin/python${PYTHON_VERSION}" /usr/local/bin/python \
    && python --version \
    && python -m pip --version \
    && rm -rf /var/lib/apt/lists/*

# Tailscale (userspace networking mode by default; no TUN/NET_ADMIN needed).
RUN install -d -m 0755 /usr/share/keyrings \
    && curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg \
        -o /usr/share/keyrings/tailscale-archive-keyring.gpg \
    && chmod 0644 /usr/share/keyrings/tailscale-archive-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/tailscale-archive-keyring.gpg] https://pkgs.tailscale.com/stable/ubuntu noble main" \
        > /etc/apt/sources.list.d/tailscale.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends tailscale \
    && rm -rf /var/lib/apt/lists/*

# KasmVNC 1.5.0: a single HTTP/WebSocket server with adaptive encoding, no
# Java/Flash and no separate websockify process.  The checksum is pinned so a
# changed GitHub asset cannot silently enter the image.
RUN curl -fsSL \
        "https://github.com/kasmtech/KasmVNC/releases/download/v${KASM_VERSION}/kasmvncserver_noble_${KASM_VERSION}_amd64.deb" \
        -o /tmp/kasmvnc.deb \
    && echo "${KASM_SHA256}  /tmp/kasmvnc.deb" | sha256sum -c - \
    && apt-get update \
    && apt-get install -y --no-install-recommends /tmp/kasmvnc.deb \
    && rm -f /tmp/kasmvnc.deb \
    && rm -rf /var/lib/apt/lists/*

# OpenClaw desktop (Tauri + WebKitGTK).  Same policy as KasmVNC: a pinned
# release asset verified by checksum, so a changed download cannot slip into
# the image.  The package pulls WebKitGTK and GStreamer, which is most of the
# image growth; build with --build-arg INSTALL_OPENCLAW=0 for the lean image.
ARG OPENCLAW_VERSION=2026.10.6
ARG OPENCLAW_SHA256=962bef5fb06b24fa24e592a6128137225a04acf9c1f742dd251715e40131263d
ARG INSTALL_OPENCLAW=1
RUN if [ "$INSTALL_OPENCLAW" = "1" ]; then \
        curl -fsSL \
            "https://github.com/openclaw/openclaw/releases/download/v${OPENCLAW_VERSION}/OpenClaw-${OPENCLAW_VERSION}-amd64.deb" \
            -o /tmp/openclaw.deb \
        && echo "${OPENCLAW_SHA256}  /tmp/openclaw.deb" | sha256sum -c - \
        && apt-get update \
        && apt-get install -y --no-install-recommends /tmp/openclaw.deb \
        && rm -f /tmp/openclaw.deb \
        && rm -rf /var/lib/apt/lists/*; \
    fi

RUN sed -i 's/^# *en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen \
    && locale-gen

# Do not bake a Linux password into the public image. The entrypoint applies
# DESKTOP_PW from the deployment's private environment at container start.
# The desktop user is in ssl-cert so KasmVNC can read the snakeoil key.
RUN set -eux; \
    if getent passwd desktop >/dev/null; then userdel -r desktop 2>/dev/null || true; fi; \
    for existing_user in $(getent passwd 1000 | cut -d: -f1); do userdel -r "$existing_user" 2>/dev/null || true; done; \
    for existing_group in $(getent group 1000 | cut -d: -f1); do groupdel "$existing_group" 2>/dev/null || true; done; \
    if ! getent group desktop >/dev/null; then \
        groupadd -g 1000 desktop 2>/dev/null || groupadd desktop; \
    fi; \
    useradd -m -u 1000 -g desktop -s /bin/bash -G sudo,ssl-cert desktop; \
    passwd -l root; \
    install -d -m 0755 -o desktop -g desktop /home/desktop/Desktop; \
    install -d -m 0755 -o desktop -g desktop /home/desktop/.config; \
    install -d -m 0755 -o desktop -g desktop /home/desktop/.cache; \
    install -d -m 0700 -o desktop -g desktop /home/desktop/.vnc

# The desktop package ships a CLI installer but nothing ever runs it, so the
# app started with "OpenClaw CLI not found" and refused to arm its bundled
# runtime ("this CLI cannot verify the current runtime pin and service
# definition").  Run the shipped installer for the desktop user at build time:
# it fetches its own private Node runtime and installs the CLI under
# /home/desktop/.openclaw, which is where the app looks for it.  --runtime-only
# matches what the app itself does; it wires up the Gateway service later, once
# the systemd user session exists.  npm has a known ENOTEMPTY flake on first
# install; the retry loop makes the build deterministic.
ARG OPENCLAW_CLI_VERSION=2026.10.7
RUN if [ "$INSTALL_OPENCLAW" = "1" ]; then \
        set -eux; \
        install -d -m 0755 -o desktop -g desktop /home/desktop/.openclaw; \
        for attempt in 1 2 3; do \
            if runuser -u desktop -- env HOME=/home/desktop \
                bash /usr/lib/OpenClaw/install-cli.sh \
                    --prefix /home/desktop/.openclaw \
                    --version "${OPENCLAW_CLI_VERSION}" \
                    --runtime-only \
                    --no-onboard; then \
                break; \
            fi; \
            rm -rf /home/desktop/.openclaw /home/desktop/.npm; \
            install -d -m 0755 -o desktop -g desktop /home/desktop/.openclaw; \
        done; \
        test -x /home/desktop/.openclaw/bin/openclaw; \
        rm -rf /home/desktop/.npm; \
    fi

# Compatibility layer, units, D-Bus facade and desktop assets.
RUN install -d -m 0755 /opt/systemd-compat /usr/share/backgrounds \
        /usr/share/applications /etc/supervisor/conf.d
COPY supervisor/supervisord.conf /etc/supervisor/supervisord.conf
COPY systemd/units/ /etc/systemd/system/
COPY systemd/systemd_compat.py /opt/systemd-compat/systemd_compat.py
COPY systemd/systemd_dbus.py /opt/systemd-compat/systemd_dbus.py
COPY systemd/systemd-journald.py /opt/systemd-compat/systemd-journald.py
COPY systemd/systemctl.py /opt/systemd-compat/systemctl.py
COPY systemd/systemd-detect-virt.py /opt/systemd-compat/systemd-detect-virt.py
COPY systemd/hostnamectl.py /opt/systemd-compat/hostnamectl.py
COPY systemd/timedatectl.py /opt/systemd-compat/timedatectl.py
COPY systemd/loginctl.py /opt/systemd-compat/loginctl.py
COPY systemd/journalctl.py /opt/systemd-compat/journalctl.py
COPY systemd/systemd-analyze.py /opt/systemd-compat/systemd-analyze.py
COPY systemd/dbus/org.freedesktop.systemd1.service /usr/share/dbus-1/system-services/org.freedesktop.systemd1.service
COPY systemd/dbus/org.freedesktop.systemd1.conf /etc/dbus-1/system.d/org.freedesktop.systemd1.conf
# logind is absent (Supervisor is PID 1), but its bus name and policy are
# present, so desktop apps still try to inhibit sleep. Point the activation
# file at the small facade instead of the packaged /bin/false.
COPY systemd/dbus/org.freedesktop.login1.service /usr/share/dbus-1/system-services/org.freedesktop.login1.service
COPY scripts/ /usr/local/bin/
COPY desktop/wallpaper.svg /usr/share/backgrounds/ubuntu-cloud.svg
# Keep custom launchers out of /usr/share/applications so they do not replace
# the package-provided Chrome/Thunar/Settings entries.  The package entries
# stay in the application menu; the Arabic launchers are used on the desktop.
COPY desktop/applications/ /opt/desktop/applications/
COPY web/ /opt/desktop/web/
COPY web/welcome.html /opt/desktop/welcome.html
COPY tests/ /opt/desktop/tests/

# Replace the real systemd CLI tools with the Supervisor-backed facade.  The
# real binaries are kept as *.real for debugging, but they cannot work without
# systemd as PID 1.
RUN ln -sfn /usr/bin/supervisord /usr/local/bin/systemd \
    && ln -sfn /usr/local/bin/google-chrome-wrapper.sh /usr/local/bin/google-chrome \
    && ln -sfn /usr/local/bin/google-chrome-wrapper.sh /usr/local/bin/google-chrome-stable \
    && ln -sfn /usr/local/bin/google-chrome-wrapper.sh /usr/local/bin/chromium \
    && ln -sfn /opt/systemd-compat/systemctl.py /usr/local/bin/systemctl \
    && ln -sfn /opt/systemd-compat/systemd-detect-virt.py /usr/local/bin/systemd-detect-virt \
    && ln -sfn /opt/systemd-compat/hostnamectl.py /usr/local/bin/hostnamectl \
    && ln -sfn /opt/systemd-compat/timedatectl.py /usr/local/bin/timedatectl \
    && ln -sfn /opt/systemd-compat/loginctl.py /usr/local/bin/loginctl \
    && ln -sfn /opt/systemd-compat/journalctl.py /usr/local/bin/journalctl \
    && ln -sfn /opt/systemd-compat/systemd-analyze.py /usr/local/bin/systemd-analyze \
    && for cmd in systemctl systemd-detect-virt hostnamectl timedatectl loginctl journalctl systemd-analyze; do \
        if [ -e "/usr/bin/$cmd" ] && [ ! -L "/usr/bin/$cmd" ]; then \
            mv "/usr/bin/$cmd" "/usr/bin/$cmd.real"; \
        fi; \
        ln -sfn "/usr/local/bin/$cmd" "/usr/bin/$cmd"; \
    done \
    && chmod 0755 /usr/local/bin/*.sh /usr/local/bin/*.py /opt/systemd-compat/*.py

# Enable the core units in the same way systemctl enable would.
RUN mkdir -p /etc/systemd/system/graphical.target.wants \
             /etc/systemd/system/multi-user.target.wants \
    && ln -sfn /etc/systemd/system/kasmvnc.service /etc/systemd/system/graphical.target.wants/kasmvnc.service \
    && ln -sfn /etc/systemd/system/xfce.service /etc/systemd/system/graphical.target.wants/xfce.service \
    && ln -sfn /etc/systemd/system/control.service /etc/systemd/system/graphical.target.wants/control.service \
    && ln -sfn /etc/systemd/system/tailscaled.service /etc/systemd/system/graphical.target.wants/tailscaled.service \
    && ln -sfn /etc/systemd/system/tailscale-serve.service /etc/systemd/system/graphical.target.wants/tailscale-serve.service \
    && ln -sfn /etc/systemd/system/dbus.service /etc/systemd/system/multi-user.target.wants/dbus.service \
    && ln -sfn /etc/systemd/system/systemd-journald.service /etc/systemd/system/multi-user.target.wants/systemd-journald.service \
    && ln -sfn /etc/systemd/system/systemd-dbus.service /etc/systemd/system/multi-user.target.wants/systemd-dbus.service \
    && ln -sfn /etc/systemd/system/graphical.target /etc/systemd/system/default.target

# Desktop launchers and runtime log directories.
RUN cp /opt/desktop/applications/*.desktop /home/desktop/Desktop/ \
    && chmod 0755 /home/desktop/Desktop/*.desktop \
    && mkdir -p /etc/supervisor/conf.d \
    && chown -R desktop:desktop /home/desktop /opt/desktop \
    && install -d -m 0755 /var/log/supervisor /var/log/journal \
    && install -d -m 0700 /var/swap \
    && install -d -m 0700 /var/lib/tailscale \
    && install -d -m 0755 /var/run/tailscale \
    && install -d -m 0755 /opt/systemd-compat
RUN update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
RUN fc-cache -f >/dev/null 2>&1 || true

# Make the compatibility tools executable even if the COPY preserved modes.
RUN chmod 0755 /opt/systemd-compat/*.py /usr/local/bin/*.sh /usr/local/bin/*.py /opt/desktop/tests/*.sh

HEALTHCHECK --interval=30s --timeout=8s --start-period=90s --retries=3 \
    CMD /usr/local/bin/healthcheck.sh

EXPOSE 3000
WORKDIR /home/desktop
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["/usr/local/bin/systemd", "-n", "-c", "/etc/supervisor/supervisord.conf"]
