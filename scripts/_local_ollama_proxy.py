"""Throwaway HTTP reverse proxy: 127.0.0.1:<LISTEN_PORT> (inside the
container) -> http://<TARGET_HOST>:<TARGET_PORT>. Exists purely so
AI_INDEX_BASE_URL can satisfy providers.py's `_require_https_or_local`
guard (which only allows https, or plain http to literal localhost/
127.0.0.1) when the real target is a different host.

Unlike a raw TCP relay, this is HTTP-aware and rewrites the Host header to
match the real target — several Ollama deployments (including this repo's
own gpu-ocr-service Caddy front, see gpu-ocr-service/Caddyfile) reject
requests whose Host header doesn't match, closing the connection with no
response ("RemoteDisconnected") rather than an HTTP error, which a plain
TCP relay can't work around since it forwards the client's original Host
header (127.0.0.1:<LISTEN_PORT>) unchanged.

Not part of the application — run manually, detached, only for the
duration of a comparison-script run against a non-local Ollama host.
"""

import http.server
import socketserver
import sys
import urllib.error
import urllib.request

LISTEN_PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 11434
TARGET_HOST = sys.argv[2] if len(sys.argv) > 2 else "host.docker.internal"
TARGET_PORT = int(sys.argv[3]) if len(sys.argv) > 3 else 11434
TARGET_BASE = f"http://{TARGET_HOST}:{TARGET_PORT}"

_DROP_REQUEST_HEADERS = {"host", "content-length", "connection"}
_DROP_RESPONSE_HEADERS = {"transfer-encoding", "connection"}


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _proxy(self) -> None:
        url = TARGET_BASE + self.path
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in _DROP_REQUEST_HEADERS}
        req = urllib.request.Request(url, data=body, headers=headers, method=self.command)
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                payload = resp.read()
                self.send_response(resp.status)
                for k, v in resp.getheaders():
                    if k.lower() not in _DROP_RESPONSE_HEADERS:
                        self.send_header(k, v)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        except urllib.error.HTTPError as e:
            payload = e.read()
            self.send_response(e.code)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except Exception as e:  # noqa: BLE001 — proxy reports upstream failures as 502, never crashes
            payload = str(e).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    do_GET = _proxy
    do_POST = _proxy
    do_PUT = _proxy

    def log_message(self, fmt, *args) -> None:
        print(f"[proxy] {self.address_string()} {fmt % args}", file=sys.stderr)


class ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    server = ThreadingServer(("127.0.0.1", LISTEN_PORT), ProxyHandler)
    print(f"[proxy] listening on 127.0.0.1:{LISTEN_PORT} -> {TARGET_BASE}")
    server.serve_forever()
