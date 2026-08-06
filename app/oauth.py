import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import HTTPException, status
from jwt.exceptions import InvalidTokenError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import OAuthIdentity, User
from app.registration import provision_free_account
from app.schemas import RegistrationVerified
from app.user_auth import JWT_ALGORITHM, normalize_email


OAUTH_STATE_COOKIE = "barcodenest_oauth_state"
OAUTH_VERIFIER_COOKIE = "barcodenest_oauth_verifier"
OAUTH_COOKIE_PATH = "/v1/auth/oauth"


@dataclass(frozen=True, slots=True)
class ProviderIdentity:
    provider: str
    subject: str
    email: str
    display_name: str


def provider_credentials(provider: str, settings: Settings) -> tuple[str, str]:
    credentials = {
        "google": (settings.google_oauth_client_id, settings.google_oauth_client_secret),
        "github": (settings.github_oauth_client_id, settings.github_oauth_client_secret),
    }.get(provider)
    if credentials is None:
        raise HTTPException(status_code=404, detail={"code": "oauth_provider_not_found", "message": "OAuth provider not found"})
    if not all(credentials):
        raise HTTPException(status_code=503, detail={"code": "oauth_provider_unavailable", "message": f"{provider.title()} sign-in is not configured"})
    return credentials[0], credentials[1]  # type: ignore[return-value]


def callback_url(provider: str, settings: Settings) -> str:
    return f"{settings.jwt_issuer.rstrip('/')}/v1/auth/oauth/{provider}/callback"


def create_oauth_request(provider: str, settings: Settings) -> tuple[str, str, str]:
    client_id, _ = provider_credentials(provider, settings)
    nonce = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    now = datetime.now(timezone.utc)
    state = jwt.encode(
        {"typ": "oauth_state", "provider": provider, "nonce": nonce, "iat": now, "exp": now + timedelta(minutes=settings.oauth_state_minutes)},
        settings.jwt_secret,
        algorithm=JWT_ALGORITHM,
    )
    common = {
        "client_id": client_id,
        "redirect_uri": callback_url(provider, settings),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if provider == "google":
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({**common, "response_type": "code", "scope": "openid email profile", "prompt": "select_account"})
    else:
        url = "https://github.com/login/oauth/authorize?" + urlencode({**common, "scope": "user:email"})
    return url, state, verifier


def validate_oauth_state(raw_state: str, state_cookie: str | None, provider: str, settings: Settings) -> None:
    if not state_cookie or not secrets.compare_digest(raw_state, state_cookie):
        raise HTTPException(status_code=400, detail={"code": "invalid_oauth_state", "message": "The sign-in request expired or could not be verified"})
    try:
        payload = jwt.decode(raw_state, settings.jwt_secret, algorithms=[JWT_ALGORITHM], options={"require": ["typ", "provider", "nonce", "iat", "exp"]})
    except InvalidTokenError as exc:
        raise HTTPException(status_code=400, detail={"code": "invalid_oauth_state", "message": "The sign-in request expired or could not be verified"}) from exc
    if payload.get("typ") != "oauth_state" or payload.get("provider") != provider:
        raise HTTPException(status_code=400, detail={"code": "invalid_oauth_state", "message": "The sign-in request could not be verified"})


async def fetch_provider_identity(provider: str, code: str, verifier: str, settings: Settings) -> ProviderIdentity:
    client_id, client_secret = provider_credentials(provider, settings)
    redirect_uri = callback_url(provider, settings)
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        if provider == "google":
            token_response = await client.post("https://oauth2.googleapis.com/token", data={"client_id": client_id, "client_secret": client_secret, "code": code, "code_verifier": verifier, "grant_type": "authorization_code", "redirect_uri": redirect_uri})
            token_response.raise_for_status()
            token = token_response.json()["access_token"]
            profile_response = await client.get("https://openidconnect.googleapis.com/v1/userinfo", headers={"Authorization": f"Bearer {token}"})
            profile_response.raise_for_status()
            profile = profile_response.json()
            if not profile.get("email_verified"):
                raise HTTPException(status_code=400, detail={"code": "provider_email_unverified", "message": "Google did not return a verified email address"})
            return ProviderIdentity("google", str(profile["sub"]), normalize_email(profile["email"]), profile.get("name") or profile["email"].split("@", 1)[0])

        token_response = await client.post("https://github.com/login/oauth/access_token", data={"client_id": client_id, "client_secret": client_secret, "code": code, "code_verifier": verifier, "redirect_uri": redirect_uri}, headers={"Accept": "application/json"})
        token_response.raise_for_status()
        token = token_response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        profile_response = await client.get("https://api.github.com/user", headers=headers)
        emails_response = await client.get("https://api.github.com/user/emails", headers=headers)
        profile_response.raise_for_status()
        emails_response.raise_for_status()
        profile = profile_response.json()
        emails = emails_response.json()
        email_entry = next((item for item in emails if item.get("primary") and item.get("verified")), None) or next((item for item in emails if item.get("verified")), None)
        if email_entry is None:
            raise HTTPException(status_code=400, detail={"code": "provider_email_unavailable", "message": "GitHub did not return a verified email address"})
        email = normalize_email(email_entry["email"])
        return ProviderIdentity("github", str(profile["id"]), email, profile.get("name") or profile.get("login") or email.split("@", 1)[0])


def authenticate_oauth_identity(session: Session, identity: ProviderIdentity, settings: Settings) -> tuple[User, RegistrationVerified | None]:
    linked = session.scalar(select(OAuthIdentity).where(OAuthIdentity.provider == identity.provider, OAuthIdentity.provider_subject == identity.subject))
    if linked is not None:
        linked.provider_email = identity.email
        linked.user.last_login_at = datetime.now(timezone.utc)
        session.commit()
        return linked.user, None

    user = session.scalar(select(User).where(User.email == identity.email))
    registration = None
    if user is None:
        user, registration = provision_free_account(session, email=identity.email, display_name=identity.display_name, password_hash=None, organization=None, settings=settings)
    if not user.active:
        raise HTTPException(status_code=403, detail={"code": "inactive_user", "message": "This BarcodeNest account is inactive"})
    user.email_verified_at = user.email_verified_at or datetime.now(timezone.utc)
    user.last_login_at = datetime.now(timezone.utc)
    session.add(OAuthIdentity(user_id=user.id, provider=identity.provider, provider_subject=identity.subject, provider_email=identity.email))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        linked = session.scalar(select(OAuthIdentity).where(OAuthIdentity.provider == identity.provider, OAuthIdentity.provider_subject == identity.subject))
        if linked is None:
            raise
        user = linked.user
        registration = None
    return user, registration
