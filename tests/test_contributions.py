from sqlalchemy import select

from app.contribution_schemas import BulkSubmissionCreate
from app.contribution_service import parse_bulk_csv, safe_web_url
from app.models import (
    Brand, BrandSubmission, BulkSubmission, Product, ProductOffer,
    ProductSourceRecord, ProductSubmission, Store, StoreSubmission, User,
)
from tests.test_admin import auth_headers, create_user


MISSING = "4006381333931"
CANONICAL = "04006381333931"
PRODUCT = {"barcode": MISSING, "product_name": "Organic oat drink", "brand": "Field & Mill", "accepted_terms": True}


def user_headers(client, session_factory, email="contributor@example.com"):
    user_id = create_user(session_factory, email=email, name="Contributor")
    return user_id, auth_headers(client, email)


def test_public_product_check_validates_normalizes_and_detects_existing(unauthenticated_client):
    invalid = unauthenticated_client.get("/v1/contributions/products/check/4006381333932")
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_gtin"
    missing = unauthenticated_client.get(f"/v1/contributions/products/check/{MISSING}")
    assert missing.status_code == 200
    assert missing.json() == {
        "submitted_gtin": MISSING, "canonical_gtin": CANONICAL,
        "barcode_type": "EAN-13", "exists": False, "product": None,
    }
    existing = unauthenticated_client.get("/v1/contributions/products/check/3017620422003")
    assert existing.json()["exists"] is True
    assert existing.json()["product"]["name"] == "Nutella"


def test_product_submission_requires_auth_and_remains_pending(unauthenticated_client, session_factory):
    assert unauthenticated_client.post("/v1/contributions/products", json=PRODUCT).status_code == 401
    user_id, headers = user_headers(unauthenticated_client, session_factory)
    response = unauthenticated_client.post("/v1/contributions/products", json=PRODUCT, headers=headers)
    assert response.status_code == 201
    with session_factory() as session:
        row = session.scalar(select(ProductSubmission))
        assert row.status == "PENDING"
        assert row.submitted_gtin == MISSING and row.canonical_gtin == CANONICAL
        assert row.submitted_by_user_id == user_id
        assert row.terms_version == "2026-08" and row.terms_accepted_at is not None
        assert session.get(Product, CANONICAL) is None


def test_contribution_terms_must_be_explicitly_accepted(unauthenticated_client, session_factory):
    _, headers = user_headers(unauthenticated_client, session_factory)
    without_consent = {key: value for key, value in PRODUCT.items() if key != "accepted_terms"}
    assert unauthenticated_client.post("/v1/contributions/products", json=without_consent, headers=headers).status_code == 422
    assert unauthenticated_client.post("/v1/contributions/products", json={**PRODUCT, "accepted_terms": False}, headers=headers).status_code == 422


def test_existing_and_equivalent_product_cannot_be_submitted(unauthenticated_client, session_factory):
    _, headers = user_headers(unauthenticated_client, session_factory)
    assert unauthenticated_client.post("/v1/contributions/products", json={**PRODUCT,"barcode":"3017620422003"}, headers=headers).status_code == 409
    assert unauthenticated_client.post("/v1/contributions/products", json=PRODUCT, headers=headers).status_code == 201
    duplicate = unauthenticated_client.post("/v1/contributions/products", json={**PRODUCT,"barcode":CANONICAL}, headers=headers)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "duplicate_submission"


def test_admin_approval_creates_product_and_provenance(unauthenticated_client, session_factory):
    _, headers = user_headers(unauthenticated_client, session_factory)
    submission = unauthenticated_client.post("/v1/contributions/products", json={**PRODUCT,"product_url":"https://example.com/products/oat"}, headers=headers).json()
    create_user(session_factory,email="reviewer@example.com",name="Reviewer",is_admin=True)
    admin = auth_headers(unauthenticated_client,"reviewer@example.com")
    approved = unauthenticated_client.post(f"/v1/admin/contributions/PRODUCT/{submission['id']}/review",json={"action":"APPROVE","contributor_message":"Thank you"},headers=admin)
    assert approved.status_code == 200 and approved.json()["status"] == "APPROVED"
    with session_factory() as session:
        product=session.get(Product,CANONICAL); source=session.scalar(select(ProductSourceRecord).where(ProductSourceRecord.product_barcode==CANONICAL))
        assert product.name == PRODUCT["product_name"] and product.source == "USER_CONTRIBUTED"
        assert source.source == "USER_CONTRIBUTED"
        assert source.source_metadata["submission_id"] == submission["id"]


def test_rejection_and_self_review_security(unauthenticated_client, session_factory):
    user_id, headers = user_headers(unauthenticated_client, session_factory)
    submission = unauthenticated_client.post("/v1/contributions/products",json=PRODUCT,headers=headers).json()
    with session_factory() as session:
        user=session.get(User,user_id); user.is_admin=True; session.commit()
    self_review=unauthenticated_client.post(f"/v1/admin/contributions/PRODUCT/{submission['id']}/review",json={"action":"APPROVE"},headers=headers)
    assert self_review.status_code == 403
    create_user(session_factory,email="other-admin@example.com",name="Admin",is_admin=True)
    admin=auth_headers(unauthenticated_client,"other-admin@example.com")
    rejected=unauthenticated_client.post(f"/v1/admin/contributions/PRODUCT/{submission['id']}/review",json={"action":"REJECT"},headers=admin)
    assert rejected.status_code == 200
    with session_factory() as session: assert session.get(Product,CANONICAL) is None


def test_product_appearing_before_approval_is_not_overwritten(unauthenticated_client, session_factory):
    _, headers=user_headers(unauthenticated_client,session_factory); submission=unauthenticated_client.post("/v1/contributions/products",json=PRODUCT,headers=headers).json()
    with session_factory() as session:
        session.add(Product(barcode=CANONICAL,barcode_type="GTIN-14",name="Existing winner",categories=[],allergens=[],nutrition={},countries=[],source="MANUAL_VERIFIED",source_id="race"));session.commit()
    create_user(session_factory,email="race-admin@example.com",name="Admin",is_admin=True);admin=auth_headers(unauthenticated_client,"race-admin@example.com")
    response=unauthenticated_client.post(f"/v1/admin/contributions/PRODUCT/{submission['id']}/review",json={"action":"APPROVE"},headers=admin)
    assert response.status_code==409
    with session_factory() as session: assert session.get(Product,CANONICAL).name=="Existing winner"


def test_store_validation_duplicates_approval_and_public_profile(unauthenticated_client,session_factory):
    _,headers=user_headers(unauthenticated_client,session_factory);payload={"name":"Corner Market","website":"https://shop.example.com/catalog","country":"Portugal","description":"Local grocer","accepted_terms":True}
    assert unauthenticated_client.post("/v1/contributions/stores",json={**payload,"website":"http://127.0.0.1/admin"},headers=headers).status_code==422
    first=unauthenticated_client.post("/v1/contributions/stores",json=payload,headers=headers);assert first.status_code==201
    assert unauthenticated_client.post("/v1/contributions/stores",json=payload,headers=headers).status_code==409
    assert unauthenticated_client.get("/v1/contributions/stores/corner-market").status_code==404
    create_user(session_factory,email="store-admin@example.com",name="Admin",is_admin=True);admin=auth_headers(unauthenticated_client,"store-admin@example.com")
    assert unauthenticated_client.post(f"/v1/admin/contributions/STORE/{first.json()['id']}/review",json={"action":"APPROVE"},headers=admin).status_code==200
    with session_factory() as session:
        store=session.scalar(select(Store));slug=store.slug;assert store.verified is False
        submitted=session.scalar(select(StoreSubmission));assert submitted.terms_accepted_at is not None
    assert unauthenticated_client.get(f"/v1/contributions/stores/{slug}").json()["name"]=="Corner Market"


def test_brand_requires_admin_verification_and_rejected_not_public(unauthenticated_client,session_factory):
    _,headers=user_headers(unauthenticated_client,session_factory);payload={"name":"North Star Foods","website":"https://northstar.example.com","business_email":"hello@northstar.example.com","accepted_terms":True}
    first=unauthenticated_client.post("/v1/contributions/brands",json=payload,headers=headers);assert first.status_code==201
    with session_factory() as session:
        submitted=session.scalar(select(BrandSubmission));assert submitted.status=="PENDING" and submitted.terms_accepted_at is not None
        assert session.scalar(select(Brand)) is None
    create_user(session_factory,email="brand-admin@example.com",name="Admin",is_admin=True);admin=auth_headers(unauthenticated_client,"brand-admin@example.com")
    rejected=unauthenticated_client.post(f"/v1/admin/contributions/BRAND/{first.json()['id']}/review",json={"action":"REJECT"},headers=admin);assert rejected.status_code==200
    with session_factory() as session: assert session.scalar(select(Brand)) is None


def test_brand_admin_approval_creates_unverified_public_profile(unauthenticated_client,session_factory):
    _,headers=user_headers(unauthenticated_client,session_factory);payload={"name":"Harvest House","company":"Harvest Group","website":"https://harvest.example.com","country":"PT","accepted_terms":True}
    submission=unauthenticated_client.post("/v1/contributions/brands",json=payload,headers=headers).json()
    create_user(session_factory,email="harvest-admin@example.com",name="Admin",is_admin=True);admin=auth_headers(unauthenticated_client,"harvest-admin@example.com")
    assert unauthenticated_client.post(f"/v1/admin/contributions/BRAND/{submission['id']}/review",json={"action":"APPROVE"},headers=admin).status_code==200
    with session_factory() as session: brand=session.scalar(select(Brand));slug=brand.slug;assert brand.verified is False
    public=unauthenticated_client.get(f"/v1/contributions/brands/{slug}");assert public.status_code==200 and public.json()["name"]=="Harvest House"


def test_bulk_csv_validation_duplicates_formula_and_pending_storage(unauthenticated_client,session_factory):
    content="gtin,product_name,brand,manufacturer,category,net_content,quantity,model,mpn,description,country_of_sale,product_url,image_url\n4006381333931,=Injected,Field Mill,,,,,,,,,https://example.com/p,\n4006381333931,Duplicate,Field Mill,,,,,,,,,,\n4006381333932,Bad checksum,Field Mill,,,,,,,,,,\n"
    rows,errors=parse_bulk_csv(BulkSubmissionCreate(filename="catalog.csv",csv_content=content,accepted_terms=True))
    assert len(rows)==1 and rows[0]["product_name"].startswith("'=")
    assert len(errors)==2
    _,headers=user_headers(unauthenticated_client,session_factory);response=unauthenticated_client.post("/v1/contributions/bulk",json={"filename":"catalog.csv","csv_content":content,"accepted_terms":True},headers=headers)
    assert response.status_code==201
    with session_factory() as session:
        bulk=session.scalar(select(BulkSubmission));assert bulk.status=="PENDING" and bulk.terms_accepted_at is not None
        assert session.get(Product,CANONICAL) is None


def test_approved_bulk_rows_create_products_with_provenance(unauthenticated_client,session_factory):
    content="gtin,product_name,brand\n4006381333931,Organic oats,Field Mill\n"
    _,headers=user_headers(unauthenticated_client,session_factory);submission=unauthenticated_client.post("/v1/contributions/bulk",json={"filename":"catalog.csv","csv_content":content,"accepted_terms":True},headers=headers).json()
    create_user(session_factory,email="bulk-admin@example.com",name="Admin",is_admin=True);admin=auth_headers(unauthenticated_client,"bulk-admin@example.com")
    approved=unauthenticated_client.post(f"/v1/admin/contributions/BULK/{submission['id']}/review",json={"action":"APPROVE"},headers=admin)
    assert approved.status_code==200
    with session_factory() as session:
        assert session.get(Product,CANONICAL).name=="Organic oats"
        assert session.scalar(select(ProductSourceRecord).where(ProductSourceRecord.product_barcode==CANONICAL)).source_metadata["bulk_submission_id"]==submission["id"]


def test_bulk_limits_and_dangerous_urls():
    for value in ("javascript:alert(1)","file:///etc/passwd","http://localhost/x","http://10.0.0.1/x","https://intranet/x"):
        try: safe_web_url(value)
        except Exception: pass
        else: raise AssertionError(f"accepted dangerous URL {value}")
    header="gtin,product_name,brand\n"; rows="".join(f"4006381333931,Name {i},Brand\n" for i in range(1001))
    try: parse_bulk_csv(BulkSubmissionCreate(filename="too-many.csv",csv_content=header+rows,accepted_terms=True))
    except Exception as exc: assert getattr(exc,"status_code",None)==413
    else: raise AssertionError("oversized row count accepted")


def test_offer_requires_owned_approved_store_and_stays_pending(unauthenticated_client,session_factory):
    owner_id,headers=user_headers(unauthenticated_client,session_factory)
    with session_factory() as session:
        submission=StoreSubmission(submitted_by_user_id=owner_id,name="Shop",normalized_name="shop",website="https://shop.example.com",normalized_website="https://shop.example.com",country="PT",status="APPROVED")
        session.add(submission);session.flush();store=Store(owner_user_id=owner_id,source_submission_id=submission.id,slug="shop",name="Shop",website=submission.website,country="PT");session.add(store);session.commit();store_id=store.id
    response=unauthenticated_client.post("/v1/contributions/offers",json={"store_id":store_id,"barcode":"3017620422003","product_url":"https://shop.example.com/nutella","price_minor":499,"currency":"eur"},headers=headers)
    assert response.status_code==201
    with session_factory() as session: assert session.scalar(select(ProductOffer)).status=="PENDING"


def test_non_admin_cannot_read_moderation_queue(unauthenticated_client,session_factory):
    _,headers=user_headers(unauthenticated_client,session_factory)
    assert unauthenticated_client.get("/v1/admin/contributions/summary",headers=headers).status_code==403
    assert unauthenticated_client.get("/v1/admin/contributions/PRODUCT",headers=headers).status_code==403


def test_my_contributions_exposes_message_not_internal_notes(unauthenticated_client,session_factory):
    _,headers=user_headers(unauthenticated_client,session_factory);response=unauthenticated_client.post("/v1/contributions/products",json=PRODUCT,headers=headers)
    with session_factory() as session:
        row=session.get(ProductSubmission,response.json()["id"]);row.review_notes="internal secret";row.contributor_message="Please add evidence";row.status="NEEDS_CHANGES";session.commit()
    body=unauthenticated_client.get("/v1/contributions/mine",headers=headers).json()
    assert body["items"][0]["contributor_message"]=="Please add evidence"
    assert "internal secret" not in str(body)
