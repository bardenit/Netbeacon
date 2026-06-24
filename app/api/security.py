"""Security audit endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Device

router = APIRouter(prefix="/api/security", tags=["security"])

_WEAK_COMMUNITIES = {"public", "private", "community", "snmp", "admin", ""}


def _snmp_risk(device: Device) -> dict:
    version = device.snmp_version or "2c"
    community = (device.snmp_community or "").strip().lower()

    if version == "3":
        has_auth = bool(device.snmp_v3_auth_password)
        has_priv = bool(device.snmp_v3_priv_password)
        auth_proto = (device.snmp_v3_auth_protocol or "SHA").upper()
        priv_proto = (device.snmp_v3_priv_protocol or "AES").upper()

        if not has_auth:
            level = "MEDIUM"
            note = "SNMPv3 without authentication (noAuthNoPriv) — username only"
        elif not has_priv:
            level = "LOW"
            note = f"SNMPv3 authNoPriv ({auth_proto}) — no encryption"
        else:
            # Full authPriv — check for weak algorithms
            weak_auth = auth_proto == "MD5"
            weak_priv = priv_proto == "DES"
            if weak_auth or weak_priv:
                level = "LOW"
                algos = []
                if weak_auth: algos.append("MD5 auth (prefer SHA/SHA256)")
                if weak_priv: algos.append("DES encryption (prefer AES)")
                note = f"SNMPv3 authPriv with weak algorithms: {', '.join(algos)}"
            else:
                level = "OK"
                note = f"SNMPv3 authPriv ({auth_proto}/{priv_proto})"
    elif version == "1":
        level = "CRITICAL"
        note = "SNMPv1 — community string sent in cleartext, no auth"
    else:
        # v2c
        if community in _WEAK_COMMUNITIES:
            level = "HIGH"
            note = f"SNMPv2c with default/empty community string '{device.snmp_community}'"
        else:
            level = "MEDIUM"
            note = "SNMPv2c — community string sent in cleartext (upgrade to v3)"

    return {
        "device_id": device.id,
        "hostname": device.snmp_name or device.hostname,
        "ip_address": device.ip_address,
        "snmp_version": version,
        "risk_level": level,
        "note": note,
    }


@router.get("/audit")
def security_audit(db: Session = Depends(get_db)):
    """Return a security risk assessment for all configured devices."""
    devices = db.query(Device).order_by(Device.hostname).all()
    results = [_snmp_risk(d) for d in devices]

    # Order by risk: CRITICAL > HIGH > MEDIUM > LOW > OK
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "OK": 4}
    results.sort(key=lambda r: order.get(r["risk_level"], 5))

    summary = {
        level: sum(1 for r in results if r["risk_level"] == level)
        for level in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "OK")
    }

    return {"devices": results, "summary": summary}
