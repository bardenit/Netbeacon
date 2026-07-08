"""
APScheduler-based poll scheduler and poll engine.

Poll logic:
  1. Load all devices from DB
  2. For each device, run the appropriate collector in a thread pool
  3. Persist results back to DB
  4. Update device poll_status / last_polled
"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any



from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.orm import Session

from app import stream
from app.config import get_config
from app.database import SessionLocal
from app.alerts import send_alert
from app.models import ArpEntry, Device, Event, MacEntry, Neighbor, Port, PortMacHistory, PortStat, PortVlan, Vlan
from app.collectors import get_collector
from app.collectors.base import CollectorResult, bitmap_to_port_set

logger = logging.getLogger(__name__)


def _classify_poll_error(exc: Exception) -> str:
    """Return a safe, non-revealing error string for storage in the database."""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "SNMP timeout — device unreachable or overloaded"
    if isinstance(exc, OSError):
        return f"Network error ({type(exc).__name__})"
    return f"Poll error ({type(exc).__name__})"

_scheduler: AsyncIOScheduler | None = None
_executor = ThreadPoolExecutor(max_workers=10)
_last_poll_time: datetime | None = None       # last FULL poll cycle
_last_status_poll_time: datetime | None = None
_poll_lock = asyncio.Lock()


def _intervals() -> dict[str, int]:
    cfg = get_config()
    return {
        "full_active_s": int(cfg.get("poll_interval_minutes", 15)) * 60,
        "full_idle_s": int(cfg.get("idle_poll_interval_minutes", 60)) * 60,
        "status_active_s": int(cfg.get("fast_poll_seconds", 60)),
        "status_idle_s": int(cfg.get("idle_status_poll_minutes", 5)) * 60,
    }


def get_scheduler_state() -> dict[str, Any]:
    iv = _intervals()
    watched = stream.is_watched()
    full_interval_s = iv["full_active_s"] if watched else iv["full_idle_s"]
    next_poll = (_last_poll_time + timedelta(seconds=full_interval_s)) if _last_poll_time else None
    return {
        "last_poll_time": _last_poll_time,
        "next_poll_time": next_poll,
        "poll_interval_minutes": get_config().get("poll_interval_minutes", 15),
        "watched": watched,
        "watchers": stream.watcher_count(),
        "last_status_poll_time": _last_status_poll_time,
    }


async def _tick():
    """Master cadence decision, runs every fast_poll_seconds.

    Watched (a browser holds the SSE stream, or within grace period):
      full poll every poll_interval_minutes, light status poll every tick.
    Idle: full poll every idle_poll_interval_minutes, status poll every
      idle_status_poll_minutes (keeps device/port-down events and alerts timely).
    """
    if _poll_lock.locked():
        return  # previous cycle still running — never stack polls
    iv = _intervals()
    watched = stream.is_watched()
    now = datetime.utcnow()
    # Small tolerance so a tick that fires marginally early still counts as due
    tol = timedelta(seconds=max(iv["status_active_s"] // 4, 5))

    full_every = iv["full_active_s"] if watched else iv["full_idle_s"]
    if _last_poll_time is None or now - _last_poll_time >= timedelta(seconds=full_every) - tol:
        await poll_all_devices()
        return

    status_every = iv["status_active_s"] if watched else iv["status_idle_s"]
    last_any = max(t for t in (_last_status_poll_time, _last_poll_time) if t is not None)
    if now - last_any >= timedelta(seconds=status_every) - tol:
        await poll_all_status()


def kick_status_poll():
    """Called when the first SSE client connects: refresh promptly if data is stale."""
    iv = _intervals()
    now = datetime.utcnow()
    candidates = [t for t in (_last_status_poll_time, _last_poll_time) if t is not None]
    last_any = max(candidates) if candidates else None
    if last_any is None or now - last_any >= timedelta(seconds=iv["status_active_s"]):
        asyncio.create_task(poll_all_status())


def start_scheduler():
    global _scheduler
    config = get_config()
    interval = config.get("poll_interval_minutes", 15)
    fast_s = max(int(config.get("fast_poll_seconds", 60)), 15) if config.get("fast_poll_seconds", 60) else 0

    _scheduler = AsyncIOScheduler()
    if fast_s:
        _scheduler.add_job(
            _tick,
            trigger="interval",
            seconds=fast_s,
            id="poll_tick",
            next_run_time=datetime.now(),  # immediate full poll on startup
        )
        logger.info(
            "Adaptive polling: full every %dm (idle %dm), status every %ds (idle %dm)",
            interval, config.get("idle_poll_interval_minutes", 60),
            fast_s, config.get("idle_status_poll_minutes", 5),
        )
    else:
        # fast_poll_seconds=0 disables the adaptive tier — classic fixed-interval mode
        _scheduler.add_job(
            poll_all_devices,
            trigger="interval",
            minutes=interval,
            id="poll_all",
            next_run_time=datetime.now(),
        )
        logger.info("Fixed polling every %d minutes (fast poll disabled)", interval)

    # Daily digest email — 7am UTC
    from app.alerts import send_digest
    _scheduler.add_job(
        lambda: send_digest("Daily"),
        trigger="cron",
        hour=7,
        minute=0,
        id="daily_digest",
    )
    logger.info("Daily digest email scheduled at 07:00 UTC")

    _scheduler.start()


def stop_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


# ── Poll functions ────────────────────────────────────────────────────────────

async def poll_all_devices():
    """Poll every device in the database concurrently (full walk)."""
    global _last_poll_time, _last_status_poll_time

    async with _poll_lock:
        _last_poll_time = datetime.utcnow()
        _last_status_poll_time = _last_poll_time  # a full poll includes status data
        logger.info("=== Poll cycle started at %s ===", _last_poll_time.isoformat())

        with SessionLocal() as db:
            devices = db.query(Device).all()

        if not devices:
            logger.info("No devices configured — nothing to poll")
            return

        tasks = [poll_device(device.id) for device in devices]
        await asyncio.gather(*tasks, return_exceptions=True)

        # Global prune once per poll cycle to keep DB lean
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(_executor, _prune_stale_data)

        logger.info("=== Poll cycle complete ===")
    stream.publish({"type": "cycle_complete", "scope": "full"})


async def poll_all_status():
    """Light status poll of every device (interface status/counters only)."""
    global _last_status_poll_time

    async with _poll_lock:
        _last_status_poll_time = datetime.utcnow()
        logger.info("--- Status poll started ---")

        with SessionLocal() as db:
            devices = db.query(Device).all()
        if not devices:
            return

        tasks = [poll_device_status(device.id) for device in devices]
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("--- Status poll complete ---")
    stream.publish({"type": "cycle_complete", "scope": "status"})


async def poll_device_status(device_id: int) -> bool:
    """Light poll of one device: interface table only. Returns True on success."""
    with SessionLocal() as db:
        device = db.get(Device, device_id)
        if not device:
            return False
        is_first_poll = device.last_polled is None
        prev_poll_status = device.poll_status
        prev_port_oper = {p.port_index: p.oper_status for p in device.ports}
        prev_health = _snapshot_health(device)
        host = device.ip_address
        community = device.snmp_community
        version = device.snmp_version or "2c"
        vendor = device.vendor
        v3_params = None
        if version == "3" and device.snmp_v3_username:
            v3_params = {
                "username":      device.snmp_v3_username,
                "auth_protocol": device.snmp_v3_auth_protocol or "SHA",
                "auth_password": device.snmp_v3_auth_password or "",
                "priv_protocol": device.snmp_v3_priv_protocol or "AES",
                "priv_password": device.snmp_v3_priv_password or "",
            }

    try:
        collector_class = get_collector(vendor)
        collector = collector_class(host=host, community=community, version=version,
                                    v3_params=v3_params)
        result: CollectorResult = await collector.collect_status()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(_executor, _persist_status_result, device_id, result)

        if result.partial and not result.ports:
            status = "error"
            error_msg = ("; ".join(result.errors))[:300] if result.errors else None
        else:
            # A status poll can't observe the walks that caused "degraded" —
            # keep that state until the next full poll clears it.
            status = "degraded" if prev_poll_status == "degraded" else "ok"
            error_msg = None

    except Exception as e:
        logger.error("Status poll failed for device %d (%s): %s", device_id, host, e)
        status = "error"
        error_msg = _classify_poll_error(e)
        result = None

    with SessionLocal() as db:
        device = db.get(Device, device_id)
        if device:
            device.last_polled = datetime.utcnow()
            device.poll_status = status
            device.poll_error = error_msg
            db.commit()

    # Device up/down and port up/down events only (empty MAC/neighbor baselines
    # make the MAC-churn and topology diffs no-ops)
    if not is_first_poll:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            _executor, _generate_events,
            device_id, prev_poll_status, status, prev_port_oper, set(), set(), result,
            prev_health,
        )

    stream.publish({"type": "device_polled", "scope": "status",
                    "device_id": device_id, "status": status})
    return status != "error"


def _snapshot_health(device) -> dict:
    """Pre-poll snapshot of counters/vitals used for delta-based event generation."""
    return {
        "port_err": {
            p.port_index: (p.rx_errors or 0) + (p.tx_errors or 0)
            for p in device.ports
            if p.rx_errors is not None or p.tx_errors is not None
        },
        "poe_mw": {p.port_index: p.poe_draw_mw for p in device.ports},
        "sys_uptime": device.sys_uptime,
        "fans_ok": device.fans_ok,
        "psu_ok": device.psu_ok,
        "cpu_util": device.cpu_util,
        "stp_top_changes": device.stp_top_changes,
    }


def _counter_delta(prev_a, prev_b, new_a, new_b) -> int | None:
    """Positive delta of a paired counter, or None if unknown/reset (wrap guard)."""
    if (new_a is None and new_b is None) or (prev_a is None and prev_b is None):
        return None
    d = ((new_a or 0) + (new_b or 0)) - ((prev_a or 0) + (prev_b or 0))
    return d if d > 0 else None


def _apply_port_health(port: Port, pd, now: datetime, rebooted: bool):
    """Flap detection, error/discard activity stamps, and max-speed tracking.

    Shared by the full and light persist paths. Must run BEFORE the new
    counter values are copied onto the Port row (deltas need the old values).
    """
    prev_oper = port.oper_status
    if prev_oper is not None and pd.oper_status is not None and prev_oper != pd.oper_status:
        port.flap_count = (port.flap_count or 0) + 1
        port.last_flap_at = now
    elif (not rebooted and pd.if_last_change is not None and port.if_last_change is not None
          and pd.if_last_change > port.if_last_change):
        # Link bounced between polls: oper status looks unchanged but the
        # interface's last-change timestamp moved forward.
        port.flap_count = (port.flap_count or 0) + 1
        port.last_flap_at = now

    if _counter_delta(port.rx_errors, port.tx_errors, pd.rx_errors, pd.tx_errors):
        port.last_error_at = now
    if _counter_delta(port.rx_discards, port.tx_discards, pd.rx_discards, pd.tx_discards):
        port.last_discard_at = now

    if pd.oper_status == 1 and pd.speed and pd.speed > (port.max_speed_seen or 0):
        port.max_speed_seen = pd.speed

    # Copy health fields (only when the walk returned data — None means the
    # walk failed and we keep the previous baseline)
    for attr in ("rx_errors", "tx_errors", "rx_discards", "tx_discards",
                 "duplex", "if_last_change", "stp_state"):
        val = getattr(pd, attr)
        if val is not None:
            setattr(port, attr, val)


def _apply_device_vitals(device: Device, result: CollectorResult, now: datetime) -> bool:
    """Copy vitals onto the Device row. Returns True if the device rebooted
    since the previous poll (sysUpTime went backwards)."""
    rebooted = (result.sys_uptime is not None and device.sys_uptime is not None
                and result.sys_uptime < device.sys_uptime)
    if result.sys_uptime is not None:
        device.sys_uptime = result.sys_uptime
    updated = False
    for attr in ("cpu_util", "mem_used_pct", "temperature", "fans_ok", "psu_ok",
                 "poe_budget_w", "poe_used_w", "stp_top_changes"):
        val = getattr(result, attr)
        if val is not None:
            setattr(device, attr, val)
            updated = True
    if updated:
        device.vitals_updated_at = now
    return rebooted


def _persist_status_result(device_id: int, result: CollectorResult):
    """Update port status/speed/counters from a light poll. Runs in thread pool.

    Only touches existing Port rows — new ports (and everything else: VLANs,
    FDB, ARP, LLDP) are handled by the full poll.
    """
    now = datetime.utcnow()
    with SessionLocal() as db:
        device = db.get(Device, device_id)
        if not device:
            return
        rebooted = _apply_device_vitals(device, result, now)
        existing_ports = {p.port_index: p for p in device.ports}
        for pd in result.ports:
            port = existing_ports.get(pd.port_index)
            if not port:
                continue
            _apply_port_health(port, pd, now, rebooted)
            port.oper_status = pd.oper_status
            port.admin_status = pd.admin_status
            port.speed = pd.speed
            port.rx_bytes = pd.rx_bytes
            port.tx_bytes = pd.tx_bytes
            port.last_seen = now
        db.commit()


async def poll_device(device_id: int) -> bool:
    """Poll a single device by ID. Returns True on success."""
    # Snapshot pre-poll state for change detection
    with SessionLocal() as db:
        device = db.get(Device, device_id)
        if not device:
            logger.warning("Device %d not found", device_id)
            return False

        is_first_poll = device.last_polled is None
        prev_poll_status = device.poll_status
        prev_port_oper = {p.port_index: p.oper_status for p in device.ports}
        prev_health = _snapshot_health(device)
        prev_macs = {m.mac_address for m in device.mac_entries}
        prev_neighbors = {
            (n.local_port_index, n.remote_system_name or "")
            for n in device.neighbors_local
        }
        host = device.ip_address
        community = device.snmp_community
        version = device.snmp_version or "2c"
        vendor = device.vendor
        v3_params = None
        if version == "3" and device.snmp_v3_username:
            v3_params = {
                "username":      device.snmp_v3_username,
                "auth_protocol": device.snmp_v3_auth_protocol or "SHA",
                "auth_password": device.snmp_v3_auth_password or "",
                "priv_protocol": device.snmp_v3_priv_protocol or "AES",
                "priv_password": device.snmp_v3_priv_password or "",
            }

    logger.info("Polling device %d (%s @ %s) SNMPv%s", device_id, vendor or "unknown", host, version)

    try:
        collector_class = get_collector(vendor)
        collector = collector_class(host=host, community=community, version=version,
                                    v3_params=v3_params)

        result: CollectorResult = await collector.collect()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(_executor, _persist_result, device_id, result)

        # If partial AND we got absolutely nothing back, treat as error not degraded
        # (silent walk failures return 0 results rather than raising, masking total unreachability)
        got_nothing = result.partial and not result.ports and not result.sys_description
        status = "error" if got_nothing else ("degraded" if result.partial else "ok")
        error_msg = ("; ".join(result.errors))[:300] if result.errors else None

    except Exception as e:
        logger.error("Poll failed for device %d (%s): %s", device_id, host, e, exc_info=True)
        status = "error"
        error_msg = _classify_poll_error(e)
        result = None

    with SessionLocal() as db:
        device = db.get(Device, device_id)
        if device:
            device.last_polled = datetime.utcnow()
            device.poll_status = status
            device.poll_error = error_msg
            db.commit()

    # Generate change events (skip first poll to avoid flood of "new" events)
    if not is_first_poll:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            _executor, _generate_events,
            device_id, prev_poll_status, status, prev_port_oper, prev_macs, prev_neighbors, result,
            prev_health,
        )

    logger.info("Device %d poll_status=%s", device_id, status)
    stream.publish({"type": "device_polled", "scope": "full",
                    "device_id": device_id, "status": status})
    return status != "error"


def _persist_result(device_id: int, result: CollectorResult):
    """Write collector result to database. Runs in thread pool."""
    now = datetime.utcnow()

    with SessionLocal() as db:
        device = db.get(Device, device_id)
        if not device:
            return

        # Update device metadata
        if result.sys_description:
            device.sys_description = result.sys_description[:1000]
        if result.sys_name:
            device.snmp_name = result.sys_name  # always store SNMP sysName for LLDP matching
            if not device.hostname:
                device.hostname = result.sys_name
        device.vendor = result.vendor  # always update (clears stale vendor if detection changes)
        if result.model:
            device.model = result.model
        if result.firmware_version:
            device.firmware_version = result.firmware_version

        rebooted = _apply_device_vitals(device, result, now)

        # ── Ports ──────────────────────────────────────────────────────────
        existing_ports = {p.port_index: p for p in device.ports}

        for pd in result.ports:
            port = existing_ports.get(pd.port_index)
            if not port:
                port = Port(device_id=device_id, port_index=pd.port_index)
                db.add(port)

            _apply_port_health(port, pd, now, rebooted)

            port.port_name = pd.port_name
            port.port_description = pd.port_description
            port.oper_status = pd.oper_status
            port.admin_status = pd.admin_status
            port.speed = pd.speed
            port.rx_bytes = pd.rx_bytes
            port.tx_bytes = pd.tx_bytes
            port.last_seen = now
            if pd.poe_draw_mw is not None:
                port.poe_draw_mw = pd.poe_draw_mw

        db.flush()

        # Rebuild port lookup after flush
        db.refresh(device)
        port_by_idx = {p.port_index: p for p in device.ports}

        # ── VLANs ──────────────────────────────────────────────────────────
        existing_vlans = {v.vlan_id: v for v in device.vlans}

        new_vlan_ids = {vd.vlan_id for vd in result.vlans}

        # Remove stale VLANs
        for vlan_id, vlan in list(existing_vlans.items()):
            if vlan_id not in new_vlan_ids:
                db.delete(vlan)
                del existing_vlans[vlan_id]

        for vd in result.vlans:
            vlan = existing_vlans.get(vd.vlan_id)
            if not vlan:
                vlan = Vlan(device_id=device_id, vlan_id=vd.vlan_id)
                db.add(vlan)
                existing_vlans[vd.vlan_id] = vlan
            vlan.vlan_name = vd.vlan_name
            vlan.last_seen = now

        db.flush()
        # Use existing_vlans dict directly — already keyed by VLAN number and
        # fully up-to-date without depending on the ORM relationship cache
        vlan_by_id = existing_vlans

        # ── Port-VLAN memberships ──────────────────────────────────────────
        _update_port_vlans(db, port_by_idx, vlan_by_id, result, now)

        # ── LLDP neighbors ─────────────────────────────────────────────────
        # Use a "mark and sweep" approach: update last_seen for existing, add new,
        # then prune neighbors on this device not seen in the last 24 hours.
        existing_neighbors = db.query(Neighbor).filter(Neighbor.local_device_id == device_id).all()
        # Key: (local_port_id, remote_chassis_id, remote_port_id)
        neighbor_map = {
            (n.local_port_id, n.remote_chassis_id, n.remote_port_id): n
            for n in existing_neighbors
        }

        for nd in result.neighbors:
            lldp_ifindex = result.bridge_to_ifindex.get(nd.local_port_index, nd.local_port_index)
            port = port_by_idx.get(lldp_ifindex)
            port_id = port.id if port else None
            
            key = (port_id, nd.remote_chassis_id, nd.remote_port_id)
            neighbor = neighbor_map.get(key)
            
            if neighbor:
                neighbor.remote_system_name = nd.remote_system_name
                neighbor.remote_system_desc = nd.remote_system_desc
                neighbor.local_port_index = nd.local_port_index
                neighbor.last_seen = now
            else:
                neighbor = Neighbor(
                    local_device_id=device_id,
                    local_port_id=port_id,
                    local_port_index=nd.local_port_index,
                    remote_chassis_id=nd.remote_chassis_id,
                    remote_port_id=nd.remote_port_id,
                    remote_system_name=nd.remote_system_name,
                    remote_system_desc=nd.remote_system_desc,
                    last_seen=now,
                )
                db.add(neighbor)

        # ── MAC entries ────────────────────────────────────────────────────
        existing_macs = db.query(MacEntry).filter(MacEntry.device_id == device_id).all()
        mac_map = {(m.mac_address, m.port_id, m.vlan_id): m for m in existing_macs}
        known_macs = {m.mac_address for m in existing_macs}

        # Track which ports have active MACs this cycle for last-seen snapshot
        port_active_macs: dict[int, list[MacEntry]] = {}  # port_index -> mac entries

        for md in result.mac_entries:
            port = port_by_idx.get(md.port_index)
            port_id = port.id if port else None
            key = (md.mac_address, port_id, md.vlan_id)

            mac_entry = mac_map.get(key)
            if mac_entry:
                mac_entry.last_seen = now
            else:
                mac_entry = MacEntry(
                    device_id=device_id,
                    port_id=port_id,
                    port_index=md.port_index,
                    mac_address=md.mac_address,
                    vlan_id=md.vlan_id,
                    last_seen=now,
                    first_seen=now,
                )
                db.add(mac_entry)

            if md.port_index not in port_active_macs:
                port_active_macs[md.port_index] = []
            port_active_macs[md.port_index].append(mac_entry)

        # Snapshot last-seen device per port (for down-port ghost info)
        # Build ARP lookup for quick IP/hostname resolution
        arp_by_mac = {}
        for ad in result.arp_entries:
            arp_by_mac[ad.mac_address] = ad

        for port_index, entries in port_active_macs.items():
            port = port_by_idx.get(port_index)
            if not port:
                continue
            # Pick the entry with the best info (has IP > no IP)
            best = next((e for e in entries if arp_by_mac.get(e.mac_address)), entries[0])
            arp = arp_by_mac.get(best.mac_address)
            port.last_mac = best.mac_address
            port.last_ip = arp.ip_address if arp else None
            port.last_hostname = (arp.hostname if arp else None)
            port.last_connection_at = now

            # PortMacHistory ring buffer (upsert by port+mac, keep last 10 per port)
            for mac_entry in entries:
                arp_hist = arp_by_mac.get(mac_entry.mac_address)
                existing_hist = db.query(PortMacHistory).filter(
                    PortMacHistory.port_id == port.id,
                    PortMacHistory.mac_address == mac_entry.mac_address,
                ).first()
                if existing_hist:
                    existing_hist.last_seen = now
                    if arp_hist:
                        existing_hist.ip_address = arp_hist.ip_address
                        existing_hist.hostname = arp_hist.hostname
                else:
                    from app.oui import lookup_vendor
                    db.add(PortMacHistory(
                        port_id=port.id,
                        mac_address=mac_entry.mac_address,
                        ip_address=arp_hist.ip_address if arp_hist else None,
                        hostname=arp_hist.hostname if arp_hist else None,
                        vendor=lookup_vendor(mac_entry.mac_address),
                        first_seen=now,
                        last_seen=now,
                    ))
                    # Prune to keep only 10 most recent entries per port
                    history_count = db.query(PortMacHistory).filter(
                        PortMacHistory.port_id == port.id
                    ).count()
                    if history_count > 10:
                        oldest = db.query(PortMacHistory).filter(
                            PortMacHistory.port_id == port.id
                        ).order_by(PortMacHistory.last_seen.asc()).first()
                        if oldest:
                            db.delete(oldest)

        # ── Port stats snapshot (traffic history) ────────────────────────
        for pd in result.ports:
            port = port_by_idx.get(pd.port_index)
            if port and (pd.rx_bytes is not None or pd.tx_bytes is not None):
                db.add(PortStat(
                    port_id=port.id,
                    rx_bytes=pd.rx_bytes,
                    tx_bytes=pd.tx_bytes,
                    rx_errors=pd.rx_errors,
                    tx_errors=pd.tx_errors,
                    rx_discards=pd.rx_discards,
                    tx_discards=pd.tx_discards,
                    sampled_at=now,
                ))

        # ── ARP entries ────────────────────────────────────────────────────
        # Purge any previously stored zero-MAC entries (unresolved ARP placeholders)
        db.query(ArpEntry).filter(ArpEntry.mac_address == "00:00:00:00:00:00").delete(synchronize_session=False)

        for ad in result.arp_entries:
            existing = db.query(ArpEntry).filter(
                ArpEntry.ip_address == ad.ip_address
            ).first()
            if existing:
                existing.mac_address = ad.mac_address
                if ad.hostname:
                    existing.hostname = ad.hostname
                existing.last_seen = now
            else:
                db.add(ArpEntry(
                    ip_address=ad.ip_address,
                    mac_address=ad.mac_address,
                    hostname=ad.hostname,
                    last_seen=now,
                ))

        db.commit()
        logger.debug("Persisted results for device %d", device_id)


def _prune_stale_data():
    """Remove data not seen in the last 2 hours (global).

    Neighbor rows that touch a currently-down device (poll_status == 'error')
    are preserved so the topology keeps the dead switch's last-known links
    (drawn red) instead of dropping it out of the graph.
    """
    cutoff = datetime.utcnow() - timedelta(hours=2)
    stat_cutoff = datetime.utcnow() - timedelta(hours=48)
    with SessionLocal() as db:
        down_ids: set[int] = set()
        down_names: set[str] = set()
        for d in db.query(Device).all():
            if d.poll_status == "error":
                down_ids.add(d.id)
                if d.hostname:
                    down_names.add(d.hostname.lower())
                if d.snmp_name:
                    down_names.add(d.snmp_name.lower())

        for n in db.query(Neighbor).filter(Neighbor.last_seen < cutoff).all():
            if n.local_device_id in down_ids:
                continue  # keep the down switch's own last-known links
            if (n.remote_system_name or "").lower() in down_names:
                continue  # keep the upstream link pointing at the down switch
            db.delete(n)

        db.query(MacEntry).filter(MacEntry.last_seen < cutoff).delete()
        db.query(PortStat).filter(PortStat.sampled_at < stat_cutoff).delete()
        db.commit()
    logger.info("Pruned stale records (Neighbor/MAC older than 2h, PortStat older than 48h)")


def _update_port_vlans(db: Session, port_by_idx: dict, vlan_by_id: dict,
                       result: CollectorResult, now: datetime):
    """Rebuild PortVlan entries from VLAN bitmap data and PVIDs."""
    port_ids = [p.id for p in port_by_idx.values()]
    if port_ids:
        db.query(PortVlan).filter(PortVlan.port_id.in_(port_ids)).delete(
            synchronize_session="fetch"
        )

    b2i = result.bridge_to_ifindex  # bridge port -> ifIndex
    memberships: dict[tuple[int, int], bool] = {}  # (ifindex, vlan_id) -> tagged

    for vd in result.vlans:
        egress_bridge = bitmap_to_port_set(vd.egress_ports)
        untagged_bridge = bitmap_to_port_set(vd.untagged_ports)

        # Skip if both bitmaps are empty (device doesn't populate Q-BRIDGE)
        if not egress_bridge and not untagged_bridge:
            continue

        for bridge_port in egress_bridge:
            ifindex = b2i.get(bridge_port, bridge_port)
            tagged = bridge_port not in untagged_bridge
            memberships[(ifindex, vd.vlan_id)] = tagged

    # Apply PVIDs — port_pvids already has ifIndex keys after translation in collector
    for ifindex, pvid in result.port_pvids.items():
        key = (ifindex, pvid)
        if key not in memberships:
            memberships[key] = False  # untagged native VLAN

    for (ifindex, vlan_id), tagged in memberships.items():
        port = port_by_idx.get(ifindex)
        vlan = vlan_by_id.get(vlan_id)
        if port and vlan:
            db.add(PortVlan(port_id=port.id, vlan_id=vlan.id, tagged=tagged, last_seen=now))


def _generate_events(
    device_id: int,
    prev_poll_status: str,
    new_poll_status: str,
    prev_port_oper: dict[int, int | None],
    prev_macs: set[str],
    prev_neighbors: set[tuple],
    result,  # CollectorResult or None if poll failed
    prev_health: dict | None = None,
):
    """Detect changes vs previous poll and write Event rows. Runs in thread pool."""
    now = datetime.utcnow()
    events: list[Event] = []

    # ── Device status change ───────────────────────────────────────────────
    if prev_poll_status != new_poll_status:
        if new_poll_status in ("ok", "degraded") and prev_poll_status == "error":
            events.append(Event(
                device_id=device_id, event_type="device_up",
                detail=f"Recovered: status is now {new_poll_status}", created_at=now,
            ))
        elif new_poll_status == "error" and prev_poll_status != "error":
            events.append(Event(
                device_id=device_id, event_type="device_down",
                detail=f"Unreachable (was {prev_poll_status})", created_at=now,
            ))
        elif new_poll_status == "degraded" and prev_poll_status == "ok":
            events.append(Event(
                device_id=device_id, event_type="device_degraded",
                detail="Poll returned partial data", created_at=now,
            ))

    if result is not None:
        # ── Port status changes ────────────────────────────────────────────
        for pd in result.ports:
            prev_oper = prev_port_oper.get(pd.port_index)
            if prev_oper is None:
                continue  # new port, skip
            if prev_oper == pd.oper_status:
                continue
            port_label = pd.port_name or str(pd.port_index)
            if pd.oper_status == 1:
                events.append(Event(
                    device_id=device_id, event_type="port_up",
                    detail=f"Port {port_label} came up", created_at=now,
                ))
            elif pd.oper_status == 2:
                events.append(Event(
                    device_id=device_id, event_type="port_down",
                    detail=f"Port {port_label} went down", created_at=now,
                ))

        # ── Port health: error bursts ──────────────────────────────────────
        PORT_ERROR_EVENT_THRESHOLD = 10
        prev_err = (prev_health or {}).get("port_err", {})
        for pd in result.ports:
            prev_total = prev_err.get(pd.port_index)
            if prev_total is None or (pd.rx_errors is None and pd.tx_errors is None):
                continue
            delta = (pd.rx_errors or 0) + (pd.tx_errors or 0) - prev_total
            if delta >= PORT_ERROR_EVENT_THRESHOLD:
                port_label = pd.port_name or str(pd.port_index)
                events.append(Event(
                    device_id=device_id, event_type="port_errors",
                    detail=f"Port {port_label}: {delta} interface errors since last poll",
                    created_at=now,
                ))

        # ── PoE device stopped drawing power ───────────────────────────────
        # Only when this poll returned PoE data at all (walk failure ≠ power loss)
        if any(pd.poe_draw_mw is not None for pd in result.ports):
            prev_poe = (prev_health or {}).get("poe_mw", {})
            for pd in result.ports:
                prev_mw = prev_poe.get(pd.port_index) or 0
                if prev_mw >= 1000 and (pd.poe_draw_mw or 0) == 0 and pd.oper_status == 1:
                    port_label = pd.port_name or str(pd.port_index)
                    events.append(Event(
                        device_id=device_id, event_type="poe_dropped",
                        detail=f"Port {port_label}: PoE device stopped drawing power (was {prev_mw} mW)",
                        created_at=now,
                    ))

        # ── Switch vitals ──────────────────────────────────────────────────
        if prev_health:
            prev_uptime = prev_health.get("sys_uptime")
            if (prev_uptime is not None and result.sys_uptime is not None
                    and result.sys_uptime < prev_uptime):
                events.append(Event(
                    device_id=device_id, event_type="device_rebooted",
                    detail=f"Switch rebooted — uptime reset ({result.sys_uptime // 6000} min ago)",
                    created_at=now,
                ))

            for attr, etype, label in (("fans_ok", "fan", "Fan"),
                                       ("psu_ok", "psu", "Power supply")):
                prev_ok = prev_health.get(attr)
                new_ok = getattr(result, attr)
                if prev_ok is None or new_ok is None or bool(prev_ok) == bool(new_ok):
                    continue
                if not new_ok:
                    events.append(Event(
                        device_id=device_id, event_type=f"{etype}_failure",
                        detail=f"{label} failure reported", created_at=now,
                    ))
                else:
                    events.append(Event(
                        device_id=device_id, event_type=f"{etype}_recovered",
                        detail=f"{label} recovered", created_at=now,
                    ))

            HIGH_CPU_THRESHOLD = 90
            prev_cpu = prev_health.get("cpu_util")
            if (result.cpu_util is not None and result.cpu_util >= HIGH_CPU_THRESHOLD
                    and (prev_cpu is None or prev_cpu < HIGH_CPU_THRESHOLD)):
                events.append(Event(
                    device_id=device_id, event_type="high_cpu",
                    detail=f"Management CPU at {result.cpu_util}%", created_at=now,
                ))

            STP_TCN_THRESHOLD = 5
            prev_tcn = prev_health.get("stp_top_changes")
            if result.stp_top_changes is not None and prev_tcn is not None:
                tcn_delta = result.stp_top_changes - prev_tcn
                if tcn_delta >= STP_TCN_THRESHOLD:
                    events.append(Event(
                        device_id=device_id, event_type="stp_topology_change",
                        detail=f"{tcn_delta} STP topology changes since last poll",
                        created_at=now,
                    ))

        # ── MAC address changes ────────────────────────────────────────────
        # Individual MAC events are too noisy (DHCP churn, sleeping clients, etc.)
        # Only emit a summary when a large batch changes at once (indicates something
        # significant like a port flap, switch reload, or VLAN change).
        new_macs = {md.mac_address for md in result.mac_entries}
        appeared = new_macs - prev_macs
        disappeared = prev_macs - new_macs
        MAC_SUMMARY_THRESHOLD = 20
        if len(appeared) >= MAC_SUMMARY_THRESHOLD:
            events.append(Event(
                device_id=device_id, event_type="mac_appeared",
                detail=f"{len(appeared)} new MAC addresses", created_at=now,
            ))
        if len(disappeared) >= MAC_SUMMARY_THRESHOLD:
            events.append(Event(
                device_id=device_id, event_type="mac_disappeared",
                detail=f"{len(disappeared)} MAC addresses removed", created_at=now,
            ))

        # ── Topology changes (LLDP neighbor added/removed) ────────────────
        cur_neighbors = {
            (nd.local_port_index, nd.remote_system_name or "")
            for nd in result.neighbors
        }
        for (port_idx, sys_name) in (cur_neighbors - prev_neighbors):
            if sys_name:
                events.append(Event(
                    device_id=device_id, event_type="topology_change",
                    detail=f"New neighbor: {sys_name} on port {port_idx}", created_at=now,
                ))
        for (port_idx, sys_name) in (prev_neighbors - cur_neighbors):
            if sys_name:
                events.append(Event(
                    device_id=device_id, event_type="topology_change",
                    detail=f"Neighbor removed: {sys_name} from port {port_idx}", created_at=now,
                ))

        # ── New device detection ───────────────────────────────────────────
        new_macs_set = {md.mac_address for md in result.mac_entries} - prev_macs
        if new_macs_set:
            with SessionLocal() as db:
                from datetime import timedelta as td
                # A MAC is "new" if it was just created (first_seen within last 2 poll cycles)
                # Use 30 min window to be safe across poll intervals
                window = datetime.utcnow() - td(minutes=30)
                from app.models import MacEntry as ME
                brand_new = db.query(ME).filter(
                    ME.device_id == device_id,
                    ME.mac_address.in_(new_macs_set),
                    ME.first_seen >= window,
                    ME.first_seen == ME.last_seen,
                ).all()
                for m in brand_new[:10]:  # cap at 10 individual new-device events
                    events.append(Event(
                        device_id=device_id, event_type="new_device",
                        detail=f"New device: {m.mac_address}", created_at=now,
                    ))

    if not events:
        return

    # Resolve device name for alert messages
    device_name = f"Device {device_id}"
    with SessionLocal() as db:
        d = db.get(Device, device_id)
        if d:
            device_name = d.snmp_name or d.hostname

    # Fire alerts for high-signal events (device status changes only — not MAC churn)
    alert_event_types = {"device_up", "device_down", "device_degraded",
                         "device_rebooted", "fan_failure", "psu_failure", "high_cpu"}
    for ev in events:
        if ev.event_type in alert_event_types:
            send_alert(device_id, device_name, ev.event_type, ev.detail or "")

    with SessionLocal() as db:
        # Prune old events: keep only most recent 2000
        count = db.query(Event).count()
        if count + len(events) > 2000:
            cutoff_id = (
                db.query(Event.id)
                .order_by(Event.created_at.desc())
                .offset(1800)
                .limit(1)
                .scalar()
            )
            if cutoff_id:
                db.query(Event).filter(Event.id <= cutoff_id).delete()
        for ev in events:
            db.add(ev)
        db.commit()
        logger.debug("Generated %d event(s) for device %d", len(events), device_id)
