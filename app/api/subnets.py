"""Subnet management — store and discover IP subnets for utilization tracking."""
from __future__ import annotations

import ipaddress
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ArpEntry, Subnet

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/subnets", tags=["subnets"])


class SubnetIn(BaseModel):
    cidr: str = Field(..., max_length=43)  # longest valid CIDR: "xxxx:xxxx:.../128"
    name: str = Field(default="", max_length=100)


@router.get("")
def list_subnets(db: Session = Depends(get_db)):
    rows = db.query(Subnet).order_by(Subnet.cidr).all()
    return [{"id": r.id, "cidr": r.cidr, "name": r.name} for r in rows]


@router.post("", status_code=201)
def add_subnet(body: SubnetIn, db: Session = Depends(get_db)):
    try:
        net = ipaddress.ip_network(body.cidr, strict=False)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid CIDR: {body.cidr}")

    canonical = str(net)
    existing = db.query(Subnet).filter(Subnet.cidr == canonical).first()
    if existing:
        raise HTTPException(status_code=409, detail="Subnet already exists")

    row = Subnet(cidr=canonical, name=body.name.strip() or None)
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "cidr": row.cidr, "name": row.name}


@router.delete("/{subnet_id}", status_code=204)
def delete_subnet(subnet_id: int, db: Session = Depends(get_db)):
    row = db.query(Subnet).filter(Subnet.id == subnet_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Subnet not found")
    db.delete(row)
    db.commit()


@router.get("/discovered")
def discovered_subnets(db: Session = Depends(get_db)):
    """Return /24 groups inferred from the ARP table as subnet suggestions."""
    entries = db.query(ArpEntry).all()
    groups: dict[str, list[str]] = {}
    for entry in entries:
        try:
            net = str(ipaddress.ip_network(f"{entry.ip_address}/24", strict=False))
        except ValueError:
            continue
        groups.setdefault(net, []).append(entry.ip_address)

    # Also pull already-configured subnets so the UI can mark them
    configured = {r.cidr for r in db.query(Subnet).all()}

    result = []
    for net_str, ips in sorted(groups.items()):
        net = ipaddress.ip_network(net_str)
        result.append({
            "suggested_cidr": net_str,
            "ip_count": len(ips),
            "sample_ips": sorted(ips, key=ipaddress.ip_address)[:5],
            "already_configured": any(
                ipaddress.ip_network(c, strict=False).overlaps(net)
                for c in configured
            ),
        })

    result.sort(key=lambda x: x["ip_count"], reverse=True)
    return result
