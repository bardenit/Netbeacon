"""Event history endpoint."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Device, Event

router = APIRouter(prefix="/api/events", tags=["events"])


class EventOut(BaseModel):
    id: int
    device_id: Optional[int] = None
    device_hostname: Optional[str] = None
    event_type: str
    detail: Optional[str] = None
    read: bool
    created_at: datetime

    model_config = {"from_attributes": True}


@router.get("", response_model=list[EventOut])
def list_events(limit: int = Query(default=100, le=1000), unread_only: bool = False, db: Session = Depends(get_db)):
    q = db.query(Event)
    if unread_only:
        q = q.filter(Event.read == False)  # noqa: E712
    events = q.order_by(Event.created_at.desc()).limit(limit).all()

    result = []
    # Cache device hostnames
    device_cache: dict[int, str] = {}
    for ev in events:
        if ev.device_id and ev.device_id not in device_cache:
            d = db.get(Device, ev.device_id)
            device_cache[ev.device_id] = (d.snmp_name or d.hostname) if d else f"Device {ev.device_id}"
        result.append(EventOut(
            id=ev.id,
            device_id=ev.device_id,
            device_hostname=device_cache.get(ev.device_id) if ev.device_id else None,
            event_type=ev.event_type,
            detail=ev.detail,
            read=ev.read,
            created_at=ev.created_at,
        ))
    return result


@router.get("/unread-count")
def unread_count(db: Session = Depends(get_db)):
    count = db.query(Event).filter(Event.read == False).count()  # noqa: E712
    return {"count": count}


@router.post("/mark-read", status_code=204)
def mark_all_read(db: Session = Depends(get_db)):
    db.query(Event).filter(Event.read == False).update({"read": True})  # noqa: E712
    db.commit()


@router.delete("", status_code=204)
def clear_events(db: Session = Depends(get_db)):
    db.query(Event).delete()
    db.commit()
