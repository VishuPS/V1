import hashlib
import hmac
import ipaddress
from datetime import datetime, timezone
from calendar import monthrange

from fastapi import HTTPException, status
from sqlalchemy import or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import issue_api_key
from app.config import Settings
from app.models import ApiClient, Subscription, SubscriptionPlan, User
from app.schemas import RegistrationCreate, RegistrationVerified
from app.user_auth import hash_password, normalize_email


# Exact public mailbox-provider domains; never infer exemptions from a brand
# substring or every address sharing a corporate email hosting provider.
NETWORK_LIMIT_EXEMPT_DOMAINS = (
    "126.com",
    "163.com",
    "abv.bg",
    "aim.com",
    "aol.com",
    "bk.ru",
    "bol.com.br",
    "centrum.cz",
    "daum.net",
    "fastmail.com",
    "free.fr",
    "gmail.com",
    "gmx.com",
    "gmx.de",
    "gmx.net",
    "googlemail.com",
    "hanmail.net",
    "hey.com",
    "hotmail.co.uk",
    "hotmail.com",
    "hotmail.de",
    "hotmail.es",
    "hotmail.fr",
    "hotmail.it",
    "icloud.com",
    "inbox.ru",
    "interia.pl",
    "laposte.net",
    "list.ru",
    "live.co.uk",
    "live.com",
    "mac.com",
    "mail.com",
    "mail.pt",
    "mail.ru",
    "me.com",
    "msn.com",
    "naver.com",
    "nesscoegroup.com",
    "o2.pl",
    "onet.pl",
    "orange.fr",
    "outlook.com",
    "outlook.com.br",
    "outlook.de",
    "outlook.es",
    "outlook.fr",
    "outlook.it",
    "outlook.pt",
    "pm.me",
    "proton.me",
    "protonmail.com",
    "qq.com",
    "rambler.ru",
    "rediffmail.com",
    "rocketmail.com",
    "sapo.pt",
    "seznam.cz",
    "terra.com.br",
    "tuta.com",
    "tutamail.com",
    "tutanota.com",
    "uol.com.br",
    "web.de",
    "wp.pl",
    "yahoo.ca",
    "yahoo.co.in",
    "yahoo.co.jp",
    "yahoo.co.uk",
    "yahoo.com",
    "yahoo.com.au",
    "yahoo.com.br",
    "yahoo.de",
    "yahoo.es",
    "yahoo.fr",
    "yahoo.in",
    "yahoo.it",
    "yandex.com",
    "yandex.ru",
    "ymail.com",
    "zoho.com",
    "zohomail.com",
)


def network_limit_exempt(email: str) -> bool:
    return email.strip().rsplit("@", 1)[-1].lower() in NETWORK_LIMIT_EXEMPT_DOMAINS


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
                "Three Free accounts have already been created from this network. "
                "Sign in to an existing account or contact support."
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
    # Exact email-domain exemption; lookalike and subdomains do not qualify.
    exempt = network_limit_exempt(email)
    network_hash = None if exempt else registration_network_hash(registration_ip, settings)
    if network_hash:
        # Three unique claim slots preserve the existing unique constraint. The
        # first slot is the legacy hash so existing real-network claims count.
        slots = [network_hash] + [
            hashlib.sha256(f"{network_hash}:slot:{slot}".encode()).hexdigest()
            for slot in (2, 3)
        ]
        if session.get_bind().dialect.name == "postgresql":
            # Serialize registrations for this network across workers/instances.
            lock_id = int.from_bytes(bytes.fromhex(network_hash)[:8], "big", signed=True)
            session.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id})
        exempt_email = or_(*(
            User.email.ilike(f"%@{domain}") for domain in NETWORK_LIMIT_EXEMPT_DOMAINS
        ))
        used = set(session.scalars(
            select(User.free_tier_registration_ip_hash).where(
                User.free_tier_registration_ip_hash.in_(slots),
                ~exempt_email,
            )
        ))
        # Existing exempt accounts must not occupy a claim slot.
        session.execute(update(User).where(
            User.free_tier_registration_ip_hash.in_(slots),
            exempt_email,
        ).values(free_tier_registration_ip_hash=None))
        network_hash = next((slot for slot in slots if slot not in used), None)
        if network_hash is None:
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
