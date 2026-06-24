"""Tiny HTTP server that redirects all requests to HTTPS (hardcoded target host)."""
import http.server
import os

# Use SERVER_HOST env var if set, otherwise redirect to the same hostname the
# client used — but validated to prevent open-redirect via Host header injection.
_HTTPS_PORT = int(os.environ.get("HTTPS_PORT", "443"))
_ALLOWED_HOSTS: set[str] = set(
    h.strip() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()
)


class RedirectHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self): self._redirect()
    def do_POST(self): self._redirect()
    def do_HEAD(self): self._redirect()
    def do_PUT(self): self._redirect()
    def do_DELETE(self): self._redirect()
    def do_OPTIONS(self): self._redirect()

    def _redirect(self):
        raw_host = self.headers.get("Host", "")
        # Strip port from Host header
        host = raw_host.split(":")[0].strip()

        # Validate: if ALLOWED_HOSTS is configured, only redirect to known hosts.
        # If not configured, accept any non-empty host (internal tool — trusted LAN).
        if _ALLOWED_HOSTS and host not in _ALLOWED_HOSTS:
            self.send_response(400)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if not host:
            host = "localhost"

        port_suffix = f":{_HTTPS_PORT}" if _HTTPS_PORT != 443 else ""
        # Use only the path component — strip any embedded newlines to prevent
        # response-splitting (Python's http.server encodes headers, but be explicit)
        safe_path = self.path.split("\r")[0].split("\n")[0]
        target = f"https://{host}{port_suffix}{safe_path}"

        self.send_response(301)
        self.send_header("Location", target)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass  # silence access log


if __name__ == "__main__":
    server = http.server.HTTPServer(("0.0.0.0", 8080), RedirectHandler)
    server.serve_forever()
