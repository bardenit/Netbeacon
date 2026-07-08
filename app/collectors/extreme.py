"""Extreme Networks (ExtremeXOS) SNMP collector."""
from __future__ import annotations

import logging
import re

from app.collectors.base import BaseCollector, CollectorResult, snmp_walk

logger = logging.getLogger(__name__)

OID_EXTREME_SW_VERSION = "1.3.6.1.4.1.1916.1.1.1.13.0"

# Vitals (EXTREME-SYSTEM-MIB / EXTREME-SOFTWARE-MONITOR-MIB) — all best-effort
OID_EXTREME_CPU_TOTAL   = "1.3.6.1.4.1.1916.1.32.1.2.0"    # extremeCpuMonitorTotalUtilization (%)
OID_EXTREME_TEMPERATURE = "1.3.6.1.4.1.1916.1.1.1.8.0"     # extremeCurrentTemperature (°C)
OID_EXTREME_FAN_STATUS  = "1.3.6.1.4.1.1916.1.1.1.9.1.2"   # extremeFanOperational (1=ok, 2=failed)
OID_EXTREME_PSU_STATUS  = "1.3.6.1.4.1.1916.1.1.1.27.1.2"  # extremePowerSupplyStatus (1=notPresent, 2=ok, 3=notOK)
OID_EXTREME_MEM_TOTAL   = "1.3.6.1.4.1.1916.1.32.2.2.1.2"  # per-slot total KB
OID_EXTREME_MEM_FREE    = "1.3.6.1.4.1.1916.1.32.2.2.1.3"  # per-slot free KB


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

    async def _collect_vitals(self, result: CollectorResult):
        await super()._collect_vitals(result)  # sysUpTime

        try:
            for _, val in (await self._get([OID_EXTREME_CPU_TOTAL])).items():
                result.cpu_util = int(val)
        except Exception as e:
            logger.debug("Extreme CPU OID failed: %s", e)

        try:
            for _, val in (await self._get([OID_EXTREME_TEMPERATURE])).items():
                result.temperature = int(val)
        except Exception as e:
            logger.debug("Extreme temperature OID failed: %s", e)

        try:
            fans = [int(v) for v in (await self._walk(OID_EXTREME_FAN_STATUS)).values()]
            if fans:
                result.fans_ok = all(f == 1 for f in fans)
        except Exception as e:
            logger.debug("Extreme fan walk failed: %s", e)

        try:
            # 1=notPresent (ignore), 2=presentOK, anything else = trouble
            psus = [int(v) for v in (await self._walk(OID_EXTREME_PSU_STATUS)).values()]
            present = [p for p in psus if p != 1]
            if present:
                result.psu_ok = all(p == 2 for p in present)
        except Exception as e:
            logger.debug("Extreme PSU walk failed: %s", e)

        try:
            total = sum(int(v) for v in (await self._walk(OID_EXTREME_MEM_TOTAL)).values())
            free = sum(int(v) for v in (await self._walk(OID_EXTREME_MEM_FREE)).values())
            if total > 0 and 0 <= free <= total:
                result.mem_used_pct = round((total - free) * 100 / total)
        except Exception as e:
            logger.debug("Extreme memory walk failed: %s", e)

    async def collect(self) -> CollectorResult:
        return await super().collect()
