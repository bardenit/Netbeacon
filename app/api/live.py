"""Server-Sent Events endpoint for live UI updates after poll cycles.

EventSource cannot send an Authorization header, and a JWT in the query string
would land in access logs — so the client first exchanges its JWT for a
short-lived one-time ticket, then connects with that.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app import stream
from app.api.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/stream", tags=["stream"])

_TICKET_TTL = timedelta(seconds=30)
_tickets: dict[str, datetime] = {}  # ticket -> expiry

_KEEPALIVE_SECONDS = 25


@router.post("/ticket", dependencies=[Depends(get_current_user)])
def create_ticket():
    # Opportunistic cleanup of expired tickets
    now = datetime.utcnow()
    for t in [t for t, exp in _tickets.items() if exp < now]:
        _tickets.pop(t, None)
    ticket = secrets.token_urlsafe(24)
    _tickets[ticket] = now + _TICKET_TTL
    return {"ticket": ticket}


@router.get("")
async def event_stream(ticket: str = Query(...)):
    expiry = _tickets.pop(ticket, None)  # single-use
    if expiry is None or expiry < datetime.utcnow():
        raise HTTPException(status_code=401, detail="Invalid or expired stream ticket")

    q = stream.subscribe()
    from app.scheduler import kick_status_poll
    kick_status_poll()

    async def gen():
        try:
            yield "retry: 5000\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=_KEEPALIVE_SECONDS)
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            stream.unsubscribe(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable buffering if behind nginx
        },
    )
