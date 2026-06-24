"""MAC OUI → vendor name lookup (lazy-loaded, best-effort)."""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)
_lookup = None   # False means init was attempted and failed


def lookup_vendor(mac: str) -> str | None:
    """Return vendor name for a MAC address, or None if unknown/unavailable."""
    global _lookup
    if _lookup is None:
        try:
            from mac_vendor_lookup import MacLookup
            _lookup = MacLookup()
        except Exception as e:
            logger.warning("OUI lookup unavailable: %s", e)
            _lookup = False
    if not _lookup:
        return None
    try:
        return _lookup.lookup(mac)
    except Exception:
        return None
