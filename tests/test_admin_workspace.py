from datetime import date, datetime, timezone
import hashlib

from sqlalchemy import select
from app.auth import current_period
from app.models import ServiceUsage, VaultProduct, Subscription, SubscriptionPlan
from tests.test_admin import create_user, auth_headers


def test_admin_can_see_workspace_units_and_capacity(unauthenticated_client,session_factory):
    client=unauthenticated_client
    create_user(session_factory,email="admin-tools@example.com",name="Admin",is_admin=True)
    user_id=create_user(session_factory,email="tools@example.com",name="Tools")
    period=current_period()
    with session_factory() as session:
        for category,units in [("generation",12),("scans",3),("labels",7),("enrichment",4),("validation",5),("vault_exports",2)]:
            session.add(ServiceUsage(account_scope="user:"+user_id,category=category,period_start=period,units=units))
        old=date(period.year-1,period.month,1)
        session.add(ServiceUsage(account_scope="user:"+user_id,category="generation",period_start=old,units=999))
        session.add(VaultProduct(user_id=user_id,identity_key=hashlib.sha256(b"private").hexdigest(),barcode="PRIVATE-ID",barcode_type="code128",name="Secret product",notes="Private note"))
        session.commit()
    headers=auth_headers(client,"admin-tools@example.com")
    details=client.get("/v1/admin/users/"+user_id,headers=headers)
    assert details.status_code==200,details.text
    workspace=details.json()["workspace_usage"]
    assert workspace["tools"]["generation"]["used"]==12
    assert workspace["tools"]["generation"]["limit"]==100
    assert workspace["tools"]["generation"]["remaining"]==88
    assert workspace["tools"]["scans"]["used"]==3
    assert workspace["tools"]["validation"]["limit"] is None
    assert workspace["vault_products"]==1 and workspace["vault_capacity"]==25
    assert workspace["vault_created_this_month"]==1
    assert "Private note" not in details.text and "Secret product" not in details.text
    assert any(item["kind"]=="tool_usage" for item in details.json()["recent_activity"])
    users=client.get("/v1/admin/users?search=tools@example.com",headers=headers).json()
    assert users["items"][0]["workspace_usage"]["tools"]["labels"]["used"]==7
    summary=client.get("/v1/admin/dashboard",headers=headers).json()
    assert summary["tool_units_month"]["generation"]==12
    assert summary["vault_products"]==1 and summary["vault_accounts"]==1
    nonadmin=auth_headers(client,"tools@example.com")
    assert client.get("/v1/admin/users/"+user_id,headers=nonadmin).status_code==403


def test_validation_and_exports_record_unmetered_activity(unauthenticated_client,session_factory):
    client=unauthenticated_client
    user_id=create_user(session_factory,email="activity@example.com",name="Activity")
    headers=auth_headers(client,"activity@example.com")
    result=client.post("/v1/account/barcodes/validate",headers=headers,json={"value":"3017620422004"})
    assert result.status_code==200 and not result.json()["valid"]
    assert client.get("/v1/account/vault/export",headers=headers).status_code==403
    with session_factory() as session:
        session.add(SubscriptionPlan(code="CREATOR",name="Creator",monthly_lookups=0,requests_per_minute=120))
        sub=session.scalar(select(Subscription).where(Subscription.user_id==user_id));sub.plan_code="CREATOR"
        session.commit()
    assert client.get("/v1/account/vault/export?format=json",headers=headers).status_code==200
    assert client.get("/v1/account/vault/export?format=csv",headers=headers).status_code==200
    with session_factory() as session:
        rows={r.category:r.units for r in session.scalars(select(ServiceUsage).where(ServiceUsage.account_scope=="user:"+user_id))}
        assert rows=={"validation":1,"vault_exports":2}
        sub=session.scalar(select(Subscription).where(Subscription.user_id==user_id))
        assert sub.free_credits_used==0
