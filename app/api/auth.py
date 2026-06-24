from __future__ import annotations

import logging
import os
import secrets
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User

logger = logging.getLogger(__name__)

# ── Secret key: env var takes priority; otherwise auto-generate and persist ────
_KEY_FILE = "/app/data/secret_key"


def _load_or_create_secret_key() -> str:
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
    key = secrets.token_hex(32)
    os.makedirs(os.path.dirname(_KEY_FILE), exist_ok=True)
    with open(_KEY_FILE, "w") as f:
        f.write(key)
    os.chmod(_KEY_FILE, 0o600)
    logger.info("Generated new JWT secret key and persisted to %s", _KEY_FILE)
    return key


SECRET_KEY = _load_or_create_secret_key()
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 2  # 2 days (down from 7)

_ph = PasswordHasher()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

router = APIRouter(prefix="/api/auth", tags=["auth"])

# ── Brute-force rate limiting (in-memory, per IP) ─────────────────────────────
_RATE_WINDOW = 300  # seconds (5 minutes)
_RATE_MAX    = 5    # max attempts per window

_failed: dict[str, list[float]] = defaultdict(list)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_rate_limit(ip: str) -> None:
    now = time.monotonic()
    _failed[ip] = [t for t in _failed[ip] if now - t < _RATE_WINDOW]
    if len(_failed[ip]) >= _RATE_MAX:
        raise HTTPException(status_code=429, detail="Too many attempts. Try again later.")


def _record_failure(ip: str) -> None:
    _failed[ip].append(time.monotonic())
    logger.warning("Failed auth attempt from %s (%d in last %ds)", ip, len(_failed[ip]), _RATE_WINDOW)


# ── Helpers ───────────────────────────────────────────────────────────────────

def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, plain)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def get_password_hash(password: str) -> str:
    return _ph.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if not username:
            raise exc
    except jwt.PyJWTError:
        raise exc
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise exc
    return user


# ── Schemas ───────────────────────────────────────────────────────────────────

class Token(BaseModel):
    access_token: str
    token_type: str


class PasswordLogin(BaseModel):
    password: str = Field(..., min_length=12, max_length=1000)


class AuthStatus(BaseModel):
    setup_required: bool


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/status", response_model=AuthStatus)
def get_auth_status(db: Session = Depends(get_db)):
    return {"setup_required": db.query(User).count() == 0}


@router.post("/setup", response_model=Token)
def initial_setup(data: PasswordLogin, request: Request, db: Session = Depends(get_db)):
    ip = _client_ip(request)
    _check_rate_limit(ip)
    if db.query(User).count() > 0:
        raise HTTPException(status_code=400, detail="Access key already set. Please login.")
    db_user = User(username="admin", hashed_password=get_password_hash(data.password))
    db.add(db_user)
    db.commit()
    return {"access_token": create_access_token({"sub": "admin"}), "token_type": "bearer"}


@router.post("/login", response_model=Token)
def login(data: PasswordLogin, request: Request, db: Session = Depends(get_db)):
    ip = _client_ip(request)
    _check_rate_limit(ip)
    user = db.query(User).filter(User.username == "admin").first()
    if not user or not verify_password(data.password, user.hashed_password):
        _record_failure(ip)
        raise HTTPException(status_code=401, detail="Invalid Access Key")
    return {"access_token": create_access_token({"sub": "admin"}), "token_type": "bearer"}
