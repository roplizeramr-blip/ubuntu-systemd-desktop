#!/usr/bin/env python3
"""Public entry point for the KasmVNC desktop.

The public port serves one of two things:

  * the lightweight control page, its JSON status API and a health probe
    (default), or
  * the KasmVNC desktop itself, with the control page moved to /status, when
    PUBLIC_DESKTOP=1.

KasmVNC always listens on loopback only.  With PUBLIC_DESKTOP=1 this process
reverse-proxies HTTP and WebSocket traffic to it, so the desktop can be reached
on the public HTTPS URL of the app without adding a web server or a second
container.  Keeping this process deliberately small avoids a second web
framework and keeps the image fast.
"""

from __future__ import annotations

import http.client
import json
import os
import select
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_ROOT = Path(os.environ.get("DESKTOP_APP_ROOT", "/opt/desktop"))
WEB_INDEX = Path(os.environ.get("DESKTOP_WEB_INDEX", str(APP_ROOT / "web" / "index.html")))
KASM_HOST = os.environ.get("KASM_HOST", "127.0.0.1")
KASM_PORT = int(os.environ.get("KASM_PORT", "6080"))
CONTROL_PORT = int(os.environ.get("CONTROL_PORT") or os.environ.get("PORT") or "3000")
# When set, "/" is the desktop and the control page lives on /status.
PUBLIC_DESKTOP = os.environ.get("PUBLIC_DESKTOP", "0") != "0"
HOSTNAME = os.environ.get("DESKTOP_HOSTNAME", "").strip()

STARTED_AT = time.monotonic()

# Hop-by-hop headers are connection specific and must not be forwarded.
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "trailers",
    "transfer-encoding",
    "upgrade",
}
MAX_REQUEST_BODY = 8 * 1024 * 1024


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def desktop_ready() -> bool:
    try:
        with socket.create_connection((KASM_HOST, KASM_PORT), timeout=0.45):
            return True
    except OSError:
        return False


def _status_details(public_url: str | None = None) -> dict[str, object]:
    running = desktop_ready()
    open_url = public_url if (PUBLIC_DESKTOP and public_url) else None

    return {
        "ready": bool(running and open_url),
        "public": bool(public_url),
        "uptime": int(time.monotonic() - STARTED_AT),
        "hostname": HOSTNAME,
        "desktop": {
            "running": running,
            "engine": "KasmVNC",
            "host": KASM_HOST,
            "port": KASM_PORT,
            "auth": os.environ.get("VNC_AUTH", "1") != "0",
            "user": os.environ.get("VNC_USER", "admin"),
            "resolution": os.environ.get("VNC_RESOLUTION", "1440x900"),
        },
        "urls": {
            "open": open_url,
            "public": public_url,
        },
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "CloudDesktopControl/2.0"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        # Keep supervisor logs useful without logging every browser poll.
        if self.path not in ("/api/status", "/healthz", "/favicon.ico"):
            super().log_message(fmt, *args)

    # ---------------------------------------------------------------- helpers

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Robots-Tag", "noindex, nofollow")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'unsafe-inline'; "
            "script-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'",
        )
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)

    def _send_json(self, status: int, payload: object) -> None:
        self._send(status, _json_bytes(payload), "application/json; charset=utf-8")

    def _index(self) -> bytes:
        try:
            return WEB_INDEX.read_bytes()
        except OSError:
            return b"Cloud desktop control page is missing.\n"

    def _public_url(self) -> str | None:
        if not PUBLIC_DESKTOP:
            return None
        host = self.headers.get("Host")
        if not host:
            return None
        proto = (self.headers.get("X-Forwarded-Proto") or "https").split(",")[0].strip()
        if proto not in ("http", "https"):
            proto = "https"
        return f"{proto}://{host}/"

    def _read_body(self) -> tuple[bytes | None, bool]:
        """Return (body, ok). ok is False when the body cannot be forwarded."""
        raw = self.headers.get("Content-Length")
        if raw is None:
            if (self.headers.get("Transfer-Encoding") or "").lower().strip() == "chunked":
                return None, False
            return b"", True
        try:
            length = int(raw)
        except ValueError:
            return None, False
        if length <= 0:
            return b"", True
        if length > MAX_REQUEST_BODY:
            return None, False
        return self.rfile.read(length), True

    # -------------------------------------------------------------- proxying

    def _proxy_request(self) -> None:
        if self.headers.get("Upgrade"):
            self._proxy_upgrade()
        else:
            self._proxy_http()

    def _proxy_http(self) -> None:
        body, ok = self._read_body()
        if not ok:
            self._send(413, b"request body too large\n", "text/plain; charset=utf-8")
            return
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}
        headers["X-Forwarded-Proto"] = "https"
        headers["X-Forwarded-For"] = self.client_address[0]
        connection: http.client.HTTPConnection | None = None
        try:
            connection = http.client.HTTPConnection(KASM_HOST, KASM_PORT, timeout=30)
            connection.request(self.command, self.path, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
        except (OSError, http.client.HTTPException):
            self._send(502, b"desktop is not ready yet\n", "text/plain; charset=utf-8")
            return
        finally:
            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    pass

        self.send_response_only(response.status, response.reason)
        for key, value in response.getheaders():
            if key.lower() in HOP_BY_HOP:
                continue
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD" and payload:
            self.wfile.write(payload)

    def _proxy_upgrade(self) -> None:
        """Send the handshake and then pipe bytes both ways (WebSocket)."""
        try:
            upstream = socket.create_connection((KASM_HOST, KASM_PORT), timeout=15)
        except OSError:
            self._send(502, b"desktop is not ready yet\n", "text/plain; charset=utf-8")
            return
        upstream.settimeout(None)
        try:
            head = [f"{self.command} {self.path} {self.request_version}"]
            head.extend(f"{key}: {value}" for key, value in self.headers.items())
            upstream.sendall(("\r\n".join(head) + "\r\n\r\n").encode("latin-1", "replace"))
        except OSError:
            upstream.close()
            return

        client = self.connection
        # The connection is taken over by the tunnel; do not parse more requests.
        self.close_connection = True
        try:
            while True:
                readable, _, _ = select.select([client, upstream], [], [], 300)
                if not readable:
                    continue
                for sock in readable:
                    try:
                        data = sock.recv(65536)
                    except OSError:
                        return
                    if not data:
                        return
                    target = upstream if sock is client else client
                    target.sendall(data)
        except OSError:
            return
        finally:
            try:
                upstream.close()
            except OSError:
                pass

    # -------------------------------------------------------------- handlers

    def _handle_local(self, path: str) -> bool:
        if path == "/healthz":
            if desktop_ready():
                self._send(200, b"ok\n", "text/plain; charset=utf-8")
            else:
                self._send(503, b"desktop-starting\n", "text/plain; charset=utf-8")
            return True
        if path == "/api/status":
            self._send_json(200, {"ok": True, "status": _status_details(self._public_url())})
            return True
        if path == "/robots.txt":
            self._send(200, b"User-agent: *\nDisallow: /\n", "text/plain; charset=utf-8")
            return True
        if path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return True
        if path == "/status" and PUBLIC_DESKTOP:
            self._send(200, self._index(), "text/html; charset=utf-8")
            return True
        if path == "/" and not PUBLIC_DESKTOP:
            self._send(200, self._index(), "text/html; charset=utf-8")
            return True
        return False

    def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
        self._method("GET")

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib callback name
        self._method("HEAD")

    def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
        self._method("POST")

    def do_PUT(self) -> None:  # noqa: N802 - stdlib callback name
        self._method("PUT")

    def do_PATCH(self) -> None:  # noqa: N802 - stdlib callback name
        self._method("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib callback name
        self._method("DELETE")

    def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib callback name
        self._method("OPTIONS")

    def _method(self, method: str) -> None:
        path = self.path.split("?", 1)[0]

        if method in ("GET", "HEAD") and self._handle_local(path):
            return

        if PUBLIC_DESKTOP and method in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            self._proxy_request()
            return

        self._send(404, b"not found\n", "text/plain; charset=utf-8")


class Server(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    server = Server(("0.0.0.0", CONTROL_PORT), Handler)
    mode = "desktop at /, control page at /status" if PUBLIC_DESKTOP else "control page at /"
    print(
        f"[control] public page on 0.0.0.0:{CONTROL_PORT} ({mode}); "
        f"KasmVNC on {KASM_HOST}:{KASM_PORT}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
