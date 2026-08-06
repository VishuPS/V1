import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

import jwt
from fastapi import Cookie, Depends, HTTPException, Request, Response, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import Settings, get_settings
from app.db import get_db
from app.models import AuthSession, User, new_uuid


ACCESS_COOKIE = "barcodenest_access"
REFRESH_COOKIE = "barcodenest_refresh"
JWT_ALGORITHM = "HS256"
password_hash = PasswordHash.recommended()
dummy_password_hash = password_hash.hash("BarcodeNest timing defense only")
bearer = HTTPBearer(auto_error=False)


def normalize_email(value: str) -> str:
    return value.strip().casefold()


def hash_password(value: str) -> str:
    return password_hash.hash(value)


def verify_password(value: str, encoded: str) -> bool:
    return password_hash.verify(value, encoded)


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _auth_error(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": code, "message": message},
        headers={"WWW-Authenticate": "Bearer"},
    )


def ensure_allowed_origin(request: Request, settings: Settings) -> None:
    origin = request.headers.get("origin")
    if origin and origin not in settings.cors_allowed_origins:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "untrusted_origin", "message": "The request origin is not allowed"},
        )


def _encode_token(
    *, user_id: str, session_id: str, token_type: str, jti: str,
    expires_at: datetime, settings: Settings,
) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": user_id,
            "sid": session_id,
            "jti": jti,
            "typ": token_type,
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": now,
            "exp": expires_at,
        },
        settings.jwt_secret,
        algorithm=JWT_ALGORITHM,
    )


def decode_token(token: str, token_type: str, settings: Settings) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[JWT_ALGORITHM],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["sub", "sid", "jti", "typ", "iat", "exp"]},
        )
    except InvalidTokenError as exc:
        raise _auth_error("invalid_token", "The authentication token is invalid or expired") from exc
    if payload.get("typ") != token_type:
        raise _auth_error("invalid_token_type", "The authentication token type is invalid")
    return payload


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime


def issue_session_tokens(
    session: Session, user: User, settings: Settings,
    *, auth_session: AuthSession | None = None,
) -> TokenPair:
    now = datetime.now(timezone.utc)
    access_expires = now + timedelta(minutes=settings.access_token_minutes)
    refresh_expires = now + timedelta(days=settings.refresh_token_days)
    refresh_jti = secrets.token_urlsafe(32)
    if auth_session is None:
        auth_session = AuthSession(
            id=new_uuid(),
            user_id=user.id,
            refresh_token_hash=_token_hash(refresh_jti),
            expires_at=refresh_expires,
            last_seen_at=now,
        )
        session.add(auth_session)
    else:
        auth_session.refresh_token_hash = _token_hash(refresh_jti)
        auth_session.expires_at = refresh_expires
        auth_session.last_seen_at = now
    access = _encode_token(
        user_id=user.id, session_id=auth_session.id, token_type="access",
        jti=secrets.token_urlsafe(16), expires_at=access_expires, settings=settings,
    )
    refresh = _encode_token(
        user_id=user.id, session_id=auth_session.id, token_type="refresh",
        jti=refresh_jti, expires_at=refresh_expires, settings=settings,
    )
    session.commit()
    return TokenPair(access, refresh, access_expires, refresh_expires)


def set_auth_cookies(response: Response, tokens: TokenPair, settings: Settings) -> None:
    response.set_cookie(
        ACCESS_COOKIE, tokens.access_token, httponly=True,
        secure=settings.auth_cookie_secure, samesite="lax", path="/",
        max_age=settings.access_token_minutes * 60,
    )
    response.set_cookie(
        REFRESH_COOKIE, tokens.refresh_token, httponly=True,
        secure=settings.auth_cookie_secure, samesite="lax", path="/v1/auth",
        max_age=settings.refresh_token_days * 86400,
    )


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/", secure=settings.auth_cookie_secure, samesite="lax")
    response.delete_cookie(REFRESH_COOKIE, path="/v1/auth", secure=settings.auth_cookie_secure, samesite="lax")
    response.headers["Clear-Site-Data"] = '"cache", "cookies", "storage"'


def authenticate_credentials(session: Session, email: str, password: str) -> User:
    user = session.scalar(select(User).where(User.email == normalize_email(email)))
    encoded = user.password_hash if user is not None and user.password_hash else dummy_password_hash
    valid = verify_password(password, encoded)
    if user is None or not valid or not user.active:
        raise _auth_error("invalid_credentials", "The email or password is incorrect")
    user.last_login_at = datetime.now(timezone.utc)
    session.commit()
    return user


@dataclass(frozen=True, slots=True)
class UserContext:
    user: User
    auth_session: AuthSession


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer)],
    access_cookie: Annotated[str | None, Cookie(alias=ACCESS_COOKIE)] = None,
    session: Annotated[Session, Depends(get_db)] = None,
    settings: Annotated[Settings, Depends(get_settings)] = None,
) -> UserContext:
    token = credentials.credentials if credentials else access_cookie
    if not token:
        raise _auth_error("authentication_required", "Authentication is required")
    payload = decode_token(token, "access", settings)
    auth_session = session.scalar(
        select(AuthSession)
        .options(joinedload(AuthSession.user))
        .where(AuthSession.id == payload["sid"], AuthSession.user_id == payload["sub"])
    )
    now = datetime.now(timezone.utc)
    if (
        auth_session is None
        or auth_session.revoked_at is not None
        or _aware(auth_session.expires_at) <= now
        or not auth_session.user.active
    ):
        raise _auth_error("session_revoked", "The session is no longer active")
    auth_session.last_seen_at = now
    session.commit()
    return UserContext(auth_session.user, auth_session)


def rotate_refresh_token(
    session: Session, raw_token: str, settings: Settings
) -> tuple[User, TokenPair]:
    payload = decode_token(raw_token, "refresh", settings)
    auth_session = session.scalar(
        select(AuthSession)
        .where(AuthSession.id == payload["sid"], AuthSession.user_id == payload["sub"])
        .with_for_update()
    )
    now = datetime.now(timezone.utc)
    if auth_session is None or auth_session.revoked_at is not None or _aware(auth_session.expires_at) <= now:
        raise _auth_error("session_revoked", "The session is no longer active")
    if not secrets.compare_digest(auth_session.refresh_token_hash, _token_hash(payload["jti"])):
        auth_session.revoked_at = now
        session.commit()
        raise _auth_error("refresh_token_reused", "The refresh token is no longer valid")
    if not auth_session.user.active:
        raise _auth_error("inactive_user", "The user account is inactive")
    return auth_session.user, issue_session_tokens(
        session, auth_session.user, settings, auth_session=auth_session
    )


def revoke_session(session: Session, auth_session: AuthSession) -> None:
    if auth_session.revoked_at is None:
        auth_session.revoked_at = datetime.now(timezone.utc)
        session.commit()
