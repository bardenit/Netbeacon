# ── Stage 1: Build Frontend ───────────────────────────────────────────────────
FROM node:20-slim AS frontend-builder
WORKDIR /web
COPY web/package*.json ./
RUN npm install
COPY web/ ./
RUN npm run build

# ── Stage 2: Final Runtime ───────────────────────────────────────────────────
FROM python:3.12-alpine AS runtime

WORKDIR /app

# Create a non-privileged user (Alpine syntax)
RUN addgroup -g 10001 netbeacon && \
    adduser -u 10001 -G netbeacon -s /sbin/nologin -D -H netbeacon

# Runtime system dependencies:
#   openssl     — TLS cert generation in entrypoint + libssl/libcrypto for cryptography package
#   util-linux  — setpriv (privilege drop in entrypoint)
#   libffi      — runtime requirement for argon2-cffi-bindings (CFFI-compiled .so)
RUN apk upgrade --no-cache && \
    apk add --no-cache \
    openssl \
    util-linux \
    libffi

# Install Python dependencies.
# Build deps are grouped into a virtual package so they're removed in the same
# layer, keeping them out of the final image entirely.
COPY requirements.txt .
RUN apk add --no-cache --virtual .build-deps \
        gcc musl-dev libffi-dev openssl-dev && \
    pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip show pysnmp-lextudio pyasn1 && \
    apk del .build-deps

# pyasn1 0.6+ removed pyasn1.compat.octets (trivial Python 3 helpers that are
# now native builtins). pysnmp-lextudio still imports them. Inject a shim so
# we can use the patched pyasn1 without pinning to the vulnerable 0.5.1.
RUN python3 - <<'EOF'
import os, site
compat = os.path.join(site.getsitepackages()[0], "pyasn1", "compat")
os.makedirs(compat, exist_ok=True)
init = os.path.join(compat, "__init__.py")
if not os.path.exists(init):
    open(init, "w").close()
with open(os.path.join(compat, "octets.py"), "w") as f:
    f.write("""\
# Compatibility shim: pyasn1 0.6+ removed this module; pysnmp-lextudio still imports it.
# All functions are trivial Python 3 builtins with SNMP-appropriate latin-1 encoding.
null = b""

def octs2ints(o): return list(o)
def ints2octs(i): return bytes(i)
def int2oct(i): return bytes([i])
def oct2int(o): return o if isinstance(o, int) else ord(o)
def str2octs(s): return s.encode("latin-1") if isinstance(s, str) else s
def octs2str(o): return o.decode("latin-1") if isinstance(o, (bytes, bytearray)) else o
def isStringType(s): return isinstance(s, str)
def isOctetsType(s): return isinstance(s, (bytes, bytearray))
""")
EOF

# Copy application code
COPY --chown=netbeacon:netbeacon app/ ./app/

# Copy built frontend from Stage 1
COPY --from=frontend-builder --chown=netbeacon:netbeacon /web/dist ./web/dist

# Setup data and config directories
RUN mkdir -p /app/data /app/config && \
    chown -R netbeacon:netbeacon /app/data /app/config

COPY --chown=root:root entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8080 8443

ENV FRONTEND_DIR=/app/web/dist
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Container starts as root so entrypoint can fix bind-mount ownership
# and generate the TLS cert, then drops to netbeacon via setpriv.
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8443", \
     "--ssl-keyfile", "/app/data/ssl/key.pem", "--ssl-certfile", "/app/data/ssl/cert.pem", \
     "--no-server-header"]
