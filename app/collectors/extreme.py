"""Extreme Networks (ExtremeXOS) SNMP collector."""
from __future__ import annotations

import logging
import re

from app.collectors.base import BaseCollector, CollectorResult, snmp_walk

logger = logging.getLogger(__name__)

OID_EXTREME_SW_VERSION = "1.3.6.1.4.1.1916.1.1.1.13.0"


class ExtremeCollector(BaseCollector):
    VENDOR_NAME = "extreme"

    def _parse_sys_description(self, desc: str) -> tuple[str | None, str | None, str | None]:
        desc_lower = desc.lower()
        if "extreme" not in desc_lower and "exos" not in desc_lower:
            # Not actually an Extreme device — use base class detection
            return super()._parse_sys_description(desc)

        model = None
        firmware = None

        m = re.search(r"ExtremeXOS\s+version\s+([\d.]+)", desc, re.IGNORECASE)
        if m:
            firmware = m.group(1)

        m = re.search(r"(X\d+[A-Z0-9-]+\w*)", desc, re.IGNORECASE)
        if m:
            model = m.group(1)

        return "extreme", model, firmware

    async def _collect_system_info(self, result: CollectorResult):
        await super()._collect_system_info(result)

        try:
            fw_data = await snmp_walk(self.host, self.community, OID_EXTREME_SW_VERSION,
                                      self.version, self.timeout)
            for _, val in fw_data.items():
                s = str(val)
                if s:
                    m = re.search(r"version\s+([\d.]+)", s, re.IGNORECASE)
                    if m and not result.firmware_version:
                        result.firmware_version = m.group(1)
                    break
        except Exception as e:
            logger.debug("Extreme enterprise FW OID failed: %s", e)

    async def collect(self) -> CollectorResult:
        return await super().collect()
