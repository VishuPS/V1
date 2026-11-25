"""Server-authoritative workspace permissions; integration billing is unchanged."""
from fastapi import HTTPException
from sqlalchemy import select
from app.models import Subscription, User
from app.auth import current_period
from app.models import ServiceUsage,new_uuid,utcnow
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

PAID={"CREATOR","STARTER","GROWTH"}
FEATURE_PLANS={
    "vault": {"FREE","ONE_TIME",*PAID},
    "advanced_exports":PAID,"bulk_generation":PAID,"label_templates":PAID,"sku_management":PAID,"bulk_editing":PAID,
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
    limits=settings.workspace_monthly_limits.get(plan,{})
    used=dict(session.execute(select(ServiceUsage.category,ServiceUsage.units).where(ServiceUsage.account_scope=="user:"+user.id,ServiceUsage.period_start==current_period(),ServiceUsage.category.in_(limits))).all()) if limits else {}
    return {"plan":plan,"billing_status":status,"policy":user.workspace_policy,"read_only":read_only,
        "features":features,"vault_capacity":capacity_for(user,plan,settings),
        "processing_limit":settings.workspace_batch_limit,"upload_bytes":settings.workspace_upload_bytes,
        "monthly_workspace_limits":settings.workspace_monthly_limits.get(plan,{}),
        "workspace_usage":{key:{"used":used.get(key,0),"limit":limit,"remaining":max(0,limit-used.get(key,0))} for key,limit in limits.items()},
        "sku_capacity":settings.sku_record_limits.get(plan,0),
        "asset_capacity":settings.asset_record_limits.get(plan,0),
        "label_template_capacity":settings.label_template_limits.get(plan,0),
        "service_limits":settings.service_limits[plan].model_dump() if plan in settings.service_limits else {},
        "upgrade_url":"/pricing/"}


def require_feature(session,user,settings,feature):
    result=account_entitlements(session,user,settings)
    if not result["features"].get(feature,False):
        raise HTTPException(403,detail={"code":"feature_not_included","feature":feature,
            "message":"This action requires an active plan that includes this feature. Existing records remain available.","upgrade_url":"/pricing/"})
    return result


def reserve_workspace_units(session,user,settings,category,units):
    """Reserve inside the caller's transaction; failed operations roll back units."""
    if units<1:
        return
    plan=account_entitlements(session,user,settings)["plan"]
    limit=settings.workspace_monthly_limits.get(plan,{}).get(category,0)
    if units>limit:
        raise HTTPException(429,detail={"code":"workspace_quota_exceeded","message":f"The monthly {category} allowance is too small for this request"})
    insert=postgres_insert if session.get_bind().dialect.name=="postgresql" else sqlite_insert
    statement=insert(ServiceUsage).values(id=new_uuid(),account_scope="user:"+user.id,category=category,period_start=current_period(),units=units,updated_at=utcnow())
    used=session.execute(statement.on_conflict_do_update(index_elements=[ServiceUsage.account_scope,ServiceUsage.category,ServiceUsage.period_start],
        set_={"units":ServiceUsage.units+units,"updated_at":utcnow()},where=ServiceUsage.units+units<=limit).returning(ServiceUsage.units)).scalar_one_or_none()
    if used is None:
        raise HTTPException(429,detail={"code":"workspace_quota_exceeded","message":f"The monthly {category} allowance is exhausted"})
