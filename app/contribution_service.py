import csv
import io
import ipaddress
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.barcodes import BarcodeError, parse_barcode
from app.contribution_schemas import (
    AdminContributionItem, AdminContributionSummary, BrandSubmissionCreate,
    BulkSubmissionCreate, ContributionItem, MyContributions, OfferCreate,
    ProductCheck, ProductSubmissionCreate, PublicProfile, ReviewAction,
    StoreSubmissionCreate,
)
from app.models import (
    Brand, BrandSubmission, BulkSubmission, Product, ProductOffer,
    ProductSourceRecord, ProductSubmission, Store, StoreSubmission, User,
)
from app.repositories import ProductRepository


CSV_COLUMNS = (
    "gtin", "product_name", "brand", "manufacturer", "category", "net_content",
    "quantity", "model", "mpn", "description", "country_of_sale", "product_url", "image_url",
)
FORMULA_PREFIXES = ("=", "+", "-", "@")


def contribution_error(http_status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=http_status, detail={"code": code, "message": message})


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    result = value.strip()
    return result or None


def normalize_name(value: str) -> str:
    return " ".join(value.casefold().split())


def safe_web_url(value: str | None, *, required: bool = False) -> str | None:
    value = clean_text(value)
    if not value:
        if required:
            raise contribution_error(422, "invalid_url", "A website URL is required")
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError
        hostname = parsed.hostname.rstrip(".").casefold()
        if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
            raise ValueError
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            if "." not in hostname:
                raise ValueError
        else:
            if not address.is_global:
                raise ValueError
        port = parsed.port
        if port not in {None, 80, 443}:
            raise ValueError
        netloc = hostname + (f":{port}" if port else "")
        return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "", parsed.query, ""))
    except (ValueError, UnicodeError) as exc:
        raise contribution_error(422, "invalid_url", "Only public HTTP or HTTPS URLs are allowed") from exc


def check_product(session: Session, raw: str) -> ProductCheck:
    try:
        barcode = parse_barcode(raw)
    except BarcodeError as exc:
        raise contribution_error(422, "invalid_gtin", str(exc)) from exc
    product = ProductRepository(session).find_by_barcode(barcode)
    return ProductCheck(
        submitted_gtin=raw.strip(), canonical_gtin=barcode.gtin14,
        barcode_type=barcode.barcode_type, exists=product is not None,
        product={"name": product.name, "brand": product.brand, "quantity": product.quantity, "image_url": product.image_url} if product else None,
    )


def enforce_rate_limit(session: Session, user_id: str, model, *, limit: int = 20) -> None:
    since = datetime.now(timezone.utc) - timedelta(days=1)
    count = session.scalar(select(func.count()).select_from(model).where(model.submitted_by_user_id == user_id, model.created_at >= since)) or 0
    if count >= limit:
        raise contribution_error(status.HTTP_429_TOO_MANY_REQUESTS, "contribution_rate_limit", "The daily contribution limit has been reached")


def create_product_submission(session: Session, user: User, payload: ProductSubmissionCreate) -> ProductSubmission:
    checked = check_product(session, payload.barcode)
    if checked.exists:
        raise contribution_error(409, "product_exists", "This product is already in BarcodeNest")
    enforce_rate_limit(session, user.id, ProductSubmission)
    contribution_source = "USER_CONTRIBUTED"
    if payload.brand_profile_id:
        brand_profile = session.scalar(select(Brand).where(Brand.id == payload.brand_profile_id, Brand.owner_user_id == user.id, Brand.active.is_(True)))
        if not brand_profile:
            raise contribution_error(403, "brand_ownership_required", "An approved brand owned by your account is required")
        contribution_source = "BRAND_OWNER_CONTRIBUTED"
    if payload.store_profile_id:
        store_profile = session.scalar(select(Store).where(Store.id == payload.store_profile_id, Store.owner_user_id == user.id, Store.active.is_(True)))
        if not store_profile:
            raise contribution_error(403, "store_ownership_required", "An approved store owned by your account is required")
        if payload.brand_profile_id:
            raise contribution_error(422, "ambiguous_contribution_source", "Choose either a brand or store contribution source")
        contribution_source = "STORE_CONTRIBUTED"
    row = ProductSubmission(
        submitted_by_user_id=user.id, submitted_gtin=payload.barcode.strip(), canonical_gtin=checked.canonical_gtin,
        brand_profile_id=payload.brand_profile_id, store_profile_id=payload.store_profile_id,
        product_name=payload.product_name.strip(), brand=payload.brand.strip(),
        manufacturer=clean_text(payload.manufacturer), category=clean_text(payload.category),
        net_content=clean_text(payload.net_content), quantity=clean_text(payload.quantity), model=clean_text(payload.model),
        mpn=clean_text(payload.mpn), description=clean_text(payload.description), country_of_sale=clean_text(payload.country_of_sale),
        product_url=safe_web_url(payload.product_url), image_url=safe_web_url(payload.image_url), contribution_source=contribution_source,
    )
    session.add(row)
    try:
        session.commit(); session.refresh(row)
    except IntegrityError as exc:
        session.rollback()
        raise contribution_error(409, "duplicate_submission", "You already submitted this product") from exc
    return row


def create_store_submission(session: Session, user: User, payload: StoreSubmissionCreate) -> StoreSubmission:
    enforce_rate_limit(session, user.id, StoreSubmission, limit=5)
    website = safe_web_url(payload.website, required=True)
    row = StoreSubmission(
        submitted_by_user_id=user.id, name=payload.name.strip(), normalized_name=normalize_name(payload.name),
        website=website, normalized_website=website, country=payload.country.strip(), description=clean_text(payload.description),
        logo_url=safe_web_url(payload.logo_url), contact_name=clean_text(payload.contact_name),
        contact_email=str(payload.contact_email).casefold() if payload.contact_email else None,
    )
    session.add(row)
    try: session.commit(); session.refresh(row)
    except IntegrityError as exc:
        session.rollback(); raise contribution_error(409, "duplicate_store", "This store has already been submitted") from exc
    return row


def create_brand_submission(session: Session, user: User, payload: BrandSubmissionCreate) -> BrandSubmission:
    enforce_rate_limit(session, user.id, BrandSubmission, limit=5)
    website = safe_web_url(payload.website, required=True)
    row = BrandSubmission(
        submitted_by_user_id=user.id, name=payload.name.strip(), normalized_name=normalize_name(payload.name),
        company=clean_text(payload.company), website=website, normalized_website=website,
        country=clean_text(payload.country), contact_name=clean_text(payload.contact_name),
        business_email=str(payload.business_email).casefold() if payload.business_email else None,
        description=clean_text(payload.description), logo_url=safe_web_url(payload.logo_url),
    )
    session.add(row)
    try: session.commit(); session.refresh(row)
    except IntegrityError as exc:
        session.rollback(); raise contribution_error(409, "duplicate_brand", "This brand has already been submitted") from exc
    return row


def _safe_csv_value(value: str) -> str:
    value = value.strip()
    return "'" + value if value.startswith(FORMULA_PREFIXES) else value


def parse_bulk_csv(payload: BulkSubmissionCreate) -> tuple[list[dict], list[dict]]:
    if not payload.filename.casefold().endswith(".csv"):
        raise contribution_error(422, "invalid_csv", "The bulk file must be a CSV")
    try:
        content = payload.csv_content.lstrip("\ufeff")
        reader = csv.DictReader(io.StringIO(content, newline=""))
        if not reader.fieldnames or any(column not in reader.fieldnames for column in ("gtin", "product_name", "brand")):
            raise contribution_error(422, "invalid_csv_headers", "CSV must include gtin, product_name, and brand")
        rows, errors, seen = [], [], set()
        for number, raw in enumerate(reader, start=2):
            if number > 1001:
                raise contribution_error(413, "csv_row_limit", "A maximum of 1,000 data rows is allowed")
            normalized = {key: _safe_csv_value(raw.get(key) or "") for key in CSV_COLUMNS}
            try:
                parsed = parse_barcode(normalized["gtin"])
                if parsed.gtin14 in seen: raise ValueError("Duplicate GTIN in file")
                if not normalized["product_name"] or not normalized["brand"]: raise ValueError("Product name and brand are required")
                normalized["product_url"] = safe_web_url(normalized["product_url"]) or ""
                normalized["image_url"] = safe_web_url(normalized["image_url"]) or ""
                normalized["canonical_gtin"] = parsed.gtin14
                seen.add(parsed.gtin14); rows.append(normalized)
            except (BarcodeError, ValueError, HTTPException) as exc:
                message = exc.detail["message"] if isinstance(exc, HTTPException) else str(exc)
                errors.append({"row": number, "gtin": normalized["gtin"], "message": message})
        if not rows and errors:
            raise contribution_error(422, "csv_no_valid_rows", "The CSV contains no valid product rows")
        return rows, errors
    except UnicodeError as exc:
        raise contribution_error(422, "invalid_csv_encoding", "CSV must use UTF-8 encoding") from exc


def create_bulk_submission(session: Session, user: User, payload: BulkSubmissionCreate) -> BulkSubmission:
    enforce_rate_limit(session, user.id, BulkSubmission, limit=3)
    rows, errors = parse_bulk_csv(payload)
    row = BulkSubmission(submitted_by_user_id=user.id, filename=payload.filename, row_count=len(rows)+len(errors), valid_row_count=len(rows), rows=rows, validation_errors=errors)
    session.add(row); session.commit(); session.refresh(row); return row


def create_offer(session: Session, user: User, payload: OfferCreate) -> ProductOffer:
    store = session.scalar(select(Store).where(Store.id == payload.store_id, Store.owner_user_id == user.id, Store.active.is_(True)))
    if not store: raise contribution_error(403, "store_ownership_required", "An approved store owned by your account is required")
    parsed = parse_barcode(payload.barcode)
    product = ProductRepository(session).find_by_barcode(parsed)
    if not product: raise contribution_error(404, "product_not_found", "The product must exist before an offer can be submitted")
    row = ProductOffer(store_id=store.id, product_barcode=product.barcode, product_url=safe_web_url(payload.product_url, required=True), price_minor=payload.price_minor, currency=payload.currency, availability=payload.availability)
    session.add(row)
    try: session.commit(); session.refresh(row)
    except IntegrityError as exc: session.rollback(); raise contribution_error(409, "duplicate_offer", "This offer already exists") from exc
    return row


def my_contributions(session: Session, user_id: str) -> MyContributions:
    items: list[ContributionItem] = []
    specs = ((ProductSubmission,"PRODUCT","product_name"),(StoreSubmission,"STORE","name"),(BrandSubmission,"BRAND","name"),(BulkSubmission,"BULK","filename"))
    for model, kind, label in specs:
        for row in session.scalars(select(model).where(model.submitted_by_user_id == user_id).order_by(model.created_at.desc())):
            items.append(ContributionItem(id=row.id,type=kind,label=getattr(row,label),status=row.status,created_at=row.created_at,contributor_message=row.contributor_message))
    items.sort(key=lambda item:item.created_at, reverse=True)
    totals = {s: sum(1 for item in items if item.status == s) for s in ("PENDING","APPROVED","REJECTED","NEEDS_CHANGES")}
    return MyContributions(items=items, totals=totals)


def admin_summary(session: Session) -> AdminContributionSummary:
    count=lambda model: session.scalar(select(func.count()).select_from(model).where(model.status=="PENDING")) or 0
    return AdminContributionSummary(pending_products=count(ProductSubmission),pending_stores=count(StoreSubmission),pending_brands=count(BrandSubmission),pending_bulk_submissions=count(BulkSubmission))


def admin_list(session: Session, kind: str, status_filter: str = "PENDING") -> list[AdminContributionItem]:
    mapping={"PRODUCT":(ProductSubmission,"product_name","brand"),"STORE":(StoreSubmission,"name","country"),"BRAND":(BrandSubmission,"name","company"),"BULK":(BulkSubmission,"filename",None)}
    if kind not in mapping: raise contribution_error(404,"invalid_contribution_type","Unknown contribution type")
    model,label,secondary=mapping[kind]
    rows=session.scalars(select(model).where(model.status==status_filter).order_by(model.created_at)).all()
    result=[]
    for row in rows:
        user=session.get(User,row.submitted_by_user_id)
        data={column.name:getattr(row,column.name) for column in model.__table__.columns if column.name not in {"review_notes","reviewed_by_user_id"}}
        result.append(AdminContributionItem(id=row.id,type=kind,label=getattr(row,label),secondary=getattr(row,secondary) if secondary else None,status=row.status,contributor_name=user.display_name,contributor_email=user.email,created_at=row.created_at,data=data))
    return result


def _slug(value: str, row_id: str) -> str:
    base=re.sub(r"[^a-z0-9]+","-",value.casefold()).strip("-")[:140] or "profile"
    return f"{base}-{row_id[:8]}"


def review_submission(session: Session, kind: str, submission_id: str, action: ReviewAction, admin_id: str):
    mapping={"PRODUCT":ProductSubmission,"STORE":StoreSubmission,"BRAND":BrandSubmission,"BULK":BulkSubmission}
    model=mapping.get(kind)
    if not model: raise contribution_error(404,"invalid_contribution_type","Unknown contribution type")
    row=session.scalar(select(model).where(model.id==submission_id).with_for_update())
    if not row: raise contribution_error(404,"submission_not_found","Contribution not found")
    if row.submitted_by_user_id==admin_id: raise contribution_error(403,"self_review_forbidden","You cannot review your own contribution")
    if row.status!="PENDING": raise contribution_error(409,"already_reviewed","This contribution has already been reviewed")
    now=datetime.now(timezone.utc); row.review_notes=clean_text(action.review_notes); row.contributor_message=clean_text(action.contributor_message); row.reviewed_at=now; row.reviewed_by_user_id=admin_id
    if action.action!="APPROVE":
        row.status = "REJECTED" if action.action == "REJECT" else action.action
        session.commit()
        return row
    if kind=="PRODUCT":
        parsed=parse_barcode(row.canonical_gtin); existing=ProductRepository(session).find_by_barcode(parsed)
        if existing:
            row.status="REJECTED"
            row.contributor_message=row.contributor_message or "This product was added to BarcodeNest before review completed."
            session.commit()
            raise contribution_error(409,"product_exists","The product was added before approval; this submission was closed without overwriting it")
        product=Product(barcode=row.canonical_gtin,barcode_type=parsed.barcode_type,name=row.product_name,brand=row.brand,categories=[row.category] if row.category else [],quantity=row.quantity or row.net_content,image_url=row.image_url,ingredients=None,allergens=[],nutrition={},countries=[row.country_of_sale] if row.country_of_sale else [],source="USER_CONTRIBUTED",source_id=row.id,source_updated_at=now)
        session.add(product); session.flush()
        session.add(ProductSourceRecord(product_barcode=product.barcode,source=row.contribution_source,source_product_id=row.id,source_gtin=row.canonical_gtin,source_url=row.product_url,license="CONTRIBUTOR_TERMS",priority=40,source_metadata={"submission_id":row.id,"contributor_user_id":row.submitted_by_user_id,"approved_at":now.isoformat()}))
    elif kind=="STORE": session.add(Store(owner_user_id=row.submitted_by_user_id,source_submission_id=row.id,slug=_slug(row.name,row.id),name=row.name,website=row.website,country=row.country,description=row.description,logo_url=row.logo_url))
    elif kind=="BRAND": session.add(Brand(owner_user_id=row.submitted_by_user_id,source_submission_id=row.id,slug=_slug(row.name,row.id),name=row.name,company=row.company,website=row.website,country=row.country,description=row.description,logo_url=row.logo_url))
    elif kind=="BULK":
        # Approval authorizes each validated row to become its own moderated product record.
        for item in row.rows:
            if ProductRepository(session).find_by_barcode(parse_barcode(item["canonical_gtin"])): continue
            product=Product(barcode=item["canonical_gtin"],barcode_type=parse_barcode(item["canonical_gtin"]).barcode_type,name=item["product_name"],brand=item["brand"],categories=[item["category"]] if item["category"] else [],quantity=item["quantity"] or item["net_content"],image_url=item["image_url"] or None,ingredients=None,allergens=[],nutrition={},countries=[item["country_of_sale"]] if item["country_of_sale"] else [],source="USER_CONTRIBUTED",source_id=f"{row.id}:{item['canonical_gtin']}",source_updated_at=now)
            session.add(product); session.flush(); session.add(ProductSourceRecord(product_barcode=product.barcode,source="USER_CONTRIBUTED",source_product_id=f"{row.id}:{product.barcode}",source_gtin=product.barcode,source_url=item["product_url"] or None,license="CONTRIBUTOR_TERMS",priority=40,source_metadata={"bulk_submission_id":row.id,"contributor_user_id":row.submitted_by_user_id,"approved_at":now.isoformat()}))
    row.status="APPROVED"; session.commit(); return row


def public_store(session: Session, slug: str) -> PublicProfile:
    row=session.scalar(select(Store).where(Store.slug==slug,Store.active.is_(True)))
    if not row: raise contribution_error(404,"store_not_found","Store not found")
    return PublicProfile.model_validate(row,from_attributes=True)


def public_brand(session: Session, slug: str) -> PublicProfile:
    row=session.scalar(select(Brand).where(Brand.slug==slug,Brand.active.is_(True)))
    if not row: raise contribution_error(404,"brand_not_found","Brand not found")
    return PublicProfile.model_validate(row,from_attributes=True)
