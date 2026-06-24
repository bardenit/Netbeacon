"""
Netgear managed switch SNMP collector.

Netgear quirks:
  - LLDP support is inconsistent — treat as optional
  - Q-BRIDGE VLAN tables may be absent or return empty
  - Some models use ifDescr instead of ifName
"""
from __future__ import annotations

import logging
import re

from app.collectors.base import BaseCollector, CollectorResult, SNMPError

logger = logging.getLogger(__name__)


class NetgearCollector(BaseCollector):
    VENDOR_NAME = "netgear"

    def _parse_sys_description(self, desc: str) -> tuple[str | None, str | None, str | None]:
        vendor = "netgear"
        model = None
        firmware = None

        m = re.search(r"(GS\d+\w*|GSM?\d+\w*|M\d+\w*)", desc, re.IGNORECASE)
        if m:
            model = m.group(1).upper()

        m = re.search(r"(\d+\.\d+\.\d+[\.\d]*)", desc)
        if m:
            firmware = m.group(1)

        return vendor, model, firmware

    async def _collect_lldp(self, result: CollectorResult):
        try:
            await super()._collect_lldp(result)
        except Exception as e:
            logger.info("[%s] LLDP not available (Netgear): %s", self.host, e)
            result.partial = True

    async def _collect_vlans(self, result: CollectorResult):
        try:
            await super()._collect_vlans(result)
            if not result.vlans:
                logger.info("[%s] No VLANs returned via Q-BRIDGE (Netgear)", self.host)
                result.partial = True
        except Exception as e:
            logger.info("[%s] VLAN collection unavailable (Netgear): %s", self.host, e)
            result.partial = True

    async def collect(self) -> CollectorResult:
        result = await super().collect()
        result.vendor = "netgear"
        return result
