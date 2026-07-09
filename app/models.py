"""SQLAlchemy ORM models for all network topology data."""
from datetime import datetime
from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, BigInteger
)
from sqlalchemy.orm import relationship
from app.database import Base
from app.crypto import EncryptedString


class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, index=True)
    hostname = Column(String, nullable=False)
    ip_address = Column(String, nullable=False, unique=True, index=True)
    snmp_community = Column(EncryptedString, default="public")
    snmp_version = Column(String, default="2c")
    ssh_enabled = Column(Boolean, default=False)
    ssh_username = Column(String, nullable=True)
    ssh_password = Column(EncryptedString, nullable=True)
    snmp_name = Column(String, nullable=True)    # sysName from SNMP (used for LLDP matching)
    is_gateway = Column(Boolean, default=False)  # marks this device as the gateway/firewall switch
    site = Column(String, nullable=True)          # logical site/location grouping
    fortigate_api_key = Column(EncryptedString, nullable=True)    # FortiGate REST API token
    fortigate_port = Column(Integer, default=443)
    fortigate_verify_ssl = Column(Boolean, default=False)
    # SNMPv3 credentials (only used when snmp_version == "3")
    snmp_v3_username = Column(String, nullable=True)
    snmp_v3_auth_protocol = Column(String, nullable=True)   # SHA, SHA256, MD5
    snmp_v3_auth_password = Column(EncryptedString, nullable=True)
    snmp_v3_priv_protocol = Column(String, nullable=True)   # AES, AES256, DES
    snmp_v3_priv_password = Column(EncryptedString, nullable=True)
    vendor = Column(String, nullable=True)       # e.g. "extreme", "netgear"
    model = Column(String, nullable=True)
    firmware_version = Column(String, nullable=True)
    sys_description = Column(String, nullable=True)
    last_polled = Column(DateTime, nullable=True)
    poll_status = Column(String, default="unknown")  # ok, degraded, error, unknown
    poll_error = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    # Switch vitals (best-effort, vendor MIBs; None = not reported)
    sys_uptime = Column(BigInteger, nullable=True)     # sysUpTime timeticks (1/100 s)
    cpu_util = Column(Integer, nullable=True)          # percent
    mem_used_pct = Column(Integer, nullable=True)      # percent
    temperature = Column(Integer, nullable=True)       # degrees C
    fans_ok = Column(Boolean, nullable=True)
    psu_ok = Column(Boolean, nullable=True)
    poe_budget_w = Column(Integer, nullable=True)      # total PSE power available (W)
    poe_used_w = Column(Integer, nullable=True)        # PSE power consumed (W)
    stp_top_changes = Column(BigInteger, nullable=True)  # dot1dStpTopChanges counter
    vitals_updated_at = Column(DateTime, nullable=True)
    poll_rtt_ms = Column(Integer, nullable=True)         # SNMP response time, status polls only

    ports = relationship("Port", back_populates="device", cascade="all, delete-orphan")
    vlans = relationship("Vlan", back_populates="device", cascade="all, delete-orphan")
    neighbors_local = relationship(
        "Neighbor", foreign_keys="Neighbor.local_device_id",
        back_populates="local_device", cascade="all, delete-orphan"
    )
    mac_entries = relationship("MacEntry", back_populates="device", cascade="all, delete-orphan")


def is_fortigate(device: "Device") -> bool:
    """FortiGate interface counters report errant values — their ports are
    excluded from port-health diagnostics and error events.

    The FortiGates are the gateway devices, so is_gateway is the primary
    signal; the name/API-key checks cover FortiGates not marked as gateway."""
    if device.is_gateway or device.fortigate_api_key:
        return True
    for s in (device.vendor, device.hostname, device.snmp_name, device.sys_description):
        if s and ("forti" in s.lower() or s.lower().startswith("fgt")):
            return True
    return False


class Port(Base):
    __tablename__ = "ports"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False, index=True)
    port_index = Column(Integer, nullable=False)   # ifIndex
    port_name = Column(String, nullable=True)       # ifName
    port_description = Column(String, nullable=True)  # ifAlias / ifDescr
    oper_status = Column(Integer, nullable=True)    # 1=up, 2=down
    admin_status = Column(Integer, nullable=True)   # 1=up, 2=down
    speed = Column(BigInteger, nullable=True)       # bits/sec
    rx_bytes = Column(BigInteger, nullable=True)
    tx_bytes = Column(BigInteger, nullable=True)
    rx_errors = Column(BigInteger, nullable=True)
    tx_errors = Column(BigInteger, nullable=True)
    rx_discards = Column(BigInteger, nullable=True)
    tx_discards = Column(BigInteger, nullable=True)
    duplex = Column(Integer, nullable=True)            # dot3StatsDuplexStatus: 1=unknown, 2=half, 3=full
    if_last_change = Column(BigInteger, nullable=True) # ifLastChange timeticks
    max_speed_seen = Column(BigInteger, nullable=True) # highest speed ever negotiated (bits/sec)
    last_error_at = Column(DateTime, nullable=True)    # last poll where error counters increased
    last_discard_at = Column(DateTime, nullable=True)  # last poll where discard counters increased
    stp_state = Column(Integer, nullable=True)         # 1=disabled,2=blocking,3=listening,4=learning,5=forwarding,6=broken
    rx_broadcast = Column(BigInteger, nullable=True)   # ifHCInBroadcastPkts counter
    last_seen = Column(DateTime, default=datetime.utcnow)
    last_mac = Column(String, nullable=True)           # last MAC seen on this port
    last_hostname = Column(String, nullable=True)      # hostname of last connected device
    last_ip = Column(String, nullable=True)            # IP of last connected device
    last_connection_at = Column(DateTime, nullable=True)
    flap_count = Column(Integer, default=0)            # link up/down cycles
    last_flap_at = Column(DateTime, nullable=True)
    notes = Column(String, nullable=True)              # admin notes
    poe_draw_mw = Column(Integer, nullable=True)       # PoE power in milliwatts
    port_type = Column(String, nullable=True)  # ap, phone, server, printer, workstation, uplink, trunk, unused

    device = relationship("Device", back_populates="ports")
    port_vlans = relationship("PortVlan", back_populates="port", cascade="all, delete-orphan")
    mac_entries = relationship("MacEntry", back_populates="port", cascade="all, delete-orphan")


class Neighbor(Base):
    __tablename__ = "neighbors"

    id = Column(Integer, primary_key=True, index=True)
    local_device_id = Column(Integer, ForeignKey("devices.id"), nullable=False, index=True)
    local_port_id = Column(Integer, ForeignKey("ports.id"), nullable=True, index=True)
    local_port_index = Column(Integer, nullable=True)  # raw ifIndex, populated before port lookup
    remote_chassis_id = Column(String, nullable=True)
    remote_port_id = Column(String, nullable=True)
    remote_system_name = Column(String, nullable=True)
    remote_system_desc = Column(String, nullable=True)
    last_seen = Column(DateTime, default=datetime.utcnow)

    local_device = relationship("Device", foreign_keys=[local_device_id], back_populates="neighbors_local")
    local_port = relationship("Port")


class Vlan(Base):
    __tablename__ = "vlans"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False, index=True)
    vlan_id = Column(Integer, nullable=False)
    vlan_name = Column(String, nullable=True)
    last_seen = Column(DateTime, default=datetime.utcnow)

    device = relationship("Device", back_populates="vlans")
    port_vlans = relationship("PortVlan", back_populates="vlan", cascade="all, delete-orphan")


class PortVlan(Base):
    __tablename__ = "port_vlans"

    id = Column(Integer, primary_key=True, index=True)
    port_id = Column(Integer, ForeignKey("ports.id"), nullable=False, index=True)
    vlan_id = Column(Integer, ForeignKey("vlans.id"), nullable=False, index=True)
    tagged = Column(Boolean, default=True)
    last_seen = Column(DateTime, default=datetime.utcnow)

    port = relationship("Port", back_populates="port_vlans")
    vlan = relationship("Vlan", back_populates="port_vlans")


class MacEntry(Base):
    __tablename__ = "mac_entries"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False, index=True)
    port_id = Column(Integer, ForeignKey("ports.id"), nullable=True)
    port_index = Column(Integer, nullable=True)
    mac_address = Column(String, nullable=False, index=True)
    vlan_id = Column(Integer, nullable=True)
    last_seen = Column(DateTime, default=datetime.utcnow)
    first_seen = Column(DateTime, default=datetime.utcnow)

    device = relationship("Device", back_populates="mac_entries")
    port = relationship("Port", back_populates="mac_entries")


class ArpEntry(Base):
    __tablename__ = "arp_entries"

    id = Column(Integer, primary_key=True, index=True)
    ip_address = Column(String, nullable=False, index=True)
    mac_address = Column(String, nullable=False, index=True)
    hostname = Column(String, nullable=True)
    last_seen = Column(DateTime, default=datetime.utcnow)


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=True, index=True)
    event_type = Column(String, nullable=False)   # device_up, device_down, device_degraded, port_up, port_down, mac_appeared, mac_disappeared
    detail = Column(String, nullable=True)
    read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    device = relationship("Device")


class DeviceLabel(Base):
    __tablename__ = "device_labels"

    id = Column(Integer, primary_key=True, index=True)
    mac_address = Column(String, nullable=False, unique=True, index=True)
    label = Column(String, nullable=False)
    notes = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class PortStat(Base):
    __tablename__ = "port_stats"
    id = Column(Integer, primary_key=True, index=True)
    port_id = Column(Integer, ForeignKey("ports.id", ondelete="CASCADE"), nullable=False, index=True)
    rx_bytes = Column(BigInteger, nullable=True)
    tx_bytes = Column(BigInteger, nullable=True)
    rx_errors = Column(BigInteger, nullable=True)
    tx_errors = Column(BigInteger, nullable=True)
    rx_discards = Column(BigInteger, nullable=True)
    tx_discards = Column(BigInteger, nullable=True)
    rx_broadcast = Column(BigInteger, nullable=True)
    sampled_at = Column(DateTime, default=datetime.utcnow, index=True)


class DeviceStat(Base):
    """Time-series vitals samples: cpu/mem/temp from full polls, RTT from status polls."""
    __tablename__ = "device_stats"
    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    cpu_util = Column(Integer, nullable=True)
    mem_used_pct = Column(Integer, nullable=True)
    temperature = Column(Integer, nullable=True)
    poll_rtt_ms = Column(Integer, nullable=True)
    sampled_at = Column(DateTime, default=datetime.utcnow, index=True)


class DeviceReboot(Base):
    """Durable reboot log (events get pruned; this doesn't)."""
    __tablename__ = "device_reboots"
    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    uptime_before_s = Column(BigInteger, nullable=True)  # seconds of uptime lost
    detected_at = Column(DateTime, default=datetime.utcnow, index=True)


class Subnet(Base):
    __tablename__ = "subnets"
    id = Column(Integer, primary_key=True, index=True)
    cidr = Column(String, nullable=False, unique=True)
    name = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class PortMacHistory(Base):
    __tablename__ = "port_mac_history"
    id = Column(Integer, primary_key=True, index=True)
    port_id = Column(Integer, ForeignKey("ports.id", ondelete="CASCADE"), nullable=False, index=True)
    mac_address = Column(String, nullable=False)
    ip_address = Column(String, nullable=True)
    hostname = Column(String, nullable=True)
    vendor = Column(String, nullable=True)
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow)
