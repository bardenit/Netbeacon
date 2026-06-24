#!/bin/sh
# Fix ownership of bind-mounted host directories, then drop to non-root user.
# Runs as root so it can chown regardless of what the host created the dirs as.
chown -R netbeacon:netbeacon /app/data /app/config

# Generate a self-signed TLS certificate on first start.
# Stored in the data volume so it survives container restarts.
if [ ! -f /app/data/ssl/cert.pem ]; then
  mkdir -p /app/data/ssl
  openssl req -x509 -newkey rsa:4096 -days 365 -nodes \
    -keyout /app/data/ssl/key.pem \
    -out    /app/data/ssl/cert.pem \
    -subj   "/CN=netbeacon" \
    -addext "subjectAltName=DNS:localhost,DNS:netbeacon,IP:127.0.0.1"
  chown -R netbeacon:netbeacon /app/data/ssl
fi

# Start the HTTP→HTTPS redirect listener in the background (port 8080 → 443).
setpriv --reuid=10001 --regid=10001 --clear-groups -- python3 /app/app/http_redirect.py &

exec setpriv --reuid=10001 --regid=10001 --clear-groups -- "$@"
