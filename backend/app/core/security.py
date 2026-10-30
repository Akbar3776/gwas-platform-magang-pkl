"""
core/security.py

Inti sistem autentikasi JWT + dependency FastAPI untuk validasi token dan
isolasi data per-user.

Desain token: access token (umur pendek) + refresh token (umur panjang)
-----------------------------------------------------------------------
- ACCESS TOKEN: umur pendek (180 menit), dikirim di header
  `Authorization: Bearer <token>` pada setiap request API. TIDAK disimpan
  di localStorage/cookie oleh frontend — cukup disimpan di memori (React
  state), sehingga kalau ada XSS yang mengeksekusi JS di halaman, jendela
  serangannya sangat sempit (token expired dalam hitungan menit).
- REFRESH TOKEN: umur panjang (7 hari), dikirim SEBAGAI HttpOnly COOKIE
  (bukan JSON body) — sehingga tidak bisa dibaca oleh JavaScript sama
  sekali, jadi tidak bisa dicuri lewat XSS. Dipakai hanya untuk menukar
  access token baru lewat endpoint `POST /auth/refresh`.

Ini penting khusus untuk aplikasi ini karena data genetik pengguna sangat
sensitif — kebocoran token = kebocoran akses ke data genom orang lain.

`refresh_token_version` di tabel users memungkinkan revoke paksa: kalau
user klik "Logout dari semua perangkat" (atau admin mencurigai kebocoran),
naikkan versinya di DB — semua refresh token lama (walau belum expired)
langsung ditolak karena versinya tidak cocok lagi.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Cookie, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.engine.database import get_db
from app.db.engine.models import User, UserRole

try:
    from jose import JWTError, jwt
except ImportError:  # pragma: no cover - fallback to PyJWT when python-jose is absent
    import jwt  # type: ignore[no-redef]
    from jwt.exceptions import PyJWTError as JWTError  # type: ignore[no-redef]

try:
    from passlib.context import CryptContext
    _pwd_context: CryptContext | None = CryptContext(schemes=["bcrypt"], deprecated="auto")
except ImportError:  # pragma: no cover - fallback when passlib is absent
    _pwd_context = None

# --- Konfigurasi ------------------------------------------------------------
# Ambil dari environment variable atau fallback ke config.settings
SECRET_KEY = (
    os.environ.get("JWT_SECRET_KEY")
    or getattr(settings, "JWT_SECRET_KEY", "")
    or getattr(settings, "SECRET_KEY", "change-me-in-production")
)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(getattr(settings, "ACCESS_TOKEN_EXPIRE_MINUTES", 180))
REFRESH_TOKEN_EXPIRE_DAYS = 7
REFRESH_COOKIE_NAME = "gwas_refresh_token"

# tokenUrl hanya dipakai untuk dokumentasi otomatis (Swagger UI "Authorize" button)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


# --- Password hashing ---------------------------------------------------
def hash_password(plain_password: str) -> str:
    if _pwd_context is not None:
        return _pwd_context.hash(plain_password)
    from werkzeug.security import generate_password_hash
    return generate_password_hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    if _pwd_context is not None:
        return _pwd_context.verify(plain_password, hashed_password)
    from werkzeug.security import check_password_hash
    return check_password_hash(hashed_password, plain_password)


def _role_str(role) -> str:
    return role.value if hasattr(role, "value") else str(role)


# --- Pembuatan token ------------------------------------------------------
def create_access_token(user: User) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user.id),
        "role": _role_str(user.role),
        "type": "access",
        "exp": expire,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(user: User) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": str(user.id),
        "type": "refresh",
        "token_version": user.refresh_token_version,
        "exp": expire,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def set_refresh_token_cookie(response, refresh_token: str) -> None:
    """
    Pasang refresh token sebagai HttpOnly cookie di response login/refresh.

    - httponly=True   -> tidak bisa dibaca `document.cookie` di JS (proteksi XSS)
    - secure=True      -> hanya dikirim lewat HTTPS (WAJIB True di production)
    - samesite="lax"  -> aman untuk navigasi same-site dan proxy dev
    - path="/"        -> agar cookie terkirim ke /api/v1/auth/refresh & /api/v1/auth/logout
    """
    env_mode = os.environ.get("ENV", getattr(settings, "ENV", "development"))
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        secure=env_mode != "development",
        samesite="lax",
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600,
        path="/",
    )


def clear_refresh_token_cookie(response) -> None:
    response.delete_cookie(key=REFRESH_COOKIE_NAME, path="/")


# --- Verifikasi token & dependency FastAPI --------------------------------
def _decode_token(token: str, expected_type: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesi tidak valid atau sudah kedaluwarsa, silakan login kembali",
        )

    if payload.get("type") != expected_type:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token tidak valid untuk operasi ini",
        )
    return payload


def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    Dependency utama untuk PROTEKSI ENDPOINT & ISOLASI DATA.

    Setiap endpoint yang mengakses data milik user (upload dataset, start job,
    lihat hasil, download export/report) WAJIB memakai dependency ini dan
    memakai `user.id` yang dikembalikan untuk memfilter query — lihat
    contoh di api/routers/datasets.py dan api/routers/jobs.py.
    """
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Anda perlu login untuk mengakses resource ini",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = _decode_token(token, expected_type="access")
    user_id = payload.get("sub")

    user = db.query(User).filter(User.id == user_id).first()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Akun tidak ditemukan atau sudah dinonaktifkan",
        )
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Dependency tambahan untuk endpoint khusus admin (mis. kelola reference panel)."""
    if user.role != UserRole.ADMIN and _role_str(user.role) != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Khusus admin")
    return user


def verify_refresh_token_and_get_user(
    db: Session,
    refresh_token: Optional[str],
) -> User:
    """Dipakai oleh endpoint POST /auth/refresh — lihat api/routers/auth.py."""
    if refresh_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesi login tidak ditemukan, silakan login kembali",
        )

    payload = _decode_token(refresh_token, expected_type="refresh")
    user_id = payload.get("sub")

    user = db.query(User).filter(User.id == user_id).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Akun tidak valid")

    # Cek revoke: kalau versi di token beda dari versi terbaru di DB, berarti
    # user sudah logout-dari-semua-device sejak refresh token ini diterbitkan.
    if payload.get("token_version") != user.refresh_token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesi sudah tidak berlaku, silakan login kembali",
        )

    return user
