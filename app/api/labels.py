"""Device label (MAC → label/notes) CRUD."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
import re
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import DeviceLabel
from app.oui import lookup_vendor

router = APIRouter(prefix="/api/labels", tags=["labels"])


class LabelOut(BaseModel):
    id: int
    mac_address: str
    vendor: Optional[str] = None
    label: str
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class LabelUpsert(BaseModel):
    mac_address: str
    label: str
    notes: Optional[str] = None

    @field_validator("mac_address")
    @classmethod
    def validate_mac(cls, v: str) -> str:
        normalized = v.lower().replace("-", ":").replace(".", ":")
        if not re.fullmatch(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", normalized):
            raise ValueError("Invalid MAC address format")
        return normalized

    @field_validator("label")
    @classmethod
    def validate_label(cls, v: str) -> str:
        if len(v) > 200:
            raise ValueError("Label must be 200 characters or fewer")
        return v

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, v: Optional[str]) -> Optional[str]:
        if v and len(v) > 2000:
            raise ValueError("Notes must be 2000 characters or fewer")
        return v


def _to_out(lbl: DeviceLabel) -> LabelOut:
    return LabelOut(
        id=lbl.id,
        mac_address=lbl.mac_address,
        vendor=lookup_vendor(lbl.mac_address),
        label=lbl.label,
        notes=lbl.notes,
        created_at=lbl.created_at,
        updated_at=lbl.updated_at,
    )


@router.get("/oui")
def oui_lookup(mac: str = Query(..., min_length=12, max_length=17)):
    normalized = mac.lower().replace("-", ":").replace(".", ":")
    if not re.fullmatch(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", normalized):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Invalid MAC address format")
    return {"vendor": lookup_vendor(normalized)}


@router.get("", response_model=list[LabelOut])
def list_labels(db: Session = Depends(get_db)):
    return [_to_out(lbl) for lbl in db.query(DeviceLabel).order_by(DeviceLabel.label).all()]


@router.post("", response_model=LabelOut)
def upsert_label(payload: LabelUpsert, db: Session = Depends(get_db)):
    mac = payload.mac_address.lower().replace("-", ":").replace(".", ":")
    existing = db.query(DeviceLabel).filter(DeviceLabel.mac_address == mac).first()
    if existing:
        existing.label = payload.label
        existing.notes = payload.notes
        existing.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return _to_out(existing)
    lbl = DeviceLabel(
        mac_address=mac,
        label=payload.label,
        notes=payload.notes,
    )
    db.add(lbl)
    db.commit()
    db.refresh(lbl)
    return _to_out(lbl)


@router.delete("/{mac_address}", status_code=204)
def delete_label(mac_address: str, db: Session = Depends(get_db)):
    mac = mac_address.lower().replace("-", ":").replace(".", ":")
    lbl = db.query(DeviceLabel).filter(DeviceLabel.mac_address == mac).first()
    if not lbl:
        raise HTTPException(status_code=404, detail="Label not found")
    db.delete(lbl)
    db.commit()
