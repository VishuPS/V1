import hashlib
import hmac
import ipaddress
from datetime import datetime, timezone
from calendar import monthrange

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import issue_api_key
from app.config import Settings
from app.models import ApiClient, Subscription, SubscriptionPlan, User
from app.schemas import RegistrationCreate, RegistrationVerified
from app.user_auth import hash_password, normalize_email


def registration_network_hash(ip_value: str | None, settings: Settings) -> str | None:
    if not settings.free_tier_ip_limit_enabled:
        return None
    try:
        address = ipaddress.ip_address(ip_value or "")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "registration_network_unavailable",
                "message": "Registration cannot verify the network right now",
            },
        ) from exc
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        identity = str(address.ipv4_mapped)
    elif isinstance(address, ipaddress.IPv6Address):
        identity = f"{ipaddress.ip_network(f'{address}/64', strict=False).network_address}/64"
    else:
        identity = str(address)
    return hmac.new(
        settings.api_key_hash_secret.encode("utf-8"),
        f"free-tier-registration-network-v1:{identity}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def free_tier_limit_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "free_tier_network_limit_reached",
            "message": (
                "A Free account has already been created from this network. "
                "Sign in to the existing account or contact support."
            ),
        },
    )


def provision_free_account(
    session: Session, *, email: str, display_name: str,
    password_hash: str | None, organization: str | None, settings: Settings,
    registration_ip: str | None = None,
) -> tuple[User, RegistrationVerified]:
    """Provision a Free account and its first API key in one transaction."""
    if not settings.registration_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "registration_unavailable",
                "message": "Registration is temporarily unavailable",
            },
        )

    email = normalize_email(email)
    if session.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "email_already_registered",
                "message": "An account already exists for this email address",
            },
        )
    network_hash = registration_network_hash(registration_ip, settings)
    if network_hash and session.scalar(
        select(User.id).where(User.free_tier_registration_ip_hash == network_hash)
    ) is not None:
        raise free_tier_limit_error()

    user = User(
        email=email,
        password_hash=password_hash,
        display_name=display_name,
        organization=organization,
        email_verified_at=None,
        free_tier_registration_ip_hash=network_hash,
    )
    session.add(user)
    try:
        session.flush()
        client = ApiClient(
            owner_user_id=user.id,
            identifier=email,
            display_name=display_name,
            plan="FREE",
        )
        session.add(client)
        session.flush()
        key, raw_key = issue_api_key(
            session, client, settings, name="default", commit=False
        )
        key.owner_user_id = user.id

        plan_record = session.get(SubscriptionPlan, "FREE")
        if plan_record is None:
            plan_record = SubscriptionPlan(
                code="FREE",
                name="Free",
                monthly_lookups=250,
                requests_per_minute=30,
                price_cents=0,
                currency="USD",
                active=True,
            )
            session.add(plan_record)
        now = datetime.now(timezone.utc)
        period_end = now.replace(day=monthrange(now.year, now.month)[1], hour=23, minute=59, second=59, microsecond=999999)
        session.add(Subscription(user_id=user.id, plan_code="FREE", status="active", monthly_call_limit=250, monthly_calls_used=0, usage_period_start=now, usage_period_end=period_end))
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if network_hash and session.scalar(
            select(User.id).where(User.free_tier_registration_ip_hash == network_hash)
        ) is not None:
            raise free_tier_limit_error() from exc
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "email_already_registered",
                "message": "An account already exists for this email address",
            },
        ) from exc

    plan = settings.plan_limits["FREE"]
    return user, RegistrationVerified(
        email=email,
        plan="FREE",
        api_key=raw_key,
        key_prefix=key.key_prefix,
        monthly_lookups=plan.monthly_lookups,
        requests_per_minute=plan.requests_per_minute,
    )


def create_registration(
    session: Session, payload: RegistrationCreate, settings: Settings,
    *, registration_ip: str | None = None,
) -> tuple[User, RegistrationVerified]:
    return provision_free_account(
        session,
        email=str(payload.email),
        display_name=payload.name,
        password_hash=hash_password(payload.password),
        organization=payload.organization,
        settings=settings,
        registration_ip=registration_ip,
    )
