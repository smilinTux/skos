import http.client
import threading

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from skos.autopilot.sandbox_proxy import AllowlistProxy, _target_host, serve


def test_target_host_from_absolute_http_uri():
    assert _target_host("http://172.17.0.1:18780/v1/chat") == "172.17.0.1"


def test_target_host_from_absolute_http_uri_no_port():
    assert _target_host("http://gw.local/x") == "gw.local"


def test_target_host_from_relative_path_is_empty():
    assert _target_host("/relative") == ""


def test_target_host_feeds_allowlist_check():
    assert AllowlistProxy(["172.17.0.1"]).is_allowed(_target_host("http://172.17.0.1:18780/v1")) is True


def test_allows_only_listed_hosts():
    p = AllowlistProxy(["github.com", "gw.local"])
    assert p.is_allowed("github.com") is True
    assert p.is_allowed("GITHUB.COM") is True          # case-insensitive
    assert p.is_allowed("github.com:443") is True       # port stripped
    assert p.is_allowed("evil.example.com") is False
    assert p.is_allowed("") is False
    assert p.is_allowed("githubXcom") is False          # no substring match


def test_empty_allowlist_denies_all():
    assert AllowlistProxy([]).is_allowed("github.com") is False


class _ChunkedUpstream(BaseHTTPRequestHandler):
    """Fake upstream that answers with Transfer-Encoding: chunked, like skgateway
    streaming does. The proxy buffers + de-chunks the body, so it must NOT pass the
    chunked header through (that mismatch is what broke opencode/undici)."""

    def do_POST(self):                                # noqa: N802
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        self.wfile.write(b"%x\r\n%s\r\n0\r\n\r\n" % (len(body), body))

    def log_message(self, *a):                        # silence
        return


def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_forward_restrips_chunked_framing_and_sets_content_length():
    up_port, px_port = _free_port(), _free_port()
    upstream = ThreadingHTTPServer(("127.0.0.1", up_port), _ChunkedUpstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    threading.Thread(
        target=serve, args=([f"127.0.0.1"], px_port), daemon=True).start()

    import time
    time.sleep(0.3)                                   # let both servers bind
    conn = http.client.HTTPConnection("127.0.0.1", px_port, timeout=5)
    # forward-proxy request: absolute-form URI with the (allowlisted) upstream host
    conn.request("POST", f"http://127.0.0.1:{up_port}/v1/chat", body=b"{}",
                 headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    payload = resp.read()

    assert resp.status == 200
    assert payload == b'{"ok":true}'
    # the chunked framing must be gone; a concrete Content-Length must describe the body
    assert resp.getheader("Transfer-Encoding") is None
    assert resp.getheader("Content-Length") == str(len(payload))
    upstream.shutdown()
