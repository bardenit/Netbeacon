"""VLAN explorer, port utilization, and network intelligence dashboard endpoints."""
from __future__ import annotations

import ipaddress
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query

logger = logging.getLogger(__name__)
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import ArpEntry, Device, MacEntry, Neighbor, Port, PortStat, Subnet, Vlan, is_fortigate

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _managed_names(db: Session) -> set[str]:
    """Lowercased hostnames + SNMP names of all managed devices."""
    names: set[str] = set()
    for dev in db.query(Device).all():
        names.add(dev.hostname.lower())
        if dev.snmp_name:
            names.add(dev.snmp_name.lower())
    return names


def _trunk_port_ids(db: Session, managed_names: set[str]) -> set[int]:
    """Port IDs whose LLDP neighbor is a managed switch (i.e. trunk/uplink ports)."""
    return {
        n.local_port_id
        for n in db.query(Neighbor).filter(Neighbor.local_port_id.isnot(None)).all()
        if n.remote_system_name and n.remote_system_name.lower() in managed_names
    }


@router.get("/summary")
def get_summary(db: Session = Depends(get_db)):
    """Return high-level network statistics."""
    devices = db.query(Device).all()
    total_devices = len(devices)
    online_devices = sum(1 for d in devices if d.poll_status == "ok")

    ports = db.query(Port).all()
    total_ports = len(ports)
    up_ports = sum(1 for p in ports if p.oper_status == 1)

    total_vlans = db.query(Vlan.vlan_id).distinct().count()
    total_macs = db.query(MacEntry.mac_address).distinct().count()

    # New devices in last 24h
    yesterday = datetime.utcnow() - timedelta(hours=24)
    new_devices = db.query(MacEntry).filter(MacEntry.first_seen >= yesterday).count()

    # Windowed health counts (24h) — lifetime flap_count is deliberately not used.
    # FortiGate (gateway) ports report errant counters and are excluded.
    from sqlalchemy import and_, or_
    fg_ids = _fortigate_ids(db)
    flapping = db.query(Port).filter(Port.last_flap_at >= yesterday).count()
    unhealthy = db.query(Port).filter(
        Port.device_id.notin_(fg_ids),
        or_(
            Port.last_error_at >= yesterday,
            and_(Port.oper_status == 1, Port.duplex == 2),
            and_(Port.oper_status == 1, Port.max_speed_seen > Port.speed),
        ),
    ).count()

    return {
        "unhealthy_ports": unhealthy,
        "total_switches": total_devices,
        "online_switches": online_devices,
        "offline_switches": total_devices - online_devices,
        "total_ports": total_ports,
        "up_ports": up_ports,
        "down_ports": total_ports - up_ports,
        "total_vlans": total_vlans,
        "total_macs": total_macs,
        "new_devices_24h": new_devices,
        "flapping_ports": flapping,
    }


@router.get("/vlans")
def vlan_explorer(db: Session = Depends(get_db)):
    """Return all VLANs across all devices, grouped by VLAN ID."""
    vlans = (
        db.query(Vlan)
        .join(Device)
        .order_by(Vlan.vlan_id, Device.hostname)
        .all()
    )

    vlan_map: dict[int, dict] = {}
    for v in vlans:
        vid = v.vlan_id
        if vid not in vlan_map:
            vlan_map[vid] = {
                "vlan_id": vid,
                "vlan_name": v.vlan_name or None,
                "device_count": 0,
                "total_ports": 0,
                "devices": [],
            }
        elif v.vlan_name and not vlan_map[vid]["vlan_name"]:
            vlan_map[vid]["vlan_name"] = v.vlan_name

        all_pv = [pv for pv in v.port_vlans]
        port_count = len(all_pv)
        tagged_count = sum(1 for pv in all_pv if pv.tagged)
        untagged_count = port_count - tagged_count

        vlan_map[vid]["devices"].append({
            "device_id": v.device_id,
            "device_hostname": v.device.hostname,
            "device_ip": v.device.ip_address,
            "port_count": port_count,
            "tagged_count": tagged_count,
            "untagged_count": untagged_count,
        })
        vlan_map[vid]["device_count"] += 1
        vlan_map[vid]["total_ports"] += port_count

    return sorted(vlan_map.values(), key=lambda x: x["vlan_id"])


@router.get("/utilization")
def port_utilization(db: Session = Depends(get_db)):
    """Return top 20 non-trunk ports sorted by total bytes (rx+tx)."""
    trunk_port_ids = _trunk_port_ids(db, _managed_names(db))

    rows = (
        db.query(Port, Device)
        .join(Device, Port.device_id == Device.id)
        .filter(Port.rx_bytes.isnot(None))
        .all()
    )

    result = []
    for port, device in rows:
        if port.id in trunk_port_ids:
            continue
        rx = port.rx_bytes or 0
        tx = port.tx_bytes or 0
        total = rx + tx
        rx_err = port.rx_errors or 0
        tx_err = port.tx_errors or 0
        result.append({
            "device_id": device.id,
            "device_hostname": device.hostname,
            "device_ip": device.ip_address,
            "port_id": port.id,
            "port_name": port.port_name,
            "port_index": port.port_index,
            "port_description": port.port_description,
            "rx_bytes": rx,
            "tx_bytes": tx,
            "total_bytes": total,
            "rx_errors": rx_err,
            "tx_errors": tx_err,
            "total_errors": rx_err + tx_err,
            "speed": port.speed,
            "oper_status": port.oper_status,
        })

    result.sort(key=lambda x: x["total_bytes"], reverse=True)
    return result[:20]


@router.get("/new-devices")
def new_devices(hours: int = Query(default=24, ge=1, le=8760), db: Session = Depends(get_db)):
    """Return MAC addresses first seen within the last N hours, deduplicated to access port."""
    from app.oui import lookup_vendor
    from sqlalchemy import func as sqlfunc

    cutoff = datetime.utcnow() - timedelta(hours=hours)

    # All new MAC entries in window
    entries = (
        db.query(MacEntry, Device, Port)
        .join(Device, MacEntry.device_id == Device.id)
        .outerjoin(Port, MacEntry.port_id == Port.id)
        .filter(MacEntry.first_seen >= cutoff)
        .all()
    )

    # Trunk ports: LLDP neighbor points to a managed switch (same logic as SearchView)
    trunk_port_ids = _trunk_port_ids(db, _managed_names(db))

    # Count MACs per port in one query
    mac_counts: dict[int, int] = {
        pid: cnt
        for pid, cnt in db.query(MacEntry.port_id, sqlfunc.count(MacEntry.id))
        .filter(MacEntry.port_id.isnot(None))
        .group_by(MacEntry.port_id)
        .all()
    }

    # Group all appearances of each MAC
    by_mac: dict[str, list] = {}
    for mac_entry, device, port in entries:
        by_mac.setdefault(mac_entry.mac_address, []).append((mac_entry, device, port))

    # ARP lookup cache — one batched query for all matched MACs
    arp_cache: dict[str, ArpEntry] = {}
    if by_mac:
        for arp in db.query(ArpEntry).filter(ArpEntry.mac_address.in_(by_mac.keys())).all():
            arp_cache.setdefault(arp.mac_address, arp)

    result = []
    for mac, candidates in by_mac.items():
        # Prefer access ports (not connected to a managed switch)
        access = [
            (m, d, p) for m, d, p in candidates
            if p is None or p.id not in trunk_port_ids
        ]
        pool = access if access else candidates

        # Pick the port with the fewest MACs (most likely the access port)
        best_entry, best_device, best_port = min(
            pool,
            key=lambda x: mac_counts.get(x[2].id, 999) if x[2] else 999
        )

        arp = arp_cache.get(mac)
        result.append({
            "mac_address": mac,
            "vendor": lookup_vendor(mac),
            "ip_address": arp.ip_address if arp else None,
            "hostname": arp.hostname if arp else None,
            "device_id": best_device.id,
            "device_hostname": best_device.hostname,
            "port_id": best_port.id if best_port else None,
            "port_name": best_port.port_name if best_port else None,
            "port_index": best_entry.port_index,
            "vlan_id": best_entry.vlan_id,
            "first_seen": best_entry.first_seen,
            "last_seen": best_entry.last_seen,
        })

    result.sort(key=lambda x: x["first_seen"] or datetime.min, reverse=True)
    return result[:200]


_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "info": 3}


def _fortigate_ids(db: Session) -> set[int]:
    return {d.id for d in db.query(Device).all() if is_fortigate(d)}


def _stat_windows(db: Session, port_ids: list[int], cutoff: datetime) -> dict[int, dict]:
    """Per-port 24h counter deltas (last sample minus first sample, clamped ≥0)."""
    windows: dict[int, dict] = {}
    if not port_ids:
        return windows
    stats = (
        db.query(PortStat)
        .filter(PortStat.port_id.in_(port_ids), PortStat.sampled_at >= cutoff)
        .order_by(PortStat.sampled_at.asc())
        .all()
    )
    first: dict[int, PortStat] = {}
    last: dict[int, PortStat] = {}
    for s in stats:
        first.setdefault(s.port_id, s)
        last[s.port_id] = s

    def total(s: PortStat, a: str, b: str) -> int:
        return (getattr(s, a) or 0) + (getattr(s, b) or 0)

    for pid, f in first.items():
        l = last[pid]
        windows[pid] = {
            "errors_24h": max(0, total(l, "rx_errors", "tx_errors") - total(f, "rx_errors", "tx_errors")),
            "discards_24h": max(0, total(l, "rx_discards", "tx_discards") - total(f, "rx_discards", "tx_discards")),
        }
    return windows


@router.get("/port-health")
def port_health(db: Session = Depends(get_db)):
    """Ports with active health issues, each with named diagnoses and evidence.

    Diagnoses: bad_cable, errors, downshift, duplex_mismatch, loop_blocked,
    congestion, flapping (benign activity — unplug/sleep pattern).
    """
    from sqlalchemy import and_, or_

    now = datetime.utcnow()
    day_ago = now - timedelta(hours=24)
    week_ago = now - timedelta(days=7)

    rows = (
        db.query(Port, Device)
        .join(Device, Port.device_id == Device.id)
        .filter(or_(
            Port.last_error_at >= week_ago,
            Port.last_discard_at >= day_ago,
            Port.last_flap_at >= day_ago,
            and_(Port.oper_status == 1, Port.duplex == 2),
            and_(Port.oper_status == 1, Port.max_speed_seen > Port.speed),
            and_(Port.oper_status == 1, Port.stp_state == 2),
        ))
        .all()
    )
    rows = [(p, d) for p, d in rows if not is_fortigate(d)]

    windows = _stat_windows(db, [p.id for p, _ in rows], day_ago)

    result = []
    for port, device in rows:
        w = windows.get(port.id, {})
        errors_24h = w.get("errors_24h", 0)
        discards_24h = w.get("discards_24h", 0)
        up = port.oper_status == 1
        flapped_24h = port.last_flap_at is not None and port.last_flap_at >= day_ago
        errored_24h = port.last_error_at is not None and port.last_error_at >= day_ago
        downshift = up and port.max_speed_seen and port.speed and port.speed < port.max_speed_seen
        has_errors = errors_24h >= 5 or errored_24h

        diagnoses = []
        if has_errors and (flapped_24h or downshift):
            diagnoses.append({"code": "bad_cable", "severity": "critical",
                              "summary": "Errors + link instability — likely bad cable or connector"})
        elif has_errors:
            diagnoses.append({"code": "errors", "severity": "high",
                              "summary": "Interface errors actively accruing"})
        if downshift:
            diagnoses.append({"code": "downshift", "severity": "high",
                              "summary": "Linked below best-seen speed — possible damaged pair"})
        if up and port.duplex == 2:
            diagnoses.append({"code": "duplex_mismatch", "severity": "high",
                              "summary": "Half duplex — negotiation problem or forced-speed mismatch"})
        if up and port.stp_state == 2:
            diagnoses.append({"code": "loop_blocked", "severity": "medium",
                              "summary": "Port blocked by spanning tree — possible loop"})
        if discards_24h >= 500 and not has_errors:
            diagnoses.append({"code": "congestion", "severity": "medium",
                              "summary": "Packets discarded with no errors — congestion, not cabling"})
        if flapped_24h and not diagnoses:
            diagnoses.append({"code": "flapping", "severity": "info",
                              "summary": "Link transitions with clean counters — likely unplug/sleep"})
        if not diagnoses:
            continue

        result.append({
            "port_id": port.id,
            "device_id": device.id,
            "device_hostname": device.hostname,
            "device_ip": device.ip_address,
            "port_name": port.port_name,
            "port_index": port.port_index,
            "port_description": port.port_description,
            "port_type": port.port_type,
            "oper_status": port.oper_status,
            "diagnoses": diagnoses,
            "errors_24h": errors_24h,
            "discards_24h": discards_24h,
            "flap_count": port.flap_count,
            "last_flap_at": port.last_flap_at,
            "last_error_at": port.last_error_at,
            "speed": port.speed,
            "max_speed_seen": port.max_speed_seen,
            "duplex": port.duplex,
            "stp_state": port.stp_state,
            "last_mac": port.last_mac,
            "last_hostname": port.last_hostname,
        })

    result.sort(key=lambda r: (
        min(_SEVERITY_RANK[d["severity"]] for d in r["diagnoses"]),
        -r["errors_24h"],
    ))
    return result[:100]


@router.get("/vitals")
def switch_vitals(db: Session = Depends(get_db)):
    """Per-switch hardware/health vitals (CPU, memory, temperature, fans, PSU, PoE, uptime)."""
    out = []
    for d in db.query(Device).order_by(Device.hostname).all():
        out.append({
            "device_id": d.id,
            "hostname": d.hostname,
            "ip_address": d.ip_address,
            "poll_status": d.poll_status,
            "uptime_seconds": d.sys_uptime // 100 if d.sys_uptime is not None else None,
            "cpu_util": d.cpu_util,
            "mem_used_pct": d.mem_used_pct,
            "temperature": d.temperature,
            "fans_ok": d.fans_ok,
            "psu_ok": d.psu_ok,
            "poe_budget_w": d.poe_budget_w,
            "poe_used_w": d.poe_used_w,
            "stp_top_changes": d.stp_top_changes,
            "vitals_updated_at": d.vitals_updated_at,
        })
    return out


@router.get("/subnet-utilization")
def subnet_utilization(db: Session = Depends(get_db)):
    """Summarize IP utilization per subnet.

    Subnets are read from the subnets table (managed via the Settings → Networks UI).
    Any IPs not covered by a configured subnet fall back to /24 grouping.
    """
    # Parse subnets stored in DB
    known_nets: list[ipaddress.IPv4Network] = []
    for row in db.query(Subnet).order_by(Subnet.cidr).all():
        try:
            known_nets.append(ipaddress.ip_network(row.cidr, strict=False))
        except ValueError:
            logger.warning("Invalid subnet in DB: %s", row.cidr)

    APIPA_NET = ipaddress.ip_network("169.254.0.0/16")

    entries = db.query(ArpEntry).all()

    # Bucket for each known subnet (keyed by network string)
    subnet_data: dict[str, dict] = {}
    for net in known_nets:
        key = str(net)
        subnet_data[key] = {
            "subnet": key,
            "prefix_len": net.prefixlen,
            "total_hosts": max(net.num_addresses - 2, 1),
            "seen_ips": [],
            "_net_obj": net,
        }

    for entry in entries:
        try:
            ip = ipaddress.ip_address(entry.ip_address)
        except ValueError:
            continue

        row = {"ip": entry.ip_address, "mac": entry.mac_address,
               "hostname": entry.hostname, "last_seen": entry.last_seen}

        # APIPA (169.254.x.x) — always bucket together regardless of config
        if ip in APIPA_NET:
            key = "169.254.0.0/16 (APIPA)"
            if key not in subnet_data:
                subnet_data[key] = {
                    "subnet": key,
                    "prefix_len": 16,
                    "total_hosts": 65534,
                    "seen_ips": [],
                    "_net_obj": APIPA_NET,
                }
            subnet_data[key]["seen_ips"].append(row)
            continue

        # Try to match a declared subnet first
        matched = False
        for net in known_nets:
            if ip in net:
                subnet_data[str(net)]["seen_ips"].append(row)
                matched = True
                break

        if not matched:
            # Fall back: auto-bucket into /24
            fallback_net = ipaddress.ip_network(f"{entry.ip_address}/24", strict=False)
            key = str(fallback_net)
            if key not in subnet_data:
                subnet_data[key] = {
                    "subnet": key,
                    "prefix_len": 24,
                    "total_hosts": 254,
                    "seen_ips": [],
                    "_net_obj": fallback_net,
                }
            subnet_data[key]["seen_ips"].append(row)

    result = []
    for data in subnet_data.values():
        used = len(data["seen_ips"])
        result.append({
            "subnet": data["subnet"],
            "prefix_len": data["prefix_len"],
            "total_hosts": data["total_hosts"],
            "used": used,
            "pct_used": round(used / data["total_hosts"] * 100, 1),
            "ips": sorted(data["seen_ips"], key=lambda x: ipaddress.ip_address(x["ip"])),
        })

    result.sort(key=lambda x: x["used"], reverse=True)
    return result


@router.get("/dark-ports")
def dark_ports(days: int = Query(default=7, ge=1, le=365), db: Session = Depends(get_db)):
    """Ports that are admin-up but have had no MACs and no significant traffic for >N days."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    rows = (
        db.query(Port, Device)
        .join(Device, Port.device_id == Device.id)
        .filter(Port.admin_status == 1)  # admin up
        .filter(
            (Port.last_connection_at < cutoff) | (Port.last_connection_at.is_(None))
        )
        .order_by(Port.last_connection_at.asc().nullsfirst())
        .all()
    )
    result = []
    for port, device in rows:
        total_bytes = (port.rx_bytes or 0) + (port.tx_bytes or 0)
        result.append({
            "port_id": port.id,
            "port_index": port.port_index,
            "port_name": port.port_name,
            "port_description": port.port_description,
            "port_type": port.port_type,
            "device_id": device.id,
            "device_hostname": device.hostname,
            "oper_status": port.oper_status,
            "last_connection_at": port.last_connection_at,
            "total_bytes": total_bytes,
            "flap_count": port.flap_count or 0,
        })
    return result[:100]


@router.get("/departed-devices")
def departed_devices(
    min_days: int = Query(default=7, ge=1, le=365),
    max_days: int = Query(default=90, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """MACs last seen between min_days and max_days ago (present but gone)."""
    from app.oui import lookup_vendor
    now = datetime.utcnow()
    cutoff_old = now - timedelta(days=max_days)
    cutoff_recent = now - timedelta(days=min_days)

    departed = (
        db.query(ArpEntry)
        .filter(ArpEntry.last_seen < cutoff_recent)
        .filter(ArpEntry.last_seen >= cutoff_old)
        .order_by(ArpEntry.last_seen.desc())
        .limit(200)
        .all()
    )

    result = []
    for arp in departed:
        days_gone = (now - arp.last_seen).days
        result.append({
            "mac_address": arp.mac_address,
            "vendor": lookup_vendor(arp.mac_address),
            "ip_address": arp.ip_address,
            "hostname": arp.hostname,
            "last_seen": arp.last_seen,
            "days_gone": days_gone,
        })
    return result


@router.get("/error-ports")
def error_ports(db: Session = Depends(get_db)):
    """Ports with non-zero error counters, sorted by total errors descending."""
    rows = (
        db.query(Port, Device)
        .join(Device, Port.device_id == Device.id)
        .filter(
            (Port.rx_errors > 0) | (Port.tx_errors > 0)
        )
        .order_by((Port.rx_errors + Port.tx_errors).desc())
        .limit(100)
        .all()
    )
    result = []
    for port, device in rows:
        rx_err = port.rx_errors or 0
        tx_err = port.tx_errors or 0
        rx = port.rx_bytes or 0
        tx = port.tx_bytes or 0
        total_bytes = rx + tx
        err_rate = round((rx_err + tx_err) / max(total_bytes / 1500, 1) * 100, 3) if total_bytes else 0
        result.append({
            "port_id": port.id,
            "port_index": port.port_index,
            "port_name": port.port_name,
            "port_description": port.port_description,
            "port_type": port.port_type,
            "device_id": device.id,
            "device_hostname": device.hostname,
            "device_ip": device.ip_address,
            "oper_status": port.oper_status,
            "rx_errors": rx_err,
            "tx_errors": tx_err,
            "total_errors": rx_err + tx_err,
            "total_bytes": total_bytes,
            "err_rate_pct": err_rate,
            "speed": port.speed,
        })
    return result


@router.get("/ip-conflicts")
def ip_conflicts(db: Session = Depends(get_db)):
    """IPs seen with more than one MAC address in the ARP table."""
    from sqlalchemy import func as sqlfunc
    dupes = (
        db.query(ArpEntry.ip_address, sqlfunc.count(ArpEntry.mac_address).label("mac_count"))
        .group_by(ArpEntry.ip_address)
        .having(sqlfunc.count(ArpEntry.mac_address) > 1)
        .all()
    )
    dupe_ips = {ip for ip, _ in dupes}
    entries_by_ip: dict[str, list[ArpEntry]] = {}
    if dupe_ips:
        for e in db.query(ArpEntry).filter(ArpEntry.ip_address.in_(dupe_ips)).all():
            entries_by_ip.setdefault(e.ip_address, []).append(e)

    result = []
    for ip, count in dupes:
        result.append({
            "ip_address": ip,
            "mac_count": count,
            "entries": [
                {"mac_address": e.mac_address, "hostname": e.hostname, "last_seen": e.last_seen}
                for e in entries_by_ip.get(ip, [])
            ],
        })
    result.sort(key=lambda x: x["ip_address"])
    return result


@router.get("/vlan-gaps")
def vlan_gaps(db: Session = Depends(get_db)):
    """VLANs present on some switches but absent from others in the same site."""
    from collections import defaultdict

    devices = db.query(Device).filter(Device.poll_status != "error").all()

    site_devices: dict[str, list] = defaultdict(list)
    for d in devices:
        site_devices[d.site or "Default"].append(d)

    # One query: VLAN IDs and names per device
    vlans_by_device: dict[int, set[int]] = defaultdict(set)
    vlan_name_by_device: dict[int, dict[int, str]] = defaultdict(dict)
    for v in db.query(Vlan).all():
        vlans_by_device[v.device_id].add(v.vlan_id)
        if v.vlan_name:
            vlan_name_by_device[v.device_id].setdefault(v.vlan_id, v.vlan_name)

    result = []
    for site, site_devs in site_devices.items():
        if len(site_devs) < 2:
            continue

        dev_vlans: dict[int, set[int]] = {d.id: vlans_by_device.get(d.id, set()) for d in site_devs}

        all_vlans = set().union(*dev_vlans.values())

        skip_vlans = {1, 1002, 1003, 1004, 1005}
        all_vlans -= skip_vlans

        gaps = []
        for vlan_id in sorted(all_vlans):
            missing_from = [
                {"device_id": d.id, "device_hostname": d.hostname}
                for d in site_devs
                if vlan_id not in dev_vlans[d.id]
            ]
            if missing_from:
                vlan_name = next(
                    (vlan_name_by_device[d.id][vlan_id]
                     for d in site_devs if vlan_id in vlan_name_by_device[d.id]),
                    None,
                )
                gaps.append({
                    "vlan_id": vlan_id,
                    "vlan_name": vlan_name,
                    "missing_from": missing_from,
                    "present_on": len(site_devs) - len(missing_from),
                    "total_switches": len(site_devs),
                })

        if gaps:
            result.append({"site": site, "gaps": gaps})

    return result


@router.get("/sites")
def site_summary(db: Session = Depends(get_db)):
    """Per-site health summary."""
    from collections import defaultdict

    devices = db.query(Device).all()
    site_map: dict[str, dict] = defaultdict(lambda: {
        "site": "", "devices": [], "total_switches": 0,
        "online_switches": 0, "total_ports": 0, "up_ports": 0,
        "new_devices_24h": 0, "flapping_ports": 0,
    })

    yesterday = datetime.utcnow() - timedelta(hours=24)

    # New MACs per device in one grouped query
    from sqlalchemy import func as sqlfunc
    new_by_device: dict[int, int] = dict(
        db.query(MacEntry.device_id, sqlfunc.count(MacEntry.id))
        .filter(MacEntry.first_seen >= yesterday)
        .group_by(MacEntry.device_id)
        .all()
    )

    for d in devices:
        site = d.site or "Default"
        s = site_map[site]
        s["site"] = site
        s["total_switches"] += 1
        if d.poll_status == "ok":
            s["online_switches"] += 1
        s["devices"].append({"id": d.id, "hostname": d.hostname, "ip_address": d.ip_address, "poll_status": d.poll_status})

        for p in d.ports:
            s["total_ports"] += 1
            if p.oper_status == 1:
                s["up_ports"] += 1
            if p.last_flap_at is not None and p.last_flap_at >= yesterday:
                s["flapping_ports"] += 1

        s["new_devices_24h"] += new_by_device.get(d.id, 0)

    return sorted(site_map.values(), key=lambda x: x["site"])


@router.get("/port-history/{port_id}")
def port_history(port_id: int, hours: int = Query(default=24, ge=1, le=8760), db: Session = Depends(get_db)):
    """Return traffic history snapshots for a port."""
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    stats = (
        db.query(PortStat)
        .filter(PortStat.port_id == port_id, PortStat.sampled_at >= cutoff)
        .order_by(PortStat.sampled_at.asc())
        .all()
    )
    return [
        {
            "sampled_at": s.sampled_at,
            "rx_bytes": s.rx_bytes,
            "tx_bytes": s.tx_bytes,
            "rx_errors": s.rx_errors,
            "tx_errors": s.tx_errors,
            "rx_discards": s.rx_discards,
            "tx_discards": s.tx_discards,
        }
        for s in stats
    ]
