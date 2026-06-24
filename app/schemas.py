"""Pydantic schemas for API request/response serialization."""
from __future__ import annotations

import ipaddress
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


# ── Device ────────────────────────────────────────────────────────────────────

class DeviceBase(BaseModel):
    hostname: str = Field(..., max_length=255)
    ip_address: str
    snmp_community: str = Field(default="public", max_length=200)
    snmp_version: Literal["1", "2c", "3"] = "2c"
    ssh_enabled: bool = False
    ssh_username: Optional[str] = Field(default=None, max_length=200)
    ssh_password: Optional[str] = Field(default=None, max_length=500)
    site: Optional[str] = Field(default=None, max_length=100)
    # SNMPv3 credentials
    snmp_v3_username: Optional[str] = Field(default=None, max_length=200)
    snmp_v3_auth_protocol: Optional[Literal["SHA", "SHA256", "MD5"]] = "SHA"
    snmp_v3_auth_password: Optional[str] = Field(default=None, max_length=500)
    snmp_v3_priv_protocol: Optional[Literal["AES", "AES256", "DES"]] = "AES"
    snmp_v3_priv_password: Optional[str] = Field(default=None, max_length=500)

    @field_validator("ip_address")
    @classmethod
    def validate_ip(cls, v: str) -> str:
        try:
            ipaddress.ip_address(v)
        except ValueError:
            raise ValueError(f"Invalid IP address: {v!r}")
        return v


class DeviceCreate(DeviceBase):
    pass


class DeviceUpdate(BaseModel):
    hostname: Optional[str] = Field(default=None, max_length=255)
    ip_address: Optional[str] = None
    snmp_community: Optional[str] = Field(default=None, max_length=200)
    snmp_version: Optional[Literal["1", "2c", "3"]] = None
    ssh_enabled: Optional[bool] = None
    ssh_username: Optional[str] = Field(default=None, max_length=200)
    ssh_password: Optional[str] = Field(default=None, max_length=500)
    site: Optional[str] = Field(default=None, max_length=100)
    snmp_v3_username: Optional[str] = Field(default=None, max_length=200)
    snmp_v3_auth_protocol: Optional[Literal["SHA", "SHA256", "MD5"]] = None
    snmp_v3_auth_password: Optional[str] = Field(default=None, max_length=500)
    snmp_v3_priv_protocol: Optional[Literal["AES", "AES256", "DES"]] = None
    snmp_v3_priv_password: Optional[str] = Field(default=None, max_length=500)

    @field_validator("ip_address")
    @classmethod
    def validate_ip(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        try:
            ipaddress.ip_address(v)
        except ValueError:
            raise ValueError(f"Invalid IP address: {v!r}")
        return v


class DeviceOut(BaseModel):
    """Safe device representation — credentials are never returned."""
    id: int
    hostname: str
    ip_address: str
    snmp_version: str
    snmp_v3_username: Optional[str] = None
    ssh_enabled: bool = False
    ssh_username: Optional[str] = None
    site: Optional[str] = None
    snmp_name: Optional[str] = None
    vendor: Optional[str] = None
    model: Optional[str] = None
    firmware_version: Optional[str] = None
    sys_description: Optional[str] = None
    last_polled: Optional[datetime] = None
    poll_status: str = "unknown"
    poll_error: Optional[str] = None
    is_gateway: bool = False
    created_at: datetime
    # Presence flags so the UI knows whether credentials are set
    has_snmp_community: bool = False
    has_ssh_password: bool = False
    has_snmp_v3_auth_password: bool = False
    has_snmp_v3_priv_password: bool = False

    model_config = {"from_attributes": True}

    @classmethod
    def from_device(cls, d: object) -> "DeviceOut":
        return cls(
            id=d.id,
            hostname=d.hostname,
            ip_address=d.ip_address,
            snmp_version=d.snmp_version or "2c",
            snmp_v3_username=d.snmp_v3_username,
            ssh_enabled=d.ssh_enabled or False,
            ssh_username=d.ssh_username,
            site=d.site,
            snmp_name=d.snmp_name,
            vendor=d.vendor,
            model=d.model,
            firmware_version=d.firmware_version,
            sys_description=d.sys_description,
            last_polled=d.last_polled,
            poll_status=d.poll_status or "unknown",
            poll_error=d.poll_error,
            is_gateway=d.is_gateway or False,
            created_at=d.created_at,
            has_snmp_community=bool(d.snmp_community),
            has_ssh_password=bool(d.ssh_password),
            has_snmp_v3_auth_password=bool(d.snmp_v3_auth_password),
            has_snmp_v3_priv_password=bool(d.snmp_v3_priv_password),
        )


# ── Port ──────────────────────────────────────────────────────────────────────

class VlanInfo(BaseModel):
    vlan_id: int
    vlan_name: Optional[str] = None
    tagged: bool

    model_config = {"from_attributes": True}


class PortOut(BaseModel):
    id: int
    device_id: int
    port_index: int
    port_name: Optional[str] = None
    port_description: Optional[str] = None
    oper_status: Optional[int] = None
    admin_status: Optional[int] = None
    speed: Optional[int] = None
    rx_bytes: Optional[int] = None
    tx_bytes: Optional[int] = None
    rx_errors: Optional[int] = None
    tx_errors: Optional[int] = None
    last_seen: Optional[datetime] = None
    vlans: list[VlanInfo] = []
    mac_count: int = 0
    lldp_neighbor: Optional[str] = None

    model_config = {"from_attributes": True}


# ── Topology ──────────────────────────────────────────────────────────────────

class TopologyNode(BaseModel):
    id: int
    hostname: str
    snmp_name: Optional[str] = None
    ip_address: str
    vendor: Optional[str] = None
    model: Optional[str] = None
    poll_status: str
    last_polled: Optional[datetime] = None
    is_gateway: bool = False
    unmanaged: bool = False
    site: Optional[str] = None


class TopologyEdge(BaseModel):
    id: int
    source_device_id: int
    target_device_id: int
    source_port: Optional[str] = None
    target_port: Optional[str] = None
    remote_system_name: Optional[str] = None


class TopologyGraph(BaseModel):
    nodes: list[TopologyNode]
    edges: list[TopologyEdge]


# ── MAC / ARP search ──────────────────────────────────────────────────────────

class MacSearchResult(BaseModel):
    mac_address: str
    device_id: int
    device_hostname: str
    device_ip: str
    port_id: Optional[int] = None
    port_name: Optional[str] = None
    port_index: Optional[int] = None
    vlan_id: Optional[int] = None
    ip_address: Optional[str] = None
    end_host_hostname: Optional[str] = None
    port_mac_count: Optional[int] = None
    last_seen: Optional[datetime] = None
    lldp_neighbor: Optional[str] = None


# ── Connection test ───────────────────────────────────────────────────────────

class ConnectionTestRequest(BaseModel):
    ip_address: str
    snmp_community: str = "public"
    snmp_version: Literal["1", "2c", "3"] = "2c"
    snmp_v3_username: Optional[str] = None
    snmp_v3_auth_protocol: Optional[Literal["SHA", "SHA256", "MD5"]] = "SHA"
    snmp_v3_auth_password: Optional[str] = None
    snmp_v3_priv_protocol: Optional[Literal["AES", "AES256", "DES"]] = "AES"
    snmp_v3_priv_password: Optional[str] = None

    @field_validator("ip_address")
    @classmethod
    def validate_ip(cls, v: str) -> str:
        try:
            ipaddress.ip_address(v)
        except ValueError:
            raise ValueError(f"Invalid IP address: {v!r}")
        return v


class ConnectionTestResult(BaseModel):
    reachable: bool
    sys_name: Optional[str] = None
    sys_description: Optional[str] = None
    vendor: Optional[str] = None
    error: Optional[str] = None


# ── Network scan ──────────────────────────────────────────────────────────────

class ScanRequest(BaseModel):
    cidr: str
    community: str = "public"
    version: Literal["1", "2c", "3"] = "2c"


class ScanDiscovery(BaseModel):
    ip_address: str
    sys_name: Optional[str] = None
    sys_description: Optional[str] = None
    vendor: Optional[str] = None
    already_added: bool = False


# ── Status ────────────────────────────────────────────────────────────────────

class PollStatus(BaseModel):
    last_poll_time: Optional[datetime] = None
    next_poll_time: Optional[datetime] = None
    poll_interval_minutes: int
    devices_total: int
    devices_ok: int
    devices_degraded: int
    devices_error: int
    devices_unknown: int
    unread_events: int = 0


# ── Port update payloads ──────────────────────────────────────────────────────

class PortNotesUpdate(BaseModel):
    notes: str = ""

    @field_validator("notes")
    @classmethod
    def limit_length(cls, v: str) -> str:
        if len(v) > 2000:
            raise ValueError("Notes must be 2000 characters or fewer")
        return v


class PortTypeUpdate(BaseModel):
    port_type: Optional[Literal[
        "AP", "Phone", "Server", "Printer",
        "Workstation", "Uplink", "Trunk", "Unused"
    ]] = None
