"""Sovereign CONNECT allowlist proxy: the sole egress from the sandbox network.
Allows a CONNECT only to an exact host in the pinned allowlist; everything else
gets 403. Stdlib only, fully inspectable."""
from __future__ import annotations

import http.client
import http.server
import select
import socket
import urllib.parse

_HOP_BY_HOP = {"proxy-connection", "connection"}


class AllowlistProxy:
    def __init__(self, allow: list[str]) -> None:
        self.allow = {h.strip().lower() for h in allow if h and h.strip()}

    def is_allowed(self, host: str) -> bool:
        if not host:
            return False
        return host.strip().lower().split(":", 1)[0] in self.allow


def _target_host(path: str) -> str:
    """Return the hostname of an absolute http(s) request URI, or "" if
    the path is relative (not a forward-proxy request)."""
    if not (path.startswith("http://") or path.startswith("https://")):
        return ""
    parsed = urllib.parse.urlsplit(path)
    return parsed.hostname or ""


def _handler(proxy: AllowlistProxy, log):
    class H(http.server.BaseHTTPRequestHandler):
        def _forward(self):
            host = _target_host(self.path)
            if not proxy.is_allowed(host):
                if log:
                    log(f"DENY {self.command} {self.path}")
                self.send_error(403, "egress denied")
                return
            if log:
                log(f"ALLOW {self.command} {host}")

            parsed = urllib.parse.urlsplit(self.path)
            port = parsed.port or 80
            target = f"{parsed.path or '/'}"
            if parsed.query:
                target = f"{target}?{parsed.query}"

            content_length = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(content_length) if content_length else None

            headers = {
                k: v
                for k, v in self.headers.items()
                if k.lower() not in _HOP_BY_HOP
            }
            headers["Host"] = parsed.netloc

            try:
                upstream = http.client.HTTPConnection(parsed.hostname, port, timeout=30)
                upstream.request(self.command, target, body=body, headers=headers)
                resp = upstream.getresponse()
                resp_body = resp.read()
            except OSError:
                self.send_error(502, "upstream unreachable")
                return

            self.send_response(resp.status)
            for name, value in resp.getheaders():
                if name.lower() in _HOP_BY_HOP:
                    continue
                self.send_header(name, value)
            self.end_headers()
            if resp_body:
                self.wfile.write(resp_body)
            upstream.close()

        def do_GET(self):                            # noqa: N802
            self._forward()

        def do_POST(self):                           # noqa: N802
            self._forward()

        def do_PUT(self):                            # noqa: N802
            self._forward()

        def do_DELETE(self):                         # noqa: N802
            self._forward()

        def do_PATCH(self):                          # noqa: N802
            self._forward()

        def do_HEAD(self):                           # noqa: N802
            self._forward()

        def do_CONNECT(self):                       # noqa: N802
            host = self.path.split(":", 1)[0]
            if not proxy.is_allowed(host):
                if log:
                    log(f"DENY {self.path}")
                self.send_error(403, "egress denied")
                return
            if log:
                log(f"ALLOW {self.path}")
            hostname, _, port = self.path.partition(":")
            try:
                upstream = socket.create_connection((hostname, int(port or 443)), timeout=30)
            except OSError:
                self.send_error(502, "upstream unreachable")
                return
            self.send_response(200, "Connection Established")
            self.end_headers()
            self._tunnel(self.connection, upstream)

        def _tunnel(self, a, b):
            socks = [a, b]
            while True:
                r, _, x = select.select(socks, [], socks, 60)
                if x or not r:
                    break
                for s in r:
                    other = b if s is a else a
                    data = s.recv(65536)
                    if not data:
                        return
                    other.sendall(data)

        def log_message(self, *a):                  # silence default logging
            return
    return H


def serve(allow: list[str], port: int = 8080, log=None) -> None:
    proxy = AllowlistProxy(allow)
    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", port), _handler(proxy, log))
    httpd.serve_forever()


if __name__ == "__main__":                          # python -m ... <port> <host> ...
    import sys
    serve(sys.argv[2:], int(sys.argv[1]))
