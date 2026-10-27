# syntax=docker/dockerfile:1.7
FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive

ENV LANG=en_US.UTF-8 \
    LANGUAGE=en_US:en \
    LC_ALL=en_US.UTF-8 \
    TZ=Africa/Cairo \
    DISPLAY=:1 \
    WS_PORT=6080 \
    VNC_DISPLAY=1 \
    CONTROL_PORT=3001 \
    VNC_RESOLUTION=1280x720 \
    VNC_USER=admin \
    VNC_AUTH=1 \
    SWAP_ENABLED=0 \
    SWAP_DIR=/var/swap \
    SWAP_FILE=/var/swap/swapfile \
    SWAP_SIZE_MB=6144 \
    SWAP_SPARSE=1 \
    HIDE_CONTAINER=0 \
    CHROME_NO_SANDBOX=1 \
    DESKTOP_HOSTNAME=ubuntu-desktop \
    PUBLIC_DESKTOP=1 \
    DEBIAN_FRONTEND=noninteractive

# Keep the base current.  The desktop is intentionally lean: TigerVNC provides
# the X server, websockify bridges it to WebSocket, noVNC renders it in the
# browser, and nginx is the only public listener (it multiplexes the desktop and
# the status page on one port and proxies WebSocket without a Python hop).
RUN printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d \
    && chmod 0755 /usr/sbin/policy-rc.d \
    && apt-get update \
    && apt-get -y upgrade \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl wget gnupg xz-utils libatomic1 git \
        locales tzdata sudo passwd procps psmisc iproute2 net-tools hostname less nano \
        dbus dbus-x11 gnome-keyring \
        python3 python3-dbus python3-gi gir1.2-glib-2.0 \
        supervisor \
        earlyoom kmod openssl \
        tigervnc-standalone-server tigervnc-common novnc websockify nginx-light \
        xfwm4 xfce4-panel xfce4-settings xfconf xfce4-terminal \
        xfce4-screenshooter xfce4-taskmanager xfce4-appfinder \
        thunar mousepad ristretto \
        x11-utils x11-xserver-utils x11-xkb-utils xauth xdg-utils desktop-file-utils \
        xfonts-base xfonts-utils adwaita-icon-theme \
        fonts-dejavu-core fonts-noto-core fonts-noto-color-emoji fonts-liberation \
        librsvg2-common librsvg2-bin feh \
    && rm -rf /var/lib/apt/lists/*

# Google Chrome replaces Firefox.  Debian's chromium package is usable, but the
# image deliberately ships Google Chrome from Google's own APT repository (the
# repository stays, so a later `apt upgrade` also updates the browser).
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

# Node.js and a second Python were removed: nothing in the desktop uses them,
# and each added a download step and a layer of files to every build.

RUN sed -i 's/^# *en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen \
    && locale-gen

# Do not bake a Linux password into the public image. The entrypoint applies
# DESKTOP_PW from the deployment's private environment at container start.
RUN set -eux; \
    if getent passwd desktop >/dev/null; then userdel -r desktop 2>/dev/null || true; fi; \
    for existing_user in $(getent passwd 1000 | cut -d: -f1); do userdel -r "$existing_user" 2>/dev/null || true; done; \
    for existing_group in $(getent group 1000 | cut -d: -f1); do groupdel "$existing_group" 2>/dev/null || true; done; \
    if ! getent group desktop >/dev/null; then \
        groupadd -g 1000 desktop 2>/dev/null || groupadd desktop; \
    fi; \
    useradd -m -u 1000 -g desktop -s /bin/bash -G sudo desktop; \
    passwd -l root; \
    install -d -m 0755 -o desktop -g desktop /home/desktop/Desktop; \
    install -d -m 0755 -o desktop -g desktop /home/desktop/.config; \
    install -d -m 0755 -o desktop -g desktop /home/desktop/.cache; \
    install -d -m 0700 -o desktop -g desktop /home/desktop/.vnc

# Desktop assets.
RUN install -d -m 0755 /usr/share/backgrounds \
        /usr/share/applications /etc/supervisor/conf.d
COPY supervisor/supervisord.conf /etc/supervisor/supervisord.conf
COPY scripts/ /usr/local/bin/
COPY desktop/wallpaper.svg /usr/share/backgrounds/ubuntu-cloud.svg
# The lean session paints the wallpaper once with feh, which needs a raster
# image; render the SVG at build time instead of running a backdrop daemon.
RUN rsvg-convert -w 1920 -h 1080 /usr/share/backgrounds/ubuntu-cloud.svg \
        -o /usr/share/backgrounds/desktop.png
# Keep custom launchers out of /usr/share/applications so they do not replace
# the package-provided Chrome/Thunar/Settings entries.  The package entries
# stay in the application menu; the Arabic launchers are used on the desktop.
COPY desktop/applications/ /opt/desktop/applications/
COPY web/ /opt/desktop/web/
COPY web/welcome.html /opt/desktop/welcome.html
COPY tests/ /opt/desktop/tests/

# Chrome must live at the expected path so the wrapper can add the no-sandbox
# flag when the container has no user namespaces.
RUN ln -sfn /usr/bin/supervisord /usr/local/bin/systemd \
    && ln -sfn /usr/local/bin/google-chrome-wrapper.sh /usr/local/bin/google-chrome \
    && ln -sfn /usr/local/bin/google-chrome-wrapper.sh /usr/local/bin/google-chrome-stable \
    && ln -sfn /usr/local/bin/google-chrome-wrapper.sh /usr/local/bin/chromium \
    && chmod 0755 /usr/local/bin/*.sh /usr/local/bin/*.py

# Desktop launchers and runtime log directories.
RUN cp /opt/desktop/applications/*.desktop /home/desktop/Desktop/ \
    && chmod 0755 /home/desktop/Desktop/*.desktop \
    && mkdir -p /etc/supervisor/conf.d \
    && chown root:desktop /etc/supervisor/conf.d \
    && chmod 0775 /etc/supervisor/conf.d \
    && chown desktop:desktop /home/desktop \
    && find /home/desktop -mindepth 1 -maxdepth 1 \
        -exec chown -R desktop:desktop {} + \
    && chown -R desktop:desktop /opt/desktop \
    && install -d -m 0755 /var/log/supervisor /var/log/nginx \
    && install -d -m 0700 /var/swap
RUN update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
RUN fc-cache -f >/dev/null 2>&1 || true

RUN chmod 0755 /usr/local/bin/*.sh /usr/local/bin/*.py /opt/desktop/tests/*.sh

HEALTHCHECK --interval=30s --timeout=8s --start-period=90s --retries=3 \
    CMD /usr/local/bin/healthcheck.sh

EXPOSE 3000
WORKDIR /home/desktop
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["/usr/local/bin/systemd", "-n", "-c", "/etc/supervisor/supervisord.conf"]
