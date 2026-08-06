from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import issue_api_key
from app.config import Settings
from app.models import ApiClient, Subscription, SubscriptionPlan, User
from app.schemas import RegistrationCreate, RegistrationVerified
from app.user_auth import hash_password, normalize_email


def provision_free_account(
    session: Session, *, email: str, display_name: str,
    password_hash: str | None, organization: str | None, settings: Settings,
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

    user = User(
        email=email,
        password_hash=password_hash,
        display_name=display_name,
        organization=organization,
        email_verified_at=None,
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
                monthly_lookups=500,
                requests_per_minute=30,
                price_cents=0,
                currency="EUR",
                active=True,
            )
            session.add(plan_record)
        session.add(Subscription(user_id=user.id, plan_code="FREE", status="active"))
        session.commit()
    except IntegrityError as exc:
        session.rollback()
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
    session: Session, payload: RegistrationCreate, settings: Settings
) -> tuple[User, RegistrationVerified]:
    return provision_free_account(
        session,
        email=str(payload.email),
        display_name=payload.name,
        password_hash=hash_password(payload.password),
        organization=payload.organization,
        settings=settings,
    )
