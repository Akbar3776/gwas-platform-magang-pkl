"""
api/routers/auth.py

POST /auth/register  - buat akun baru
POST /auth/login     - login, kembalikan access token (JSON) + refresh token (HttpOnly cookie)
POST /auth/refresh   - tukar refresh token (dari cookie) dengan access token baru
POST /auth/logout    - hapus refresh token cookie + revoke (naikkan token_version)
GET  /auth/me        - info user yang sedang login
"""

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.core.security import (
    REFRESH_COOKIE_NAME,
    clear_refresh_token_cookie,
    create_access_token,
    create_refresh_token,
    get_current_user,
    hash_password,
    set_refresh_token_cookie,
    verify_password,
    verify_refresh_token_and_get_user,
)
from app.db.engine.database import get_db
from app.db.engine.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, description="Minimal 8 karakter")
    full_name: str | None = None


class UserOut(BaseModel):
    id: str
    email: str
    full_name: str | None
    role: str

    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email sudah terdaftar")

    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        role="researcher",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return UserOut(id=str(user.id), email=user.email, full_name=user.full_name, role=user.role)


@router.post("/login", response_model=TokenResponse)
def login(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    # OAuth2PasswordRequestForm memakai field "username" secara default —
    # di sini kita perlakukan sebagai email.
    user = db.query(User).filter(User.email == form_data.username).first()

    if user is None or not verify_password(form_data.password, user.hashed_password):
        # Pesan generik sengaja disamakan untuk email-salah vs password-salah,
        # agar endpoint ini tidak bisa dipakai menebak email mana yang terdaftar.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email atau password salah")

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Akun dinonaktifkan")

    access_token = create_access_token(user)
    refresh_token = create_refresh_token(user)
    set_refresh_token_cookie(response, refresh_token)

    return TokenResponse(
        access_token=access_token,
        user=UserOut(id=str(user.id), email=user.email, full_name=user.full_name, role=user.role),
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh_access_token(
    response: Response,
    db: Session = Depends(get_db),
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
):
    """
    Dipanggil otomatis oleh frontend (lihat AuthContext.jsx) saat aplikasi
    pertama kali dimuat, untuk mendapatkan access token baru tanpa memaksa
    user login ulang, selama refresh token di cookie masih valid.
    """
    user = verify_refresh_token_and_get_user(db, refresh_token)

    new_access_token = create_access_token(user)
    # Rotasi refresh token setiap kali dipakai (mengurangi jendela waktu kalau
    # refresh token lama sempat bocor sebelum dipakai penyerang).
    new_refresh_token = create_refresh_token(user)
    set_refresh_token_cookie(response, new_refresh_token)

    return TokenResponse(
        access_token=new_access_token,
        user=UserOut(id=str(user.id), email=user.email, full_name=user.full_name, role=user.role),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Naikkan token_version -> semua refresh token yang pernah diterbitkan
    # untuk user ini (termasuk yang mungkin masih tersimpan di device lain)
    # langsung tidak valid lagi.
    user.refresh_token_version += 1
    db.add(user)
    db.commit()

    clear_refresh_token_cookie(response)
    return None


@router.get("/me", response_model=UserOut)
def read_current_user(user: User = Depends(get_current_user)):
    return UserOut(id=str(user.id), email=user.email, full_name=user.full_name, role=user.role)
