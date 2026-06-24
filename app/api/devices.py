"""Device CRUD and per-device poll endpoints."""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Device
from app.oui import lookup_vendor
from app.schemas import DeviceCreate, DeviceOut, DeviceUpdate, ScanRequest, ScanDiscovery, ConnectionTestRequest, ConnectionTestResult, PortNotesUpdate, PortTypeUpdate
from app.scheduler import poll_device

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/devices", tags=["devices"])


def _device_out(device: Device) -> DeviceOut:
    return DeviceOut.from_device(device)


@router.get("", response_model=list[DeviceOut])
def list_devices(db: Session = Depends(get_db)):
    return [_device_out(d) for d in db.query(Device).order_by(Device.hostname).all()]


@router.post("", response_model=DeviceOut, status_code=201)
def create_device(payload: DeviceCreate, db: Session = Depends(get_db)):
    existing = db.query(Device).filter(Device.ip_address == payload.ip_address).first()
    if existing:
        raise HTTPException(status_code=409, detail="Device with that IP already exists")
    device = Device(**payload.model_dump())
    db.add(device)
    db.commit()
    db.refresh(device)
    logger.info("Added device %s (%s)", device.hostname, device.ip_address)
    return _device_out(device)


@router.get("/{device_id}", response_model=DeviceOut)
def get_device(device_id: int, db: Session = Depends(get_db)):
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return _device_out(device)


@router.put("/{device_id}", response_model=DeviceOut)
def update_device(device_id: int, payload: DeviceUpdate, db: Session = Depends(get_db)):
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(device, field, value)
    db.commit()
    db.refresh(device)
    return _device_out(device)


@router.delete("/{device_id}", status_code=204)
def delete_device(device_id: int, db: Session = Depends(get_db)):
    from sqlalchemy import func
    from app.models import Neighbor

    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    # Suppress phantom resurrection: drop this device's own LLDP rows and any
    # rows on other switches that advertise it by name, so a deleted switch
    # doesn't reappear as an unmanaged phantom node in the topology.
    names = {n.lower() for n in (device.hostname, device.snmp_name) if n}
    db.query(Neighbor).filter(Neighbor.local_device_id == device_id).delete(
        synchronize_session=False)
    if names:
        db.query(Neighbor).filter(
            func.lower(Neighbor.remote_system_name).in_(names)
        ).delete(synchronize_session=False)

    db.delete(device)
    db.commit()
    logger.info("Deleted device %d", device_id)


@router.post("/{device_id}/gateway", response_model=DeviceOut)
def set_gateway(device_id: int, db: Session = Depends(get_db)):
    """Toggle this device as the gateway switch (clears the flag on all others first)."""
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    # If already gateway, just clear it; otherwise set only this one within the same site
    if device.is_gateway:
        device.is_gateway = False
    else:
        # Clear only devices in the same site (or same "no site" group)
        q = db.query(Device).filter(Device.site == device.site)
        q.update({Device.is_gateway: False})
        device.is_gateway = True
    db.commit()
    db.refresh(device)
    return _device_out(device)


@router.post("/{device_id}/poll", status_code=202)
async def trigger_device_poll(device_id: int, db: Session = Depends(get_db)):
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    asyncio.create_task(poll_device(device_id))
    return {"message": f"Poll triggered for device {device_id}", "device_id": device_id}


@router.get("/{device_id}/ports")
def get_device_ports(device_id: int, db: Session = Depends(get_db)):
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    ports_out = []
    from app.models import Neighbor, MacEntry as ME
    for port in sorted(device.ports, key=lambda p: p.port_index):
        vlans = []
        for pv in port.port_vlans:
            if pv.vlan:
                vlans.append({
                    "vlan_id": pv.vlan.vlan_id,
                    "vlan_name": pv.vlan.vlan_name,
                    "tagged": pv.tagged,
                })

        neighbor = db.query(Neighbor).filter(
            Neighbor.local_device_id == device_id,
            Neighbor.local_port_id == port.id,
        ).first()

        ports_out.append({
            "id": port.id,
            "device_id": port.device_id,
            "port_index": port.port_index,
            "port_name": port.port_name,
            "port_description": port.port_description,
            "oper_status": port.oper_status,
            "admin_status": port.admin_status,
            "speed": port.speed,
            "rx_bytes": port.rx_bytes,
            "tx_bytes": port.tx_bytes,
            "rx_errors": port.rx_errors,
            "tx_errors": port.tx_errors,
            "last_seen": port.last_seen,
            "last_mac": port.last_mac,
            "last_hostname": port.last_hostname,
            "last_ip": port.last_ip,
            "last_connection_at": port.last_connection_at,
            "flap_count": port.flap_count or 0,
            "last_flap_at": port.last_flap_at,
            "notes": port.notes,
            "port_type": port.port_type,
            "poe_draw_mw": port.poe_draw_mw,
            "vlans": vlans,
            "mac_count": len(port.mac_entries),
            "lldp_neighbor": neighbor.remote_system_name if neighbor else None,
            "lldp_neighbor_chassis_id": neighbor.remote_chassis_id if neighbor else None,
            "lldp_neighbor_vendor": lookup_vendor(neighbor.remote_chassis_id) if neighbor and neighbor.remote_chassis_id else None,
        })

    return ports_out


@router.post("/test", response_model=ConnectionTestResult)
async def test_connection(payload: ConnectionTestRequest):
    """Test SNMP connectivity to a device without saving it."""
    from app.collectors.base import snmp_probe, BaseCollector
    v3_params = None
    if payload.snmp_version == "3" and payload.snmp_v3_username:
        v3_params = {
            "username":      payload.snmp_v3_username,
            "auth_protocol": payload.snmp_v3_auth_protocol or "SHA",
            "auth_password": payload.snmp_v3_auth_password or "",
            "priv_protocol": payload.snmp_v3_priv_protocol or "AES",
            "priv_password": payload.snmp_v3_priv_password or "",
        }
    try:
        data = await snmp_probe(
            payload.ip_address, payload.snmp_community or "public",
            payload.snmp_version or "2c", v3_params, timeout=5,
            retries=1 if payload.snmp_version == "3" else 0,
        )
    except Exception as e:
        return ConnectionTestResult(reachable=False, error=f"Connection failed ({type(e).__name__})")

    if data is None:
        return ConnectionTestResult(
            reachable=False,
            error="No SNMP response — check IP, community string, and that SNMP is enabled",
        )

    desc = data.get("1.3.6.1.2.1.1.1.0", "") or ""
    name = data.get("1.3.6.1.2.1.1.5.0", "") or ""
    vendor, _, _ = BaseCollector(host=payload.ip_address,
                                  community=payload.snmp_community or "public")._parse_sys_description(desc)
    return ConnectionTestResult(
        reachable=True,
        sys_name=name.strip() or None,
        sys_description=desc.strip()[:120] or None,
        vendor=vendor,
    )


@router.patch("/{device_id}/ports/{port_id}/notes")
def update_port_notes(device_id: int, port_id: int, payload: PortNotesUpdate, db: Session = Depends(get_db)):
    from app.models import Port as PortModel
    port = db.get(PortModel, port_id)
    if not port or port.device_id != device_id:
        raise HTTPException(status_code=404, detail="Port not found")
    port.notes = payload.notes
    db.commit()
    return {"ok": True}


@router.get("/{device_id}/ports/{port_id}/mac-history")
def get_port_mac_history(device_id: int, port_id: int, db: Session = Depends(get_db)):
    from app.models import Port as PortModel, PortMacHistory
    port = db.get(PortModel, port_id)
    if not port or port.device_id != device_id:
        raise HTTPException(status_code=404, detail="Port not found")
    rows = (
        db.query(PortMacHistory)
        .filter(PortMacHistory.port_id == port_id)
        .order_by(PortMacHistory.last_seen.desc())
        .all()
    )
    return [
        {
            "mac_address": r.mac_address,
            "ip_address": r.ip_address,
            "hostname": r.hostname,
            "vendor": r.vendor,
            "first_seen": r.first_seen,
            "last_seen": r.last_seen,
        }
        for r in rows
    ]


@router.patch("/{device_id}/ports/{port_id}/type")
def set_port_type(device_id: int, port_id: int, body: PortTypeUpdate, db: Session = Depends(get_db)):
    from app.models import Port as PortModel
    port = db.query(PortModel).filter(PortModel.id == port_id, PortModel.device_id == device_id).first()
    if not port:
        raise HTTPException(status_code=404, detail="Port not found")
    port.port_type = body.port_type
    db.commit()
    return {"port_id": port_id, "port_type": port.port_type}


@router.post("/scan", response_model=list[ScanDiscovery])
async def scan_network(body: ScanRequest, db: Session = Depends(get_db)):
    """Scan a CIDR range for SNMP-responding devices."""
    import ipaddress
    try:
        net = ipaddress.ip_network(body.cidr, strict=False)
    except ValueError as e:
        raise HTTPException(400, f"Invalid CIDR: {e}")
    if net.num_addresses > 1024:
        raise HTTPException(400, "CIDR range too large — max /22 (1024 addresses)")

    existing_ips = {d.ip_address for d in db.query(Device).all()}
    from app.collectors.base import snmp_probe, BaseCollector

    sem = asyncio.Semaphore(30)

    async def probe(ip: str):
        async with sem:
            data = await snmp_probe(ip, body.community, body.version)
            if data is None:
                return None
            desc = data.get("1.3.6.1.2.1.1.1.0", "") or ""
            name = data.get("1.3.6.1.2.1.1.5.0", "") or ""
            vendor, _, _ = BaseCollector(host=ip, community=body.community)._parse_sys_description(desc)
            return ScanDiscovery(
                ip_address=ip,
                sys_name=name.strip() or None,
                sys_description=desc.strip()[:200] or None,
                vendor=vendor,
                already_added=ip in existing_ips,
            )

    tasks = [probe(str(ip)) for ip in net.hosts()]
    results = await asyncio.gather(*tasks)
    found = [r for r in results if r is not None]
    found.sort(key=lambda r: tuple(int(x) for x in r.ip_address.split(".")))
    logger.info("Scan of %s: %d/%d hosts responded to SNMP", body.cidr, len(found), net.num_addresses - 2)
    return found


@router.get("/{device_id}/fdb")
def get_device_fdb(device_id: int, db: Session = Depends(get_db)):
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    from app.models import MacEntry, ArpEntry
    entries = db.query(MacEntry).filter(MacEntry.device_id == device_id).all()

    # Cross-reference ARP for IPs
    result = []
    for entry in entries:
        arp = db.query(ArpEntry).filter(
            ArpEntry.mac_address == entry.mac_address
        ).order_by(ArpEntry.last_seen.desc()).first()
        result.append({
            "mac_address": entry.mac_address,
            "port_id": entry.port_id,
            "port_index": entry.port_index,
            "port_name": entry.port.port_name if entry.port else None,
            "vlan_id": entry.vlan_id,
            "ip_address": arp.ip_address if arp else None,
            "hostname": arp.hostname if arp else None,
            "vendor": lookup_vendor(entry.mac_address),
            "last_seen": entry.last_seen,
        })

    return result


@router.delete("/maintenance/stale-data")
def purge_stale_data(older_than_days: int = Query(default=7, ge=1), db: Session = Depends(get_db)):
    """Delete mac_entries and arp_entries not seen within the last N days."""
    from datetime import datetime, timedelta
    from app.models import MacEntry, ArpEntry
    cutoff = datetime.utcnow() - timedelta(days=older_than_days)
    mac_deleted = db.query(MacEntry).filter(MacEntry.last_seen < cutoff).delete(synchronize_session=False)
    arp_deleted = db.query(ArpEntry).filter(ArpEntry.last_seen < cutoff).delete(synchronize_session=False)
    db.commit()
    return {"mac_entries_deleted": mac_deleted, "arp_entries_deleted": arp_deleted, "older_than_days": older_than_days}
