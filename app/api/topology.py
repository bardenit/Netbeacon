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

    # Flag edges touching a down device so the UI can keep them drawn in red
    down_ids = {d.id for d in devices if d.poll_status == "error"}
    for e in edges:
        if e.source_device_id in down_ids or e.target_device_id in down_ids:
            e.down = True

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
    ctx = _SearchContext(db)

    # ── Search by MAC ──────────────────────────────────────────────────────
    if len(mac_normalized) >= 4 and all(c in "0123456789abcdef" for c in mac_normalized):
        for mac, entries in ctx.entries_by_mac.items():
            if mac_normalized in mac.replace(":", ""):
                best = _pick_access_entry(entries, ctx)
                if best:
                    _append_mac_result(results, best, ctx)

    # ── Search by IP ───────────────────────────────────────────────────────
    if not results:
        arp_entries = db.query(ArpEntry).filter(
            ArpEntry.ip_address.like(f"%{q}%")
        ).all()
        for arp in arp_entries:
            best = _pick_access_entry(ctx.entries_by_mac.get(arp.mac_address, []), ctx)
            if best:
                _append_mac_result(results, best, ctx, ip_override=arp.ip_address)

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
            best = _pick_access_entry(ctx.entries_by_mac.get(arp.mac_address, []), ctx)
            if best:
                _append_mac_result(results, best, ctx,
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
        best = _pick_access_entry(ctx.entries_by_mac.get(lbl.mac_address, []), ctx)
        if best:
            _append_mac_result(results, best, ctx)
            seen_macs.add(lbl.mac_address)

    return results[:200]


class _SearchContext:
    """Per-request lookup tables so search scoring never queries inside a loop."""

    def __init__(self, db: Session):
        devices = db.query(Device).all()
        self.device_map: dict[int, Device] = {d.id: d for d in devices}
        self.known_names: set[str] = set()
        for d in devices:
            if d.hostname:
                self.known_names.add(d.hostname.lower())
            if d.snmp_name:
                self.known_names.add(d.snmp_name.lower())

        # LLDP neighbor per (device_id, port_id)
        self.neighbor_by_port: dict[tuple[int, int], Neighbor] = {}
        for n in db.query(Neighbor).all():
            if n.local_port_id is not None:
                self.neighbor_by_port[(n.local_device_id, n.local_port_id)] = n

        # All FDB entries grouped by MAC + per-port MAC counts (trunk detection)
        self.entries_by_mac: dict[str, list[MacEntry]] = {}
        self.mac_count_by_port: dict[int, int] = {}
        for e in db.query(MacEntry).all():
            self.entries_by_mac.setdefault(e.mac_address, []).append(e)
            if e.port_id is not None:
                self.mac_count_by_port[e.port_id] = self.mac_count_by_port.get(e.port_id, 0) + 1

        # Newest ARP entry per MAC
        self.arp_by_mac: dict[str, ArpEntry] = {}
        for a in db.query(ArpEntry).order_by(ArpEntry.last_seen.asc()).all():
            self.arp_by_mac[a.mac_address] = a


def _pick_access_entry(entries: list, ctx: _SearchContext):
    """Given multiple MacEntry records for the same MAC, return the access-port one.

    Trunk ports (LLDP neighbor is a known managed switch) are deprioritized.
    AP uplinks (LLDP neighbor exists but isn't a managed switch) are treated as
    access ports — that's where the wireless client actually is.
    """
    if not entries:
        return None
    if len(entries) == 1:
        return entries[0]

    def score(entry):
        port = entry.port
        if not port:
            return (1, 999999)
        mac_count = ctx.mac_count_by_port.get(port.id, 0)
        neighbor = ctx.neighbor_by_port.get((entry.device_id, port.id))
        # Only penalise if the LLDP peer is a switch we manage — not an AP
        is_switch_trunk = (
            neighbor is not None and
            (neighbor.remote_system_name or '').lower() in ctx.known_names
        )
        return (int(is_switch_trunk), mac_count)

    return min(entries, key=score)


def _append_mac_result(results: list, entry: MacEntry, ctx: _SearchContext,
                       ip_override: str | None = None,
                       hostname_override: str | None = None):
    device = ctx.device_map.get(entry.device_id)
    if not device:
        return

    port = entry.port
    arp = None
    if not ip_override:
        arp = ctx.arp_by_mac.get(entry.mac_address)

    end_host_hostname = hostname_override or (arp.hostname if arp else None)

    # Count total MACs on this port (for trunk detection)
    port_mac_count = None
    if port:
        port_mac_count = ctx.mac_count_by_port.get(port.id, 0)

    # Check for LLDP neighbor on this port
    lldp_name = None
    if port:
        neighbor = ctx.neighbor_by_port.get((device.id, port.id))
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
