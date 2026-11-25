"""Server-authoritative workspace permissions; integration billing is unchanged."""
from fastapi import HTTPException
from sqlalchemy import select
from app.models import Subscription, User

PAID={"CREATOR","STARTER","GROWTH"}
FEATURE_PLANS={
    "vault": {"FREE","ONE_TIME",*PAID},
    "advanced_exports":PAID,"bulk_generation":PAID,"label_templates":PAID,"sku_management":PAID,
    "inventory":{"STARTER","GROWTH"},"assets":{"STARTER","GROWTH"},"catalogue_auditing":{"STARTER","GROWTH"},
    "team_workspace":set(),
}


def capacity_for(user:User,plan:str,settings)->int:
    limits=settings.vault_limits
    if user.workspace_policy!="legacy" and (plan=="CREATOR" or "new_customer_vault_limits" in settings.model_fields_set):
        limits=settings.new_customer_vault_limits
    return limits.get(plan,0)


def account_entitlements(session,user:User,settings)->dict:
    sub=session.scalar(select(Subscription).where(Subscription.user_id==user.id).order_by(Subscription.created_at.desc()).limit(1))
    plan=sub.plan_code if sub else "FREE"
    status=sub.status if sub else "active"
    # Preserve the existing past-due billing grace behavior; do not change Stripe.
    read_only=plan in PAID and status not in {"active","trialing","past_due"}
    features={feature:plan in plans and not read_only for feature,plans in FEATURE_PLANS.items()}
    return {"plan":plan,"billing_status":status,"policy":user.workspace_policy,"read_only":read_only,
        "features":features,"vault_capacity":capacity_for(user,plan,settings),
        "processing_limit":settings.workspace_batch_limit,"upload_bytes":settings.workspace_upload_bytes,
        "service_limits":settings.service_limits[plan].model_dump() if plan in settings.service_limits else {},
        "upgrade_url":"/pricing/"}


def require_feature(session,user,settings,feature):
    result=account_entitlements(session,user,settings)
    if not result["features"].get(feature,False):
        raise HTTPException(403,detail={"code":"feature_not_included","feature":feature,
            "message":"This action requires an active plan that includes this feature. Existing records remain available.","upgrade_url":"/pricing/"})
    return result
