# NetBeacon - Network Topology Mapper

## Project Overview
A Dockerized network management tool that discovery and visualizes network topology using SNMP. Built for mixed-vendor environments (Extreme Networks, Netgear), it provides Layer 2 and Layer 3 visibility including physical port mapping, MAC/IP tracking, and VLAN membership.

## Project Status (v1.0 Complete)

### Completed
- **Modern React UI:** Fully migrated from vanilla JS to **React 18**, **Vite**, and **Tailwind CSS**.
- **Elite Security Hardening:** 
  - Switched to a **multi-stage build** with a hardened **Python 3.12-Alpine** base (64 MB image, 1 residual CVE in busybox).
  - Implemented a **read-only filesystem** and **non-root user** (`netbeacon`), privilege drop via `setpriv`.
  - Integrated **Single Access Key** protection with **Argon2id** hashing (argon2-cffi) and JWT tokens.
  - Credential columns (community strings, passwords, API keys) encrypted at rest with **Fernet** (`app/crypto.py`).
  - Full security header suite: CSP, HSTS, X-Frame-Options, Referrer-Policy, Permissions-Policy.
  - Login rate limiting (5 attempts / 5 min per IP), 12-char minimum password enforced at schema level.
- **Multi-Architecture Support:** Native support for **amd64** and **arm64** (Apple Silicon/Raspberry Pi) via Docker Buildx.
- **Topology Visualization:** Interactive graph with **hierarchical layout** (Gateway-driven) and custom port labels.
- **Switch Faceplate View:** Visual 2nd-gen grid representation with live status, VLAN filtering, and MAC tooltips.
- **Advanced Search:** Integrated MAC/IP/Hostname search with **Path-from-Gateway** visualization and "Jump to Faceplate" logic.
- **SNMP Resilience:** Added automatic retries (2) and robust error handling for fragile switch management CPUs.
- **Data Stability:** Moved to an **upsert + mark-and-sweep** pruning model to prevent UI flicker during poll cycles.

### Upcoming
- **SSH Fallback:** Netmiko integration for devices with restricted SNMP access.
- **Persistence Queue:** Moving DB writes to a single-threaded queue to prevent SQLite locking issues.
- **Port Traffic History:** Time-series visualization of port utilization.

## Tech Stack
- **Backend:** Python 3.12 (FastAPI), SQLAlchemy (SQLite), APScheduler.
- **Frontend:** React 18, TypeScript, Tailwind CSS, vis-network, Lucide Icons.
- **Auth:** Argon2id hashing (argon2-cffi), JWT (PyJWT), LocalStorage session management.
- **Deployment:** Multi-arch Docker Hub image (`jbarden75/netbeacon:latest`).

## Quick Start

### Docker Compose
```yaml
services:
  netbeacon:
    image: jbarden75/netbeacon:latest
    container_name: netbeacon
    ports:
      - "80:8080"
    volumes:
      - ./data:/app/data
      - ./config:/app/config
    environment:
      - POLL_INTERVAL_MINUTES=5
      - LOG_LEVEL=INFO
    restart: unless-stopped
```

### Initial Setup
1. Run `docker compose up -d`.
2. Open `http://localhost`.
3. Follow the wizard to **Set System Access Key**. This key will be required for all future dashboard access.

## Development

### Frontend Build
```bash
cd web
npm install
npm run build  # Compiles to web/dist
```

### Backend Setup
```bash
pip install -r requirements.txt
python -m app.main
```

## Data Collection Strategy

### Primary: SNMP
Uses standard MIBs for cross-vendor compatibility:
- `LLDP-MIB` (lldpRemTable) - neighbor discovery.
- `BRIDGE-MIB` (dot1dTpFdbTable) - MAC address table.
- `IF-MIB` (ifTable, ifXTable) - status, speed, and counters.
- `IP-MIB` - ARP table for IP-to-MAC mapping.
- `Q-BRIDGE-MIB` - VLAN membership and tagging state.

### Dependency Notes
- `pyasn1>=0.6.3` is used. Compatibility with `pysnmp-lextudio` is maintained via a shim injected into site-packages during the Docker build (`pyasn1/compat/octets.py`). **Do not pin pyasn1 to 0.5.x** — the shim handles the missing module.

## Notes
- **Deployment:** The app is designed to run behind a firewall/VPN.
- **Scalability:** The current SQLite backend is optimized for up to ~100-200 switches.
- **Extensibility:** New vendors can be added by creating a subclass in `app/collectors/`.
