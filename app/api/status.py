"""Status and health endpoints."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.alerts import get_alert_config_summary, send_alert
from app.api.auth import get_current_user
from app.database import get_db
from app.models import Device, Event
from app.schemas import PollStatus
from app.scheduler import get_scheduler_state

router = APIRouter(tags=["status"])


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/api/status", response_model=PollStatus, dependencies=[Depends(get_current_user)])
def get_status(db: Session = Depends(get_db)):
    """Public summary — aggregate counts only, no device details."""
    state = get_scheduler_state()
    devices = db.query(Device).all()
    unread_count = db.query(Event).filter(Event.read == False).count()

    status_counts = {"ok": 0, "degraded": 0, "error": 0, "unknown": 0}
    for device in devices:
        s = device.poll_status or "unknown"
        status_counts[s] = status_counts.get(s, 0) + 1

    return PollStatus(
        last_poll_time=state["last_poll_time"],
        next_poll_time=state["next_poll_time"],
        poll_interval_minutes=state["poll_interval_minutes"],
        devices_total=len(devices),
        devices_ok=status_counts.get("ok", 0),
        devices_degraded=status_counts.get("degraded", 0),
        devices_error=status_counts.get("error", 0),
        devices_unknown=status_counts.get("unknown", 0),
        unread_events=unread_count,
    )


@router.get("/api/alerts/config", dependencies=[Depends(get_current_user)])
def alerts_config():
    return get_alert_config_summary()


@router.post("/api/alerts/test", status_code=202, dependencies=[Depends(get_current_user)])
def test_alert():
    send_alert(None, "NetBeacon Test", "device_up",
               "This is a test alert from NetBeacon.", force=True)
    return {"message": "Test alert dispatched"}
