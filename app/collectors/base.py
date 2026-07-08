"""
Base SNMP collector class — async API using pysnmp.hlapi.asyncio.

All vendor-specific collectors inherit from this.

OIDs used:
  IF-MIB      : 1.3.6.1.2.1.2  / 1.3.6.1.2.1.31
  LLDP-MIB    : 1.0.8802.1.1.2
  BRIDGE-MIB  : 1.3.6.1.2.1.17
  Q-BRIDGE    : 1.3.6.1.2.1.17.7
  IP-MIB/ARP  : 1.3.6.1.2.1.4.22
"""
from __future__ import annotations

import asyncio
import logging
import socket
from dataclasses import dataclass, field
from typing import Any

from pysnmp.hlapi.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    UsmUserData,
    getCmd,
    usmAesCfb128Protocol,    # AES-128
    usmAesCfb256Protocol,    # AES-256
    usmDESPrivProtocol,
    usmHMACMD5AuthProtocol,
    usmHMACSHAAuthProtocol,
    usmHMAC192SHA256AuthProtocol,
    usmNoAuthProtocol,
    usmNoPrivProtocol,
    walkCmd,
)

logger = logging.getLogger(__name__)

# ── OID constants ─────────────────────────────────────────────────────────────

OID_SYS_DESCR      = "1.3.6.1.2.1.1.1.0"
OID_SYS_UPTIME     = "1.3.6.1.2.1.1.3.0"
OID_SYS_NAME       = "1.3.6.1.2.1.1.5.0"

# IF-MIB
OID_IF_DESCR        = "1.3.6.1.2.1.2.2.1.2"
OID_IF_ADMIN_STATUS = "1.3.6.1.2.1.2.2.1.7"
OID_IF_OPER_STATUS  = "1.3.6.1.2.1.2.2.1.8"
OID_IF_SPEED        = "1.3.6.1.2.1.2.2.1.5"
OID_IF_IN_OCTETS    = "1.3.6.1.2.1.2.2.1.10"
OID_IF_OUT_OCTETS   = "1.3.6.1.2.1.2.2.1.16"
OID_IF_IN_ERRORS    = "1.3.6.1.2.1.2.2.1.14"
OID_IF_OUT_ERRORS   = "1.3.6.1.2.1.2.2.1.20"
OID_IF_IN_DISCARDS  = "1.3.6.1.2.1.2.2.1.13"
OID_IF_OUT_DISCARDS = "1.3.6.1.2.1.2.2.1.19"
OID_IF_LAST_CHANGE  = "1.3.6.1.2.1.2.2.1.9"
OID_IF_NAME         = "1.3.6.1.2.1.31.1.1.1.1"
OID_IF_ALIAS        = "1.3.6.1.2.1.31.1.1.1.18"
OID_IF_HC_IN        = "1.3.6.1.2.1.31.1.1.1.6"
OID_IF_HC_OUT       = "1.3.6.1.2.1.31.1.1.1.10"

# LLDP-MIB
OID_LLDP_REM_CHASSIS_ID = "1.0.8802.1.1.2.1.4.1.1.4"
OID_LLDP_REM_PORT_ID    = "1.0.8802.1.1.2.1.4.1.1.7"
OID_LLDP_REM_SYS_NAME   = "1.0.8802.1.1.2.1.4.1.1.9"
OID_LLDP_REM_SYS_DESC   = "1.0.8802.1.1.2.1.4.1.1.10"

# BRIDGE-MIB FDB
OID_FDB_ADDRESS       = "1.3.6.1.2.1.17.4.3.1.1"
OID_FDB_PORT          = "1.3.6.1.2.1.17.4.3.1.2"
OID_FDB_STATUS        = "1.3.6.1.2.1.17.4.3.1.3"
OID_DOT1D_BASE_IFINDEX = "1.3.6.1.2.1.17.1.4.1.2"  # bridge port num -> ifIndex

# Q-BRIDGE-MIB
OID_DOT1Q_FDB_PORT            = "1.3.6.1.2.1.17.7.1.2.2.1.2"  # dot1qTpFdbPort (indexed by vlan.mac)
OID_DOT1Q_VLAN_STATIC_NAME    = "1.3.6.1.2.1.17.7.1.4.3.1.1"
# Use static (not current) egress/untagged tables — EXOS doesn't populate current tables
OID_DOT1Q_VLAN_EGRESS_PORTS   = "1.3.6.1.2.1.17.7.1.4.3.1.2"
OID_DOT1Q_VLAN_UNTAGGED_PORTS = "1.3.6.1.2.1.17.7.1.4.3.1.4"
OID_DOT1Q_PVID                = "1.3.6.1.2.1.17.7.1.4.5.1.1"

# IP-MIB ARP
OID_ARP_PHYS_ADDRESS = "1.3.6.1.2.1.4.22.1.2"

# EtherLike-MIB — duplex status per port (1=unknown, 2=half, 3=full)
OID_DOT3_DUPLEX = "1.3.6.1.2.1.10.7.2.1.19"

# BRIDGE-MIB STP
OID_STP_TOP_CHANGES = "1.3.6.1.2.1.17.2.4.0"       # topology change counter
OID_STP_PORT_STATE  = "1.3.6.1.2.1.17.2.15.1.3"    # per bridge port: 2=blocking, 5=forwarding

# POWER-ETHERNET-MIB (RFC 3621) — PoE actual power per port (milliwatts)
OID_POE_ACTUAL_POWER = "1.3.6.1.2.1.105.1.1.1.6"
OID_POE_MAIN_POWER   = "1.3.6.1.2.1.105.1.3.1.1.2"  # PSE budget per group (W)
OID_POE_MAIN_CONSUMP = "1.3.6.1.2.1.105.1.3.1.1.4"  # PSE consumption per group (W)


# ── Data containers ───────────────────────────────────────────────────────────

@dataclass
class PortData:
    port_index: int
    port_name: str | None = None
    port_description: str | None = None
    oper_status: int | None = None
    admin_status: int | None = None
    speed: int | None = None
    rx_bytes: int | None = None
    tx_bytes: int | None = None
    rx_errors: int | None = None
    tx_errors: int | None = None
    rx_discards: int | None = None
    tx_discards: int | None = None
    duplex: int | None = None
    if_last_change: int | None = None
    stp_state: int | None = None
    poe_draw_mw: int | None = None


@dataclass
class NeighborData:
    local_port_index: int
    remote_chassis_id: str | None = None
    remote_port_id: str | None = None
    remote_system_name: str | None = None
    remote_system_desc: str | None = None


@dataclass
class VlanData:
    vlan_id: int
    vlan_name: str | None = None
    egress_ports: bytes = b""
    untagged_ports: bytes = b""


@dataclass
class MacData:
    mac_address: str
    port_index: int
    vlan_id: int | None = None


@dataclass
class ArpData:
    ip_address: str
    mac_address: str
    hostname: str | None = None


@dataclass
class CollectorResult:
    sys_description: str | None = None
    sys_name: str | None = None
    vendor: str | None = None
    model: str | None = None
    firmware_version: str | None = None
    ports: list[PortData] = field(default_factory=list)
    neighbors: list[NeighborData] = field(default_factory=list)
    vlans: list[VlanData] = field(default_factory=list)
    port_pvids: dict[int, int] = field(default_factory=dict)        # bridge_port -> vlan_id
    bridge_to_ifindex: dict[int, int] = field(default_factory=dict) # bridge_port -> ifIndex
    mac_entries: list[MacData] = field(default_factory=list)
    arp_entries: list[ArpData] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    partial: bool = False
    # Switch vitals (best-effort; None = not reported by this device)
    sys_uptime: int | None = None       # sysUpTime timeticks
    cpu_util: int | None = None         # percent
    mem_used_pct: int | None = None     # percent
    temperature: int | None = None      # degrees C
    fans_ok: bool | None = None
    psu_ok: bool | None = None
    poe_budget_w: int | None = None
    poe_used_w: int | None = None
    stp_top_changes: int | None = None


# ── SNMP helpers ──────────────────────────────────────────────────────────────

# Single global engine — reused across all calls so MIBs are loaded once
# and asyncio transport callbacks are never left dangling on a dead engine.
_snmp_engine = SnmpEngine()


class SNMPError(Exception):
    pass


def _make_transport(host: str, timeout: int = 10, retries: int = 2):
    return UdpTransportTarget((host, 161), timeout=timeout, retries=retries)


_AUTH_PROTOCOLS = {
    "MD5":    usmHMACMD5AuthProtocol,
    "SHA":    usmHMACSHAAuthProtocol,
    "SHA256": usmHMAC192SHA256AuthProtocol,  # closest widely-supported SHA-2 variant
}
_PRIV_PROTOCOLS = {
    "DES":    usmDESPrivProtocol,
    "AES":    usmAesCfb128Protocol,
    "AES256": usmAesCfb256Protocol,
}


def _make_community(community: str, version: str = "2c", v3_params: dict | None = None):
    """Return the appropriate auth object for CommunityData (v1/v2c) or UsmUserData (v3)."""
    if version == "3" and v3_params:
        username  = v3_params.get("username") or ""
        auth_pass = v3_params.get("auth_password") or ""
        priv_pass = v3_params.get("priv_password") or ""
        auth_proto = _AUTH_PROTOCOLS.get((v3_params.get("auth_protocol") or "SHA").upper(),
                                         usmHMACSHAAuthProtocol)
        priv_proto = _PRIV_PROTOCOLS.get((v3_params.get("priv_protocol") or "AES").upper(),
                                          usmAesCfb128Protocol)

        if auth_pass and priv_pass:
            # authPriv — full security
            return UsmUserData(username, authKey=auth_pass, privKey=priv_pass,
                               authProtocol=auth_proto, privProtocol=priv_proto)
        elif auth_pass:
            # authNoPriv
            return UsmUserData(username, authKey=auth_pass, authProtocol=auth_proto,
                               privProtocol=usmNoPrivProtocol)
        else:
            # noAuthNoPriv — username only
            return UsmUserData(username, authProtocol=usmNoAuthProtocol,
                               privProtocol=usmNoPrivProtocol)

    mp_model = 1 if version == "2c" else 0
    return CommunityData(community, mpModel=mp_model)


async def snmp_get(host: str, community: str, oids: list[str], version: str = "2c",
                   timeout: int = 10, v3_params: dict | None = None) -> dict[str, Any]:
    """SNMP GET for scalar OIDs. Returns {oid_str: value}."""
    transport = _make_transport(host, timeout=timeout)
    comm = _make_community(community, version, v3_params)
    obj_types = [ObjectType(ObjectIdentity(oid)) for oid in oids]

    try:
        error_indication, error_status, error_index, var_binds = await getCmd(
            _snmp_engine, comm, transport, ContextData(), *obj_types
        )

        if error_indication:
            raise SNMPError(f"SNMP GET error on {host}: {error_indication}")
        if error_status:
            raise SNMPError(f"SNMP GET status error on {host}: {error_status.prettyPrint()}")

        return {str(vb[0]): vb[1] for vb in var_binds}
    except Exception as e:
        if isinstance(e, SNMPError):
            raise
        raise SNMPError(f"SNMP GET exception on {host}: {str(e)}")


async def snmp_walk(host: str, community: str, base_oid: str, version: str = "2c",
                    timeout: int = 15, max_rows: int = 10000,
                    v3_params: dict | None = None) -> dict[str, Any]:
    """
    SNMP WALK of a subtree using walkCmd (async generator).
    Returns {full_oid_str: value}.
    """
    transport = _make_transport(host, timeout=timeout)
    comm = _make_community(community, version, v3_params)

    results: dict[str, Any] = {}
    row_count = 0

    try:
        # walkCmd internally handles retries if retries > 0 in transport
        async for (error_indication, error_status, error_index, var_bind_table) in walkCmd(
            _snmp_engine, comm, transport, ContextData(),
            ObjectType(ObjectIdentity(base_oid)),
            lexicographicMode=False,
        ):
            if error_indication:
                logger.debug("SNMP walk %s on %s error: %s", base_oid, host, error_indication)
                # For walks, we often want to return what we have so far instead of failing entirely
                break
            if error_status:
                logger.debug("SNMP walk %s on %s status error: %s", base_oid, host,
                             error_status.prettyPrint())
                break

            for var_bind in var_bind_table:
                results[str(var_bind[0])] = var_bind[1]
                row_count += 1
                if row_count >= max_rows:
                    logger.warning("SNMP walk %s on %s hit max_rows=%d",
                                   base_oid, host, max_rows)
                    return results
    except asyncio.TimeoutError:
        logger.warning("SNMP walk %s on %s timed out", base_oid, host)
    except Exception as e:
        logger.warning("SNMP walk %s on %s exception: %s", base_oid, host, str(e))

    return results


async def snmp_probe(host: str, community: str, version: str = "2c",
                     v3_params: dict | None = None, timeout: int = 1,
                     retries: int | None = None) -> dict | None:
    """Quick SNMP probe. Returns {sysDescr, sysName} or None on failure.

    SNMPv3 needs at least one retry so the USM time-window resync
    (usmStatsNotInTimeWindows report -> corrected re-send) can complete;
    Cisco agents are strict about this. v2c keeps retries=0 for scan speed.
    """
    if retries is None:
        retries = 1 if version == "3" else 0
    try:
        transport = UdpTransportTarget((host, 161), timeout=timeout, retries=retries)
        comm = _make_community(community, version, v3_params)
        error_indication, error_status, _, var_binds = await getCmd(
            _snmp_engine, comm, transport, ContextData(),
            ObjectType(ObjectIdentity("1.3.6.1.2.1.1.1.0")),
            ObjectType(ObjectIdentity("1.3.6.1.2.1.1.5.0")),
        )
        if error_indication or error_status:
            return None
        return {str(vb[0]): str(vb[1]) for vb in var_binds}
    except Exception:
        return None


def oid_last_int(oid_str: str) -> int:
    return int(oid_str.rstrip(".").rsplit(".", 1)[-1])


def oid_suffix(oid_str: str, prefix: str) -> str:
    prefix = prefix.rstrip(".")
    return oid_str[len(prefix):].lstrip(".")


def mac_bytes_to_str(value: Any) -> str | None:
    try:
        raw = bytes(value)
        if len(raw) == 6:
            return ":".join(f"{b:02x}" for b in raw)
    except Exception:
        pass
    s = str(value)
    if len(s) == 17 and s.count(":") == 5:
        return s.lower()
    return None


def bitmap_to_port_set(bitmap_value: Any) -> set[int]:
    ports = set()
    try:
        raw = bytes(bitmap_value)
    except Exception:
        return ports
    for byte_idx, byte_val in enumerate(raw):
        for bit_idx in range(8):
            if byte_val & (0x80 >> bit_idx):
                ports.add(byte_idx * 8 + bit_idx + 1)
    return ports


# ── Base collector ────────────────────────────────────────────────────────────

class BaseCollector:
    VENDOR_NAME = "generic"

    def __init__(self, host: str, community: str, version: str = "2c", timeout: int = 5,
                 v3_params: dict | None = None):
        self.host = host
        self.community = community
        self.version = version
        self.timeout = timeout
        self.v3_params = v3_params  # dict with username/auth_protocol/auth_password/priv_protocol/priv_password

    async def collect(self) -> CollectorResult:
        result = CollectorResult()
        log = logger.getChild(self.host)

        log.info("Starting SNMP poll [%s]", self.VENDOR_NAME)

        try:
            await self._collect_system_info(result)
            log.info("System: %s / %s", result.sys_name, result.vendor)
        except Exception as e:
            log.error("System info failed: %s", e)
            result.errors.append(f"system_info: {e}")
            result.partial = True

        # Collect bridge port -> ifIndex map early — used by FDB and VLAN
        try:
            await self._collect_bridge_port_map(result)
            log.debug("Bridge port map: %d entries", len(result.bridge_to_ifindex))
        except Exception as e:
            log.debug("Bridge port map failed (non-fatal): %s", e)

        try:
            await self._collect_interfaces(result)
            log.info("Collected %d interfaces", len(result.ports))
        except Exception as e:
            log.error("Interface collection failed: %s", e)
            result.errors.append(f"interfaces: {e}")
            result.partial = True

        try:
            await self._collect_lldp(result)
            log.info("Collected %d LLDP neighbors", len(result.neighbors))
        except Exception as e:
            log.warning("LLDP collection failed (non-fatal): %s", e)
            result.errors.append(f"lldp: {e}")
            result.partial = True

        try:
            await self._collect_vlans(result)
            log.info("Collected %d VLANs", len(result.vlans))
        except Exception as e:
            log.warning("VLAN collection failed (non-fatal): %s", e)
            result.errors.append(f"vlans: {e}")
            result.partial = True

        try:
            await self._collect_fdb(result)
            log.info("Collected %d MAC entries", len(result.mac_entries))
        except Exception as e:
            log.warning("FDB collection failed (non-fatal): %s", e)
            result.errors.append(f"fdb: {e}")
            result.partial = True

        try:
            await self._collect_arp(result)
            log.info("Collected %d ARP entries", len(result.arp_entries))
        except Exception as e:
            log.debug("ARP collection failed (non-fatal): %s", e)

        try:
            await self._collect_poe(result)
        except Exception as e:
            logger.debug("PoE collection failed (non-fatal): %s", e)

        try:
            await self._collect_stp(result)
        except Exception as e:
            logger.debug("STP collection failed (non-fatal): %s", e)

        try:
            await self._collect_vitals(result)
        except Exception as e:
            logger.debug("Vitals collection failed (non-fatal): %s", e)

        log.info(
            "Poll complete: %d ports, %d neighbors, %d vlans, %d macs | partial=%s errors=%d",
            len(result.ports), len(result.neighbors), len(result.vlans),
            len(result.mac_entries), result.partial, len(result.errors)
        )
        return result

    async def collect_status(self) -> CollectorResult:
        """Light poll: interface status/speed/counters only.

        Skips the expensive walks (FDB, ARP, VLANs, LLDP, bridge map) so it can
        run frequently without loading switch management CPUs or the network.
        """
        result = CollectorResult()
        try:
            await self._collect_interfaces(result)
        except Exception as e:
            logger.getChild(self.host).error("Status poll failed: %s", e)
            result.errors.append(f"interfaces: {e}")
            result.partial = True
        # sysUpTime is one GET — cheap enough for the light poll, and it gives
        # fast reboot detection plus a wrap guard for ifLastChange flap detection.
        try:
            data = await self._get([OID_SYS_UPTIME])
            for _, val in data.items():
                result.sys_uptime = int(val)
        except Exception:
            pass
        return result

    # ── Convenience wrappers that auto-inject host/community/version/v3_params ──

    async def _get(self, oids: list[str], timeout: int | None = None) -> dict:
        return await snmp_get(self.host, self.community, oids,
                              self.version, timeout or self.timeout, self.v3_params)

    async def _walk(self, base_oid: str, timeout: int | None = None,
                    max_rows: int = 10000) -> dict:
        return await snmp_walk(self.host, self.community, base_oid,
                               self.version, timeout or self.timeout, max_rows, self.v3_params)

    async def _collect_system_info(self, result: CollectorResult):
        data = await self._get([OID_SYS_DESCR, OID_SYS_NAME])
        for oid, val in data.items():
            s = str(val)
            if "1.1.1.0" in oid:
                result.sys_description = s
            elif "1.5.0" in oid:
                result.sys_name = s

        if result.sys_description:
            result.vendor, result.model, result.firmware_version = \
                self._parse_sys_description(result.sys_description)

    def _parse_sys_description(self, desc: str) -> tuple[str | None, str | None, str | None]:
        import re
        desc_lower = desc.lower()
        if "extreme" in desc_lower or "exos" in desc_lower:
            return "extreme", None, None
        # Netgear: sysDescr starts with model number (GS, FS, M4, XS, MS, JGS…)
        # and never includes the word "netgear" — match by model prefix or keywords
        if "netgear" in desc_lower or "smart managed" in desc_lower or \
                re.match(r'^(GS|FS|M4|XS|MS|JGS|GSM|FSM|GSS|SFP)\d', desc):
            return "netgear", None, None
        return None, None, None

    async def _collect_interfaces(self, result: CollectorResult):
        ports_by_idx: dict[int, PortData] = {}

        for oid, val in (await self._walk(OID_IF_DESCR)).items():
            idx = oid_last_int(oid)
            ports_by_idx[idx] = PortData(port_index=idx, port_description=str(val))

        if not ports_by_idx:
            logger.warning("No interfaces found on %s", self.host)
            return

        for oid, val in (await self._walk(OID_IF_NAME)).items():
            idx = oid_last_int(oid)
            if idx in ports_by_idx:
                ports_by_idx[idx].port_name = str(val)

        for oid, val in (await self._walk(OID_IF_ALIAS)).items():
            idx = oid_last_int(oid)
            if idx in ports_by_idx and str(val):
                ports_by_idx[idx].port_description = str(val)

        for oid, val in (await self._walk(OID_IF_ADMIN_STATUS)).items():
            idx = oid_last_int(oid)
            if idx in ports_by_idx:
                ports_by_idx[idx].admin_status = int(val)

        for oid, val in (await self._walk(OID_IF_OPER_STATUS)).items():
            idx = oid_last_int(oid)
            if idx in ports_by_idx:
                ports_by_idx[idx].oper_status = int(val)

        for oid, val in (await self._walk(OID_IF_SPEED)).items():
            idx = oid_last_int(oid)
            if idx in ports_by_idx:
                ports_by_idx[idx].speed = int(val)

        # Prefer 64-bit HC counters
        hc_in  = await self._walk(OID_IF_HC_IN)
        hc_out = await self._walk(OID_IF_HC_OUT)

        if hc_in:
            for oid, val in hc_in.items():
                idx = oid_last_int(oid)
                if idx in ports_by_idx:
                    ports_by_idx[idx].rx_bytes = int(val)
            for oid, val in hc_out.items():
                idx = oid_last_int(oid)
                if idx in ports_by_idx:
                    ports_by_idx[idx].tx_bytes = int(val)
        else:
            for oid, val in (await self._walk(OID_IF_IN_OCTETS)).items():
                idx = oid_last_int(oid)
                if idx in ports_by_idx:
                    ports_by_idx[idx].rx_bytes = int(val)
            for oid, val in (await self._walk(OID_IF_OUT_OCTETS)).items():
                idx = oid_last_int(oid)
                if idx in ports_by_idx:
                    ports_by_idx[idx].tx_bytes = int(val)

        # Error/discard counters + last link change (all non-fatal)
        for base_oid, attr in (
            (OID_IF_IN_ERRORS, "rx_errors"),
            (OID_IF_OUT_ERRORS, "tx_errors"),
            (OID_IF_IN_DISCARDS, "rx_discards"),
            (OID_IF_OUT_DISCARDS, "tx_discards"),
            (OID_IF_LAST_CHANGE, "if_last_change"),
        ):
            try:
                for oid, val in (await self._walk(base_oid)).items():
                    idx = oid_last_int(oid)
                    if idx in ports_by_idx:
                        setattr(ports_by_idx[idx], attr, int(val))
            except Exception as e:
                logger.getChild(self.host).debug("%s walk failed (non-fatal): %s", attr, e)

        # Duplex (EtherLike-MIB, indexed by ifIndex)
        try:
            for oid, val in (await self._walk(OID_DOT3_DUPLEX)).items():
                idx = oid_last_int(oid)
                if idx in ports_by_idx:
                    ports_by_idx[idx].duplex = int(val)
        except Exception as e:
            logger.getChild(self.host).debug("duplex walk failed (non-fatal): %s", e)

        result.ports = list(ports_by_idx.values())

    async def _collect_lldp(self, result: CollectorResult):
        neighbors: dict[tuple, NeighborData] = {}

        def _key_from_oid(oid: str, base: str) -> tuple[int, int] | None:
            suffix = oid_suffix(oid, base)
            parts = suffix.split(".")
            if len(parts) >= 3:
                try:
                    return int(parts[1]), int(parts[2])
                except (ValueError, IndexError):
                    pass
            return None

        for oid, val in (await self._walk(OID_LLDP_REM_SYS_NAME)).items():
            key = _key_from_oid(oid, OID_LLDP_REM_SYS_NAME)
            if key:
                nd = neighbors.setdefault(key, NeighborData(local_port_index=key[0]))
                nd.remote_system_name = str(val)

        for oid, val in (await self._walk(OID_LLDP_REM_CHASSIS_ID)).items():
            key = _key_from_oid(oid, OID_LLDP_REM_CHASSIS_ID)
            if key and key in neighbors:
                raw = mac_bytes_to_str(val)
                neighbors[key].remote_chassis_id = raw or str(val)

        for oid, val in (await self._walk(OID_LLDP_REM_PORT_ID)).items():
            key = _key_from_oid(oid, OID_LLDP_REM_PORT_ID)
            if key and key in neighbors:
                neighbors[key].remote_port_id = str(val)

        for oid, val in (await self._walk(OID_LLDP_REM_SYS_DESC)).items():
            key = _key_from_oid(oid, OID_LLDP_REM_SYS_DESC)
            if key and key in neighbors:
                neighbors[key].remote_system_desc = str(val)

        result.neighbors = list(neighbors.values())

    async def _collect_vlans(self, result: CollectorResult):
        vlans: dict[int, VlanData] = {}

        for oid, val in (await self._walk(OID_DOT1Q_VLAN_STATIC_NAME)).items():
            vlan_id = oid_last_int(oid)
            vlans[vlan_id] = VlanData(vlan_id=vlan_id, vlan_name=str(val))

        for oid, val in (await self._walk(OID_DOT1Q_VLAN_EGRESS_PORTS)).items():
            parts = oid.rsplit(".", 2)
            if len(parts) >= 2:
                try:
                    vlan_id = int(parts[-1])
                    if vlan_id in vlans:
                        vlans[vlan_id].egress_ports = bytes(val)
                except (ValueError, TypeError):
                    pass

        for oid, val in (await self._walk(OID_DOT1Q_VLAN_UNTAGGED_PORTS)).items():
            parts = oid.rsplit(".", 2)
            if len(parts) >= 2:
                try:
                    vlan_id = int(parts[-1])
                    if vlan_id in vlans:
                        vlans[vlan_id].untagged_ports = bytes(val)
                except (ValueError, TypeError):
                    pass

        for oid, val in (await self._walk(OID_DOT1Q_PVID)).items():
            bridge_port = oid_last_int(oid)
            # Translate bridge port number to ifIndex
            ifindex = result.bridge_to_ifindex.get(bridge_port, bridge_port)
            try:
                result.port_pvids[ifindex] = int(val)
            except (ValueError, TypeError):
                pass

        result.vlans = list(vlans.values())

    async def _collect_bridge_port_map(self, result: CollectorResult):
        """Walk dot1dBasePortIfIndex to build bridge_port -> ifIndex map."""
        for oid, val in (await self._walk(OID_DOT1D_BASE_IFINDEX)).items():
            bridge_port = oid_last_int(oid)
            try:
                result.bridge_to_ifindex[bridge_port] = int(val)
            except (ValueError, TypeError):
                pass

    async def _collect_fdb(self, result: CollectorResult):

        port_by_suffix: dict[str, int] = {}
        mac_by_suffix: dict[str, str] = {}

        for oid, val in (await self._walk(OID_FDB_ADDRESS)).items():
            mac = mac_bytes_to_str(val)
            if mac:
                suffix = oid_suffix(oid, OID_FDB_ADDRESS)
                mac_by_suffix[suffix] = mac

        for oid, val in (await self._walk(OID_FDB_PORT)).items():
            suffix = oid_suffix(oid, OID_FDB_PORT)
            if suffix in mac_by_suffix:
                try:
                    bridge_port = int(val)
                    ifindex = result.bridge_to_ifindex.get(bridge_port, bridge_port)
                    port_by_suffix[suffix] = ifindex
                except (ValueError, TypeError):
                    pass

        # Accept all entries with a valid MAC and port — the FDB ages itself out on the switch.
        # Status filtering was dropped because some vendors (e.g. Netgear) return non-standard
        # status codes that caused learned entries to be silently discarded.
        seen: set[str] = set()
        for suffix, mac in mac_by_suffix.items():
            if suffix in port_by_suffix:
                result.mac_entries.append(
                    MacData(mac_address=mac, port_index=port_by_suffix[suffix])
                )
                seen.add(mac)

        # Also walk Q-BRIDGE dot1qTpFdbTable — required for 802.1Q switches where MACs
        # on non-default VLANs are not present in the classic dot1dTpFdbTable.
        # OID index format: {vlan_id}.{6 MAC octets}
        for oid, val in (await self._walk(OID_DOT1Q_FDB_PORT)).items():
            suffix = oid_suffix(oid, OID_DOT1Q_FDB_PORT)
            parts = suffix.split(".")
            if len(parts) == 7:
                try:
                    vlan_id = int(parts[0])
                    mac = ":".join(f"{int(p):02x}" for p in parts[1:])
                    bridge_port = int(val)
                    if bridge_port == 0:
                        continue
                    ifindex = result.bridge_to_ifindex.get(bridge_port, bridge_port)
                    if mac not in seen:
                        result.mac_entries.append(
                            MacData(mac_address=mac, port_index=ifindex, vlan_id=vlan_id)
                        )
                        seen.add(mac)
                except (ValueError, TypeError):
                    pass

    async def _collect_arp(self, result: CollectorResult):
        raw: list[ArpData] = []
        for oid, val in (await self._walk(OID_ARP_PHYS_ADDRESS)).items():
            mac = mac_bytes_to_str(val)
            if not mac or mac == "00:00:00:00:00:00":
                continue
            suffix = oid_suffix(oid, OID_ARP_PHYS_ADDRESS)
            parts = suffix.split(".")
            if len(parts) >= 5:
                ip = ".".join(parts[1:5])
                try:
                    socket.inet_aton(ip)
                    raw.append(ArpData(ip_address=ip, mac_address=mac))
                except OSError:
                    pass

        # Reverse DNS lookups — limited concurrency, 2s timeout per lookup
        sem = asyncio.Semaphore(10)

        async def _rdns(entry: ArpData) -> ArpData:
            async with sem:
                try:
                    loop = asyncio.get_running_loop()
                    host, *_ = await asyncio.wait_for(
                        loop.run_in_executor(None, socket.gethostbyaddr, entry.ip_address),
                        timeout=2.0
                    )
                    entry.hostname = host.lower()
                except Exception:
                    pass
            return entry

        raw = list(await asyncio.gather(*[_rdns(e) for e in raw]))
        result.arp_entries.extend(raw)

    async def _collect_poe(self, result: CollectorResult):
        """Walk POWER-ETHERNET-MIB to get PoE draw per port (best-effort)."""
        raw = await self._walk(OID_POE_ACTUAL_POWER, timeout=5)
        if not raw:
            return

        # Build a name→port map for matching by physical port number
        name_to_port = {p.port_name: p for p in result.ports if p.port_name}

        for oid, val in raw.items():
            try:
                mw = int(val)
            except (ValueError, TypeError):
                continue
            if mw <= 0:
                continue

            # OID suffix is {module_idx}.{port_idx}
            suffix = oid_suffix(oid, OID_POE_ACTUAL_POWER)
            parts = suffix.strip(".").split(".")
            if len(parts) < 2:
                continue
            try:
                module_idx = int(parts[0])
                port_idx   = int(parts[1])
            except ValueError:
                continue

            # Try to find the port by name — EXOS uses "module:port" notation
            port = name_to_port.get(f"{module_idx}:{port_idx}")
            if not port:
                # Fallback: match by raw port number across all slots
                for p in result.ports:
                    nm = p.port_name or ""
                    if nm.endswith(f":{port_idx}") or nm == str(port_idx):
                        port = p
                        break
            if port:
                port.poe_draw_mw = mw

        # PSE budget/consumption (Watts, summed across power groups)
        try:
            budget = sum(int(v) for v in (await self._walk(OID_POE_MAIN_POWER, timeout=5)).values())
            used = sum(int(v) for v in (await self._walk(OID_POE_MAIN_CONSUMP, timeout=5)).values())
            if budget > 0:
                result.poe_budget_w = budget
                result.poe_used_w = used
        except Exception as e:
            logger.getChild(self.host).debug("PoE main PSE walk failed (non-fatal): %s", e)

    async def _collect_stp(self, result: CollectorResult):
        """STP topology-change counter + per-port STP state (best-effort)."""
        try:
            data = await self._get([OID_STP_TOP_CHANGES])
            for _, val in data.items():
                result.stp_top_changes = int(val)
        except Exception as e:
            logger.getChild(self.host).debug("STP top-changes failed (non-fatal): %s", e)

        # Per-port state is indexed by bridge port number — translate to ifIndex
        if not result.bridge_to_ifindex or not result.ports:
            return
        ports_by_idx = {p.port_index: p for p in result.ports}
        try:
            for oid, val in (await self._walk(OID_STP_PORT_STATE)).items():
                bridge_port = oid_last_int(oid)
                ifidx = result.bridge_to_ifindex.get(bridge_port)
                if ifidx in ports_by_idx:
                    ports_by_idx[ifidx].stp_state = int(val)
        except Exception as e:
            logger.getChild(self.host).debug("STP port-state walk failed (non-fatal): %s", e)

    async def _collect_vitals(self, result: CollectorResult):
        """Device health metrics. Base class collects sysUpTime only;
        vendor subclasses add CPU/memory/temperature/fan/PSU."""
        try:
            data = await self._get([OID_SYS_UPTIME])
            for _, val in data.items():
                result.sys_uptime = int(val)
        except Exception as e:
            logger.getChild(self.host).debug("sysUpTime failed (non-fatal): %s", e)
