"""Topology graph and MAC/IP search endpoints."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ArpEntry, Device, DeviceLabel, MacEntry, Neighbor
from app.schemas import MacSearchResult, TopologyEdge, TopologyGraph, TopologyNode

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["topology"])


def _norm_port(name: str | None) -> str:
    return (name or "").strip().lower()


@router.get("/topology", response_model=TopologyGraph)
def get_topology(db: Session = Depends(get_db)):
    devices = db.query(Device).all()
    device_map = {d.id: d for d in devices}
    port_map = {p.id: p for d in devices for p in d.ports}

    nodes = [
        TopologyNode(
            id=d.id,
            hostname=d.hostname,
            snmp_name=d.snmp_name,
            ip_address=d.ip_address,
            vendor=d.vendor,
            model=d.model,
            poll_status=d.poll_status,
            last_polled=d.last_polled,
            is_gateway=d.is_gateway or False,
            site=d.site,
        )
        for d in devices
    ]

    # Build edges from LLDP neighbor data
    # We resolve remote_system_name -> device to create graph edges
    edges: list[TopologyEdge] = []
    # pair -> edge index for updating target_port from reverse LLDP entry
    pair_to_edge: dict[frozenset, int] = {}
    edge_id = 0

    all_neighbors = db.query(Neighbor).all()

    # Build lookup: hostname/snmp_name/IP -> device_id
    name_to_id: dict[str, int] = {}
    ip_to_id: dict[str, int] = {}
    for d in devices:
        name_to_id[d.hostname.lower()] = d.id
        if d.snmp_name:
            name_to_id[d.snmp_name.lower()] = d.id
        ip_to_id[d.ip_address] = d.id

    for neighbor in all_neighbors:
        src_device = device_map.get(neighbor.local_device_id)
        if not src_device:
            continue

        # Try to resolve remote to a known device by name then chassis ID (as IP)
        remote_name = (neighbor.remote_system_name or "").lower()
        target_id = name_to_id.get(remote_name)

        if target_id is None and neighbor.remote_chassis_id:
            target_id = ip_to_id.get(neighbor.remote_chassis_id)

        if target_id is None:
            continue

        pair = frozenset([neighbor.local_device_id, target_id])
        src_port = port_map.get(neighbor.local_port_id) if neighbor.local_port_id else None
        src_port_name = src_port.port_name if src_port else None

        if pair in pair_to_edge:
            # Reverse entry: use this device's actual local port name as target_port
            # (more accurate than the remote_port_id string which may be a raw number)
            if src_port_name:
                existing = edges[pair_to_edge[pair]]
                edges[pair_to_edge[pair]] = TopologyEdge(
                    id=existing.id,
                    source_device_id=existing.source_device_id,
                    target_device_id=existing.target_device_id,
                    source_port=existing.source_port,
                    target_port=src_port_name,
                    remote_system_name=existing.remote_system_name,
                )
            continue

        pair_to_edge[pair] = len(edges)
        edge_id += 1
        edges.append(TopologyEdge(
            id=edge_id,
            source_device_id=neighbor.local_device_id,
            target_device_id=target_id,
            source_port=src_port_name,
            target_port=neighbor.remote_port_id,
            remote_system_name=neighbor.remote_system_name,
        ))

    # ── Phantom nodes for unmanaged LLDP peers (APs, printers, etc.) ─────────
    phantom_id = -1
    phantom_name_to_id: dict[str, int] = {}

    for neighbor in all_neighbors:
        remote_name = (neighbor.remote_system_name or "").strip()
        if not remote_name:
            continue
        if name_to_id.get(remote_name.lower()):
            continue  # already a known managed device

        src_device = device_map.get(neighbor.local_device_id)
        if not src_device:
            continue

        if remote_name not in phantom_name_to_id:
            phantom_name_to_id[remote_name] = phantom_id
            nodes.append(TopologyNode(
                id=phantom_id,
                hostname=remote_name,
                ip_address="",
                poll_status="unmanaged",
                unmanaged=True,
                site=src_device.site,
            ))
            phantom_id -= 1

        pid = phantom_name_to_id[remote_name]
        pair = frozenset([neighbor.local_device_id, pid])
        if pair in pair_to_edge:
            continue

        src_port = port_map.get(neighbor.local_port_id) if neighbor.local_port_id else None
        src_port_name = src_port.port_name if src_port else None
        pair_to_edge[pair] = len(edges)
        edge_id += 1
        edges.append(TopologyEdge(
            id=edge_id,
            source_device_id=neighbor.local_device_id,
            target_device_id=pid,
            source_port=src_port_name,
            target_port=neighbor.remote_port_id,
            remote_system_name=remote_name,
        ))

    # ── Drop leaked/transitive LLDP edges ───────────────────────────────────
    # A physical port connects to exactly one neighbor. Build the set of ports
    # that have a first-hand LLDP report (i.e. are the *source* of an edge).
    # If another edge merely *claims* a link to that same (device, port) but
    # names a different peer, that claim is a forwarded/leaked LLDP frame —
    # the port's own first-hand report wins, so the claim is dropped.
    first_hand: dict[tuple[int, str], int] = {}
    for e in edges:
        if e.source_port:
            first_hand[(e.source_device_id, _norm_port(e.source_port))] = e.target_device_id

    filtered: list[TopologyEdge] = []
    for e in edges:
        owner = first_hand.get((e.target_device_id, _norm_port(e.target_port)))
        if e.target_port and owner is not None and owner != e.source_device_id:
            continue  # target device's own port points elsewhere → leaked claim
        filtered.append(e)
    edges = filtered

    # Prune phantom nodes orphaned by edge filtering (managed nodes always kept)
    referenced = {e.source_device_id for e in edges} | {e.target_device_id for e in edges}
    nodes = [n for n in nodes if n.id >= 0 or n.id in referenced]

    return TopologyGraph(nodes=nodes, edges=edges)


@router.post("/poll", status_code=202)
async def poll_all(db: Session = Depends(get_db)):
    import asyncio
    from app.scheduler import poll_all_devices
    asyncio.create_task(poll_all_devices())
    return {"message": "Poll triggered for all devices"}


@router.get("/search", response_model=list[MacSearchResult])
def search(
    q: str = Query(..., min_length=2, description="MAC, IP, or hostname fragment"),
    db: Session = Depends(get_db),
):
    q = q.strip().lower()
    # Normalize MAC query (remove separators)
    mac_normalized = q.replace(":", "").replace("-", "").replace(".", "")

    results: list[MacSearchResult] = []

    # ── Search by MAC ──────────────────────────────────────────────────────
    if len(mac_normalized) >= 4 and all(c in "0123456789abcdef" for c in mac_normalized):
        from collections import defaultdict
        matched: dict[str, list] = defaultdict(list)
        for entry in db.query(MacEntry).all():
            if mac_normalized in entry.mac_address.replace(":", ""):
                matched[entry.mac_address].append(entry)
        for entries in matched.values():
            best = _pick_access_entry(entries, db)
            if best:
                _append_mac_result(results, best, db)

    # ── Search by IP ───────────────────────────────────────────────────────
    if not results:
        arp_entries = db.query(ArpEntry).filter(
            ArpEntry.ip_address.like(f"%{q}%")
        ).all()
        for arp in arp_entries:
            entries = db.query(MacEntry).filter(
                MacEntry.mac_address == arp.mac_address
            ).all()
            best = _pick_access_entry(entries, db)
            if best:
                _append_mac_result(results, best, db, ip_override=arp.ip_address)

    # ── Search by hostname fragment ────────────────────────────────────────
    if not results:
        devices = db.query(Device).filter(
            Device.hostname.ilike(f"%{q}%")
        ).all()
        for device in devices:
            results.append(MacSearchResult(
                mac_address="",
                device_id=device.id,
                device_hostname=device.hostname,
                device_ip=device.ip_address,
            ))

        arp_entries = db.query(ArpEntry).filter(
            ArpEntry.hostname.ilike(f"%{q}%")
        ).all()
        seen_macs = {r.mac_address for r in results}
        for arp in arp_entries:
            if arp.mac_address in seen_macs:
                continue
            entries = db.query(MacEntry).filter(
                MacEntry.mac_address == arp.mac_address
            ).all()
            best = _pick_access_entry(entries, db)
            if best:
                _append_mac_result(results, best, db,
                                   ip_override=arp.ip_address,
                                   hostname_override=arp.hostname)
                seen_macs.add(arp.mac_address)

    # ── Search by MAC label name (always runs — labels are user-assigned names) ──
    from sqlalchemy import or_
    seen_macs = {r.mac_address for r in results}
    labels = db.query(DeviceLabel).filter(
        or_(DeviceLabel.label.ilike(f"%{q}%"), DeviceLabel.notes.ilike(f"%{q}%"))
    ).all()
    for lbl in labels:
        if lbl.mac_address in seen_macs:
            continue
        entries = db.query(MacEntry).filter(
            MacEntry.mac_address == lbl.mac_address
        ).all()
        best = _pick_access_entry(entries, db)
        if best:
            _append_mac_result(results, best, db)
            seen_macs.add(lbl.mac_address)

    return results[:200]


def _pick_access_entry(entries: list, db: Session):
    """Given multiple MacEntry records for the same MAC, return the access-port one.

    Trunk ports (LLDP neighbor is a known managed switch) are deprioritized.
    AP uplinks (LLDP neighbor exists but isn't a managed switch) are treated as
    access ports — that's where the wireless client actually is.
    """
    if not entries:
        return None
    if len(entries) == 1:
        return entries[0]

    # Build a set of known device names once for the comparison
    known_names: set[str] = set()
    for d in db.query(Device).all():
        if d.hostname:
            known_names.add(d.hostname.lower())
        if d.snmp_name:
            known_names.add(d.snmp_name.lower())

    def score(entry):
        port = entry.port
        if not port:
            return (1, 999999)
        mac_count = db.query(MacEntry).filter(MacEntry.port_id == port.id).count()
        neighbor = db.query(Neighbor).filter(
            Neighbor.local_device_id == entry.device_id,
            Neighbor.local_port_id == port.id,
        ).first()
        # Only penalise if the LLDP peer is a switch we manage — not an AP
        is_switch_trunk = (
            neighbor is not None and
            (neighbor.remote_system_name or '').lower() in known_names
        )
        return (int(is_switch_trunk), mac_count)

    return min(entries, key=score)


def _append_mac_result(results: list, entry: MacEntry, db: Session,
                       ip_override: str | None = None,
                       hostname_override: str | None = None):
    device = db.get(Device, entry.device_id)
    if not device:
        return

    port = entry.port
    arp = None
    if not ip_override:
        arp = db.query(ArpEntry).filter(
            ArpEntry.mac_address == entry.mac_address
        ).first()

    end_host_hostname = hostname_override or (arp.hostname if arp else None)

    # Count total MACs on this port (for trunk detection)
    port_mac_count = None
    if port:
        port_mac_count = db.query(MacEntry).filter(MacEntry.port_id == port.id).count()

    # Check for LLDP neighbor on this port
    lldp_name = None
    if port:
        from app.models import Neighbor
        neighbor = db.query(Neighbor).filter(
            Neighbor.local_device_id == device.id,
            Neighbor.local_port_id == port.id,
        ).first()
        if neighbor:
            lldp_name = neighbor.remote_system_name

    results.append(MacSearchResult(
        mac_address=entry.mac_address,
        device_id=device.id,
        device_hostname=device.hostname,
        device_ip=device.ip_address,
        port_id=port.id if port else None,
        port_name=port.port_name if port else None,
        port_index=entry.port_index,
        vlan_id=entry.vlan_id,
        ip_address=ip_override or (arp.ip_address if arp else None),
        end_host_hostname=end_host_hostname,
        port_mac_count=port_mac_count,
        last_seen=entry.last_seen,
        lldp_neighbor=lldp_name,
    ))
