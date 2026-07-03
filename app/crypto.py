"""Transparent Fernet encryption for credential columns stored in SQLite."""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None

_KEY_FILE = "/app/data/secret_key"


def load_or_create_secret_key() -> str:
    """Single source of truth for the app secret (JWT signing + credential encryption).

    Priority: SECRET_KEY env var, then the persisted key file, else generate a
    random key and persist it. Never falls back to a hardcoded value — if the
    key can't be created, startup fails rather than encrypting with a known key.
    """
    env_key = os.environ.get("SECRET_KEY", "").strip()
    if env_key:
        return env_key
    try:
        with open(_KEY_FILE) as f:
            key = f.read().strip()
            if key:
                return key
    except FileNotFoundError:
        pass
    try:
        key = secrets.token_hex(32)
        os.makedirs(os.path.dirname(_KEY_FILE), exist_ok=True)
        with open(_KEY_FILE, "w") as f:
            f.write(key)
        os.chmod(_KEY_FILE, 0o600)
        logger.info("Generated new secret key and persisted to %s", _KEY_FILE)
        return key
    except OSError as e:
        raise RuntimeError(
            f"No SECRET_KEY env var set and cannot create key file {_KEY_FILE}: {e}. "
            "Set SECRET_KEY or make the data directory writable."
        ) from e


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet
    secret = load_or_create_secret_key()
    # Derive a 32-byte key distinct from the JWT secret using a domain prefix.
    key_bytes = hashlib.sha256(f"cred-enc:{secret}".encode()).digest()
    _fernet = Fernet(base64.urlsafe_b64encode(key_bytes))
    return _fernet


def encrypt_value(plaintext: str) -> str:
    if not plaintext:
        return plaintext
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    if not ciphertext:
        return ciphertext
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except (InvalidToken, Exception):
        # Plaintext fallback: value was stored before encryption was introduced,
        # or the key changed. Return as-is so the device remains usable.
        return ciphertext


class EncryptedString(TypeDecorator):
    """SQLAlchemy column type that transparently encrypts on write, decrypts on read."""
    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        return encrypt_value(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        return decrypt_value(value)
