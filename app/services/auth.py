from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import decode_token, hash_password, verify_password
from app.db.session import get_db
from app.models.user import User
from app.services.email import send_password_reset_email, send_verification_email

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_PREFIX}/auth/login")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc_datetime(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    return db.query(User).filter(User.email == email.lower()).first()


def normalize_name(value: str) -> str:
    return " ".join(value.strip().split())


def build_display_name(first_name: Optional[str], last_name: Optional[str], email: str) -> str:
    pieces = [piece for piece in [first_name, last_name] if piece]
    if pieces:
        return " ".join(pieces)

    email_prefix = email.split("@", 1)[0]
    return email_prefix.replace(".", " ").replace("_", " ").title()


def create_user(db: Session, first_name: str, last_name: str, email: str, password: str) -> User:
    normalized_email = email.lower()
    normalized_first_name = normalize_name(first_name)
    normalized_last_name = normalize_name(last_name)
    user = User(
        first_name=normalized_first_name,
        last_name=normalized_last_name,
        display_name=build_display_name(normalized_first_name, normalized_last_name, normalized_email),
        email=normalized_email,
        password_hash=hash_password(password),
        email_verified=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def build_social_email(provider: str, provider_subject: str) -> str:
    digest = hashlib.sha256(f"{provider}:{provider_subject}".encode("utf-8")).hexdigest()[:24]
    return f"{provider}.{digest}@social.tryclothes.local"


def get_or_create_guest_user(db: Session) -> User:
    guest_email = "guest.tryon@system.tryclothes.local"
    user = get_user_by_email(db, guest_email)
    if user:
        return user

    user = User(
        first_name="Guest",
        last_name="User",
        display_name="Guest",
        email=guest_email,
        password_hash=hash_password(secrets.token_urlsafe(32)),
        email_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_guest_session_user(db: Session) -> User:
    guest_suffix = secrets.token_urlsafe(10).replace("-", "").replace("_", "").lower()
    guest_email = f"guest.{guest_suffix}@guest.tryclothes.local"
    user = User(
        first_name="Guest",
        last_name="Session",
        display_name="Guest",
        email=guest_email,
        password_hash=hash_password(secrets.token_urlsafe(32)),
        email_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, email: str, password: str) -> Optional[User]:
    user = get_user_by_email(db, email)
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def get_or_create_social_user(
    db: Session,
    provider: str,
    provider_subject: str,
    email: Optional[str] = None,
    display_name: Optional[str] = None,
) -> User:
    social_email = build_social_email(provider, provider_subject)
    user = get_user_by_email(db, social_email)
    if user:
        if display_name and not user.display_name:
            user.display_name = display_name.strip()
            db.commit()
            db.refresh(user)
        return user

    first_name = provider.title()
    last_name = "Member"
    normalized_display_name = display_name.strip() if display_name else f"{provider.title()} Member"
    if display_name:
        parts = [piece for piece in normalized_display_name.split(" ") if piece]
        if len(parts) >= 2:
            first_name = parts[0]
            last_name = " ".join(parts[1:])
        elif len(parts) == 1:
            first_name = parts[0]
            last_name = "Member"

    user = User(
        first_name=first_name,
        last_name=last_name,
        display_name=normalized_display_name,
        email=social_email,
        password_hash=hash_password(secrets.token_urlsafe(32)),
        email_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_email_verification_token(db: Session, user: User) -> str:
    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_email_token(raw_token)
    expires_at = utc_now() + timedelta(minutes=settings.EMAIL_VERIFICATION_TOKEN_EXPIRE_MINUTES)

    user.email_verification_token_hash = token_hash
    user.email_verification_expires_at = expires_at
    user.email_verification_sent_at = utc_now()
    db.commit()
    db.refresh(user)
    return raw_token


def send_verification_for_user(db: Session, user: User) -> None:
    token = create_email_verification_token(db, user)
    verification_url = build_verification_url(token)
    send_verification_email(
        to_email=user.email,
        recipient_name=user.display_name or user.first_name or "there",
        verification_url=verification_url,
    )


def verify_email_token(db: Session, token: str) -> User:
    token_hash = _hash_email_token(token)
    user = db.query(User).filter(User.email_verification_token_hash == token_hash).first()
    if not user:
        raise HTTPException(status_code=400, detail="The verification link is invalid.")

    if user.email_verified:
        return user

    expires_at = as_utc_datetime(user.email_verification_expires_at)
    if not expires_at or expires_at < utc_now():
        raise HTTPException(status_code=400, detail="The verification link has expired.")

    user.email_verified = True
    user.email_verification_token_hash = None
    user.email_verification_expires_at = None
    db.commit()
    db.refresh(user)
    return user


def resend_verification_email(db: Session, email: str) -> bool:
    user = get_user_by_email(db, email)
    if not user or user.email_verified:
        return False

    send_verification_for_user(db, user)
    return True


def mark_user_email_verified(db: Session, user: User) -> User:
    user.email_verified = True
    user.email_verification_token_hash = None
    user.email_verification_expires_at = None
    user.email_verification_sent_at = None
    db.commit()
    db.refresh(user)
    return user


def create_password_reset_token(db: Session, user: User) -> str:
    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw_token)
    expires_at = utc_now() + timedelta(minutes=settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES)

    user.password_reset_token_hash = token_hash
    user.password_reset_expires_at = expires_at
    user.password_reset_sent_at = utc_now()
    db.commit()
    db.refresh(user)
    return raw_token


def send_password_reset_for_user(db: Session, user: User) -> None:
    token = create_password_reset_token(db, user)
    reset_url = build_password_reset_url(token)
    send_password_reset_email(
        to_email=user.email,
        recipient_name=user.display_name or user.first_name or "there",
        reset_url=reset_url,
    )


def request_password_reset(db: Session, email: str) -> bool:
    user = get_user_by_email(db, email)
    if not user:
        return False

    send_password_reset_for_user(db, user)
    return True


def reset_password_with_token(db: Session, token: str, new_password: str) -> User:
    user = _get_user_by_password_reset_token(db, token)

    user.password_hash = hash_password(new_password)
    user.password_reset_token_hash = None
    user.password_reset_expires_at = None
    user.password_reset_sent_at = None
    db.commit()
    db.refresh(user)
    return user


def validate_password_reset_token(db: Session, token: str) -> User:
    return _get_user_by_password_reset_token(db, token)


def build_verification_url(token: str) -> str:
    base = settings.APP_PUBLIC_BASE_URL.rstrip("/")
    return f"{base}{settings.API_V1_PREFIX}/auth/verify-email?token={token}"


def build_password_reset_url(token: str) -> str:
    base = settings.APP_PUBLIC_BASE_URL.rstrip("/")
    return f"{base}{settings.API_V1_PREFIX}/auth/reset-password?token={token}"


def _hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_email_token(value: str) -> str:
    return _hash_token(value)


def _get_user_by_password_reset_token(db: Session, token: str) -> User:
    token_hash = _hash_token(token)
    user = db.query(User).filter(User.password_reset_token_hash == token_hash).first()
    if not user:
        raise HTTPException(status_code=400, detail="The password reset link is invalid.")

    expires_at = as_utc_datetime(user.password_reset_expires_at)
    if not expires_at or expires_at < utc_now():
        raise HTTPException(status_code=400, detail="The password reset link has expired.")

    return user


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = decode_token(token)
    except ValueError as exc:
        raise credentials_exception from exc

    user_id = payload.get("sub")
    if not user_id:
        raise credentials_exception

    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if not user:
        raise credentials_exception
    return user
