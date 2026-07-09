# NetBeacon — Project Status (v1.0 Complete)

## Completed
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
- **Port Health Diagnostics:** Error/discard/duplex/`ifLastChange` collection with named diagnoses (bad_cable, downshift, duplex_mismatch, congestion, loop_blocked, flapping) via `GET /api/dashboard/port-health` — replaces the old lifetime flap counter panel.
- **Switch Vitals:** CPU/memory/temperature/fan/PSU (Extreme private MIB), sysUpTime reboot detection, PoE budget, and STP topology-change tracking via `GET /api/dashboard/vitals`; new event types (port_errors, device_rebooted, fan/psu_failure, high_cpu, poe_dropped, stp_topology_change).

## Upcoming
- **SSH Fallback:** Netmiko integration for devices with restricted SNMP access.
- **Persistence Queue:** Moving DB writes to a single-threaded queue to prevent SQLite locking issues.
- **Port Traffic History:** Time-series visualization of port utilization.
