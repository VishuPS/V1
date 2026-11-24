import csv
from io import StringIO

from sqlalchemy import select
from app.config import get_settings
from app.models import MonthlyUsage, ServiceUsage, Subscription, SubscriptionPlan, VaultProduct
from tests.test_user_auth import create_account, login


def auth(client,session_factory,email="vault@example.com"):
    user_id,_=create_account(session_factory,email)
    token=login(client,email).json()["access_token"]
    return user_id,{"Authorization":"Bearer "+token}


def paid(session_factory,user_id,plan="STARTER"):
    with session_factory() as session:
        if plan!="FREE" and not session.get(SubscriptionPlan,plan):
            session.add(SubscriptionPlan(code=plan,name=plan,monthly_lookups=2000,requests_per_minute=300))
            session.flush()
        subscription=session.scalar(select(Subscription).where(Subscription.user_id==user_id))
        subscription.plan_code=plan;session.commit()


def record(barcode="SKU-001",name="Blue storage box",**extra):
    return {"barcode":barcode,"name":name,**extra}


def test_vault_requires_account_and_rejects_foreign_origin(unauthenticated_client,session_factory):
    client=unauthenticated_client
    assert client.get("/v1/account/vault").status_code==401
    _,headers=auth(client,session_factory)
    assert client.post("/v1/account/vault",headers={**headers,"Origin":"https://untrusted.example"},json=record()).status_code==403


def test_private_crud_does_not_consume_lookup_or_tool_units(unauthenticated_client,session_factory):
    client=unauthenticated_client;user_id,headers=auth(client,session_factory)
    response=client.post("/v1/account/vault",headers=headers,json=record(price_minor=1299,currency="EUR",collection="Shop",custom_fields={"Colour":"Blue"}))
    assert response.status_code==200,response.text
    item=response.json()["item"]
    assert response.json()["created"] and item["barcode_type"]=="code128"
    listing=client.get("/v1/account/vault?q=BLUE&collection=Shop",headers=headers)
    assert listing.headers["cache-control"]=="no-store"
    assert listing.json()["total"]==1 and listing.json()["capacity"]==25
    assert listing.json()["collections"]==["Shop"]
    updated=client.put("/v1/account/vault/"+item["id"],headers=headers,json=record(name="Edited",revision=item["revision"]))
    assert updated.status_code==200 and updated.json()["revision"]==2
    assert client.put("/v1/account/vault/"+item["id"],headers=headers,json=record(revision=1)).status_code==409
    assert client.get("/v1/account/vault/"+item["id"],headers=headers).json()["name"]=="Edited"
    with session_factory() as session:
        assert session.scalar(select(MonthlyUsage)) is None
        assert session.scalar(select(ServiceUsage)) is None
        assert session.scalar(select(Subscription).where(Subscription.user_id==user_id)).free_credits_used==0
    assert client.delete("/v1/account/vault/"+item["id"],headers=headers).status_code==204
    assert client.get("/v1/account/vault",headers=headers).json()["used"]==0


def test_accounts_cannot_read_edit_delete_or_export_others_records(unauthenticated_client,session_factory):
    client=unauthenticated_client;first,a=auth(client,session_factory,"first@example.com")
    second,b=auth(client,session_factory,"second@example.com")
    item=client.post("/v1/account/vault",headers=a,json=record()).json()["item"]
    assert client.get("/v1/account/vault",headers=b).json()["items"]==[]
    for method in ["GET","DELETE","PUT"]:
        response=client.request(method,"/v1/account/vault/"+item["id"],headers=b,**({"json":record(revision=1)} if method=="PUT" else {}))
        assert response.status_code==404
    paid(session_factory,second)
    assert client.get("/v1/account/vault/export?format=json",headers=b).json()==[]


def test_capacity_and_duplicate_save_preserve_existing_edits(unauthenticated_client,session_factory):
    from app.main import app
    client=unauthenticated_client;_,headers=auth(client,session_factory)
    settings=get_settings().model_copy(update={"vault_limits":{"FREE":1}})
    app.dependency_overrides[get_settings]=lambda:settings
    item=client.post("/v1/account/vault",headers=headers,json=record("012345678905","My custom name")).json()["item"]
    duplicate=client.post("/v1/account/vault",headers=headers,json=record("0012345678905","Remote name"))
    assert duplicate.status_code==200 and not duplicate.json()["created"]
    assert duplicate.json()["item"]["id"]==item["id"] and duplicate.json()["item"]["name"]=="My custom name"
    assert client.post("/v1/account/vault",headers=headers,json=record("OTHER")).status_code==409
    assert client.get("/v1/account/vault",headers=headers).json()["used"]==1


def test_paid_exports_formula_safety_and_downgrade_retention(unauthenticated_client,session_factory):
    from app.main import app
    client=unauthenticated_client;uid,headers=auth(client,session_factory)
    paid(session_factory,uid)
    settings=get_settings().model_copy(update={"vault_limits":{"FREE":1,"STARTER":3}})
    app.dependency_overrides[get_settings]=lambda:settings
    one=client.post("/v1/account/vault",headers=headers,json=record("00123","=danger",collection="A")).json()["item"]
    client.post("/v1/account/vault",headers=headers,json=record("00234","Other",collection="B"))
    exported=client.get("/v1/account/vault/export?format=csv&collection=A",headers=headers)
    assert exported.status_code==200
    rows=list(csv.DictReader(StringIO(exported.content.decode("utf-8-sig"))))
    assert len(rows)==1 and rows[0]["name"]=="'=danger" and rows[0]["barcode"]=="00123"
    assert len(client.get("/v1/account/vault/export?format=json",headers=headers).json())==2
    paid(session_factory,uid,"FREE")
    assert client.get("/v1/account/vault",headers=headers).json()["used"]==2
    assert client.post("/v1/account/vault",headers=headers,json=record("00345")).status_code==409
    assert client.get("/v1/account/vault/export",headers=headers).status_code==403
    assert client.put("/v1/account/vault/"+one["id"],headers=headers,json=record("00123","Still editable",revision=1)).status_code==200
    assert client.delete("/v1/account/vault/"+one["id"],headers=headers).status_code==204


def test_bad_data_rejected_and_update_collision_is_safe(unauthenticated_client,session_factory):
    client=unauthenticated_client;_,headers=auth(client,session_factory)
    for extra in [{"image_url":"javascript:alert(1)"},{"price_minor":-1},{"name":"  "},{"custom_fields":{"x":"a"*501}},{"barcode":"3017620422004","barcode_type":"ean13"}]:
        assert client.post("/v1/account/vault",headers=headers,json={**record(),**extra}).status_code==422
    first=client.post("/v1/account/vault",headers=headers,json=record("A")).json()["item"]
    second=client.post("/v1/account/vault",headers=headers,json=record("B")).json()["item"]
    assert client.put("/v1/account/vault/"+second["id"],headers=headers,json=record("A",revision=1)).status_code==409
    assert client.get("/v1/account/vault/"+second["id"],headers=headers).json()["barcode"]=="B"


def test_account_labels_share_label_quota(unauthenticated_client,session_factory):
    client=unauthenticated_client;_,headers=auth(client,session_factory)
    response=client.post("/v1/account/labels/render",headers=headers,json={"items":[{"value":"SKU-001","symbology":"code128","title":"Box"}]})
    assert response.status_code==200 and response.content.startswith(b"%PDF")
    categories=client.get("/v1/account/services/usage",headers=headers).json()["categories"]
    assert categories["labels"]["used"]==1 and categories["generation"]["used"]==0
