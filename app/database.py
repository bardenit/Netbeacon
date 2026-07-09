"""SQLAlchemy database setup."""
import logging
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

logger = logging.getLogger(__name__)

_here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_default_db = os.path.join(_here, "data", "network.db")
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{_default_db}")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from app import models  # noqa: F401 - ensures models are registered
    Base.metadata.create_all(bind=engine)
    _migrate()
    _migrate_credentials()
    logger.info("Database initialized at %s", DATABASE_URL)


def _migrate():
    """Apply additive schema migrations for existing databases."""
    migrations = [
        "ALTER TABLE devices ADD COLUMN snmp_name VARCHAR",
        "ALTER TABLE devices ADD COLUMN is_gateway BOOLEAN DEFAULT 0",
        "ALTER TABLE arp_entries ADD COLUMN hostname VARCHAR",
        "ALTER TABLE devices ADD COLUMN snmp_v3_username VARCHAR",
        "ALTER TABLE devices ADD COLUMN snmp_v3_auth_protocol VARCHAR",
        "ALTER TABLE devices ADD COLUMN snmp_v3_auth_password VARCHAR",
        "ALTER TABLE devices ADD COLUMN snmp_v3_priv_protocol VARCHAR",
        "ALTER TABLE devices ADD COLUMN snmp_v3_priv_password VARCHAR",
        "ALTER TABLE devices ADD COLUMN site VARCHAR",
        "ALTER TABLE devices ADD COLUMN fortigate_api_key VARCHAR",
        "ALTER TABLE devices ADD COLUMN fortigate_port INTEGER DEFAULT 443",
        "ALTER TABLE devices ADD COLUMN fortigate_verify_ssl BOOLEAN DEFAULT 0",
        "ALTER TABLE ports ADD COLUMN last_mac VARCHAR",
        "ALTER TABLE ports ADD COLUMN last_hostname VARCHAR",
        "ALTER TABLE ports ADD COLUMN last_ip VARCHAR",
        "ALTER TABLE ports ADD COLUMN last_connection_at DATETIME",
        "ALTER TABLE ports ADD COLUMN flap_count INTEGER DEFAULT 0",
        "ALTER TABLE ports ADD COLUMN last_flap_at DATETIME",
        "ALTER TABLE ports ADD COLUMN notes VARCHAR",
        "ALTER TABLE ports ADD COLUMN poe_draw_mw INTEGER",
        "ALTER TABLE mac_entries ADD COLUMN first_seen DATETIME",
        "ALTER TABLE ports ADD COLUMN port_type VARCHAR",
        # Port health (errors/discards/duplex/flap fidelity)
        "ALTER TABLE ports ADD COLUMN rx_discards BIGINT",
        "ALTER TABLE ports ADD COLUMN tx_discards BIGINT",
        "ALTER TABLE ports ADD COLUMN duplex INTEGER",
        "ALTER TABLE ports ADD COLUMN if_last_change BIGINT",
        "ALTER TABLE ports ADD COLUMN max_speed_seen BIGINT",
        "ALTER TABLE ports ADD COLUMN last_error_at DATETIME",
        "ALTER TABLE ports ADD COLUMN last_discard_at DATETIME",
        "ALTER TABLE ports ADD COLUMN stp_state INTEGER",
        "ALTER TABLE port_stats ADD COLUMN rx_discards BIGINT",
        "ALTER TABLE port_stats ADD COLUMN tx_discards BIGINT",
        # Switch vitals
        "ALTER TABLE devices ADD COLUMN sys_uptime BIGINT",
        "ALTER TABLE devices ADD COLUMN cpu_util INTEGER",
        "ALTER TABLE devices ADD COLUMN mem_used_pct INTEGER",
        "ALTER TABLE devices ADD COLUMN temperature INTEGER",
        "ALTER TABLE devices ADD COLUMN fans_ok BOOLEAN",
        "ALTER TABLE devices ADD COLUMN psu_ok BOOLEAN",
        "ALTER TABLE devices ADD COLUMN poe_budget_w INTEGER",
        "ALTER TABLE devices ADD COLUMN poe_used_w INTEGER",
        "ALTER TABLE devices ADD COLUMN stp_top_changes BIGINT",
        "ALTER TABLE devices ADD COLUMN vitals_updated_at DATETIME",
        "ALTER TABLE devices ADD COLUMN poll_rtt_ms INTEGER",
        "ALTER TABLE ports ADD COLUMN rx_broadcast BIGINT",
        "ALTER TABLE port_stats ADD COLUMN rx_broadcast BIGINT",
        "CREATE INDEX IF NOT EXISTS ix_neighbors_local_port_id ON neighbors (local_port_id)",
        "CREATE INDEX IF NOT EXISTS ix_port_vlans_vlan_id ON port_vlans (vlan_id)",
    ]
    with engine.connect() as conn:
        for sql in migrations:
            try:
                conn.execute(__import__("sqlalchemy").text(sql))
                conn.commit()
                logger.info("Migration applied: %s", sql)
            except Exception:
                pass  # Column already exists


def _migrate_credentials():
    """Encrypt any plaintext credential values stored before encryption was introduced."""
    from app.crypto import encrypt_value, decrypt_value
    _sa = __import__("sqlalchemy")
    _text = _sa.text

    credential_cols = [
        "snmp_community", "ssh_password",
        "snmp_v3_auth_password", "snmp_v3_priv_password", "fortigate_api_key",
    ]

    migrated = 0
    with engine.connect() as conn:
        rows = conn.execute(_text("SELECT id FROM devices")).fetchall()
        for row in rows:
            device_id = row[0]
            updates: dict[str, str] = {}
            for col in credential_cols:
                val = conn.execute(
                    _text(f"SELECT {col} FROM devices WHERE id = :id"),
                    {"id": device_id},
                ).scalar()
                if not val:
                    continue
                # Fernet tokens are always >60 chars and start with 'gAAAA'.
                # Anything shorter or with a different prefix is still plaintext.
                decrypted = decrypt_value(val)
                if decrypted == val:
                    # decrypt_value returned the input unchanged → it's plaintext
                    updates[col] = encrypt_value(val)

            if updates:
                for col, enc_val in updates.items():
                    conn.execute(
                        _text(f"UPDATE devices SET {col} = :val WHERE id = :id"),
                        {"val": enc_val, "id": device_id},
                    )
                conn.commit()
                migrated += 1

    if migrated:
        logger.info("Credential migration: encrypted credentials for %d device(s)", migrated)
