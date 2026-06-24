from __future__ import annotations

from app.collectors.extreme import ExtremeCollector
from app.collectors.netgear import NetgearCollector
from app.collectors.base import BaseCollector

__all__ = ["BaseCollector", "ExtremeCollector", "NetgearCollector"]


def get_collector(vendor: str | None) -> type[BaseCollector]:
    """Return the appropriate collector class for a given vendor string."""
    if vendor and "extreme" in vendor.lower():
        return ExtremeCollector
    if vendor and "netgear" in vendor.lower():
        return NetgearCollector
    # Default: generic base collector (detects vendor on first poll)
    return BaseCollector
