from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.billing import StripeClient, process_stripe_event, verify_stripe_event
from app.config import Settings, get_settings
from app.db import get_db
from app.models import Subscription
from app.schemas import BillingRedirect, CheckoutCreate
from app.user_auth import UserContext, ensure_allowed_origin, get_current_user

router = APIRouter(prefix="/v1/billing", tags=["billing"])
DbSession = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
CurrentUser = Annotated[UserContext, Depends(get_current_user)]


def current_subscription(session: Session, user_id: str) -> Subscription:
    return session.scalar(select(Subscription).where(Subscription.user_id == user_id).order_by(Subscription.created_at.desc()))


@router.post("/checkout", response_model=BillingRedirect)
def checkout(payload: CheckoutCreate, request: Request, context: CurrentUser, session: DbSession, settings: SettingsDep) -> BillingRedirect:
    ensure_allowed_origin(request, settings)
    subscription = current_subscription(session, context.user.id)
    url = StripeClient(settings).checkout(context.user.id, context.user.email, payload.plan, subscription.provider_customer_id if subscription else None)
    return BillingRedirect(url=url)


@router.post("/portal", response_model=BillingRedirect)
def portal(request: Request, context: CurrentUser, session: DbSession, settings: SettingsDep) -> BillingRedirect:
    ensure_allowed_origin(request, settings)
    subscription = current_subscription(session, context.user.id)
    if not subscription or not subscription.provider_customer_id:
        from fastapi import HTTPException
        raise HTTPException(409, detail={"code":"stripe_customer_required","message":"No Stripe billing account exists yet"})
    return BillingRedirect(url=StripeClient(settings).portal(subscription.provider_customer_id))


@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request, session: DbSession, settings: SettingsDep) -> dict[str, bool]:
    payload = await request.body()
    event = verify_stripe_event(payload, request.headers.get("Stripe-Signature"), settings.stripe_webhook_secret)
    processed = process_stripe_event(session, settings, event, StripeClient(settings))
    return {"received": True, "processed": processed}
