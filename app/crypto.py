"""Transparent Fernet encryption for credential columns stored in SQLite."""
from __future__ import annotations

import base64
import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None

_KEY_FILE = "/app/data/secret_key"


def _load_secret_key() -> str:
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
    # Dev fallback — credentials will be unrecoverable if this key is in use when a
    # real SECRET_KEY is later set, but the plaintext fallback in decrypt_value handles that.
    return "netbeacon-dev-fallback-do-not-use-in-production"


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet
    secret = _load_secret_key()
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
