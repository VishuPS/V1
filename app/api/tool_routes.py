import csv
from typing import Annotated

from datetime import datetime, timezone
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth import AuthContext, authenticate_api_key, authorized_key_context
from app.barcode_tools import BatchRenderRequest, CsvRequest, LabelItem, LabelsRequest, RenderRequest, ValidationRequest, enriched_csv, parse_csv, render_archive, render_labels, render_symbol
from app.barcodes import BarcodeError, parse_barcode
from app.config import Settings, get_settings
from app.db import get_db
from app.lookup_analytics import LookupOutcome, record_lookup_outcomes_safely
from app.repositories import ProductRepository
from app.service_usage import consume_service_usage, limits_for, service_balances, record_activity, account_scope
from app.services import resolve_product
from app.models import ApiKey
from app.user_auth import UserContext, ensure_allowed_origin, get_current_user
from app.api.auth_routes import account_client
from app.barcode_scanner import ScanRequest, read_scan_image, decode_scan_image
from app.api.routes import get_product

router = APIRouter(tags=["barcode tools"])
DbSession = Annotated[Session, Depends(get_db)]
Authenticated = Annotated[AuthContext, Depends(authenticate_api_key)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def account_tool_context(request: Request, context: Annotated[UserContext, Depends(get_current_user)],
                         session: DbSession, settings: SettingsDep) -> AuthContext:
    ensure_allowed_origin(request, settings)
    client = account_client(session, context.user)
    key = session.scalar(select(ApiKey).where(
        ApiKey.client_id == client.id, ApiKey.active.is_(True),
        or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > datetime.now(timezone.utc)),
    ).order_by(ApiKey.created_at).limit(1))
    if key is None:
        raise HTTPException(409, detail={"code": "api_key_required", "message": "Create an active API key in your account to use barcode tools"})
    return authorized_key_context(key, session, settings, request)


AccountAuthenticated = Annotated[AuthContext, Depends(account_tool_context)]


def invalid_tool_request(exc: ValueError) -> HTTPException:
    return HTTPException(422, detail={"code": "invalid_tool_request", "message": str(exc)})


@router.post("/v1/barcodes/scan")
def scan_barcode(payload: ScanRequest, session: DbSession, auth: Authenticated, settings: SettingsDep, response: Response):
    try:
        image = read_scan_image(payload)
    except ValueError as exc:
        raise invalid_tool_request(exc) from exc
    # A valid image consumes one scan attempt, even if no barcode is readable.
    try:
        response.headers.update(consume_service_usage(session, auth, settings, "scans", 1))
        return decode_scan_image(image)
    finally:
        image.close()


@router.post("/v1/account/barcodes/scan")
def account_scan_barcode(payload: ScanRequest, session: DbSession, auth: AccountAuthenticated, settings: SettingsDep, response: Response):
    return scan_barcode(payload, session, auth, settings, response)


@router.get("/v1/account/products/{barcode}")
def account_lookup_product(barcode: str, session: DbSession, auth: AccountAuthenticated,
                           settings: SettingsDep, response: Response, background_tasks: BackgroundTasks):
    return get_product(barcode, session, auth, response, background_tasks, settings)


@router.get("/v1/services/usage")
def tool_usage(session: DbSession, auth: Authenticated, settings: SettingsDep) -> dict:
    return service_balances(session, auth, settings)


@router.post("/v1/barcodes/render", response_class=Response)
def generate_barcode(payload: RenderRequest, session: DbSession, auth: Authenticated, settings: SettingsDep):
    try:
        data = render_symbol(payload)
    except ValueError as exc:
        raise invalid_tool_request(exc) from exc
    headers = consume_service_usage(session, auth, settings, "generation", 1)
    headers["Content-Disposition"] = f'attachment; filename="barcode.{payload.format}"'
    return Response(data, media_type="image/svg+xml" if payload.format == "svg" else "image/png", headers=headers)


@router.post("/v1/barcodes/batch-render", response_class=Response)
def generate_batch(payload: BatchRenderRequest, session: DbSession, auth: Authenticated, settings: SettingsDep):
    maximum = limits_for(session, auth, settings).batch_generation_max
    if maximum == 0:
        raise HTTPException(403, detail={"code": "paid_plan_required", "message": "ZIP batch generation requires Creator, Starter or Growth"})
    if len(payload.items) > maximum:
        raise HTTPException(422, detail={"code": "batch_generation_limit", "message": f"Your plan supports up to {maximum} images per ZIP request"})
    try:
        data = render_archive(payload)
    except ValueError as exc:
        raise invalid_tool_request(exc) from exc
    headers = consume_service_usage(session, auth, settings, "generation", len(payload.items))
    headers["Content-Disposition"] = 'attachment; filename="barcodes.zip"'
    return Response(data, media_type="application/zip", headers=headers)


@router.post("/v1/account/barcodes/render", response_class=Response)
def account_generate_barcode(payload: RenderRequest, session: DbSession, auth: AccountAuthenticated, settings: SettingsDep):
    return generate_barcode(payload, session, auth, settings)


@router.post("/v1/account/barcodes/batch-render", response_class=Response)
def account_generate_batch(payload: BatchRenderRequest, session: DbSession, auth: AccountAuthenticated, settings: SettingsDep):
    return generate_batch(payload, session, auth, settings)


@router.get("/v1/account/services/usage")
def account_tool_usage(session: DbSession, auth: AccountAuthenticated, settings: SettingsDep):
    return service_balances(session, auth, settings)


@router.post("/v1/account/barcodes/validate")
def account_validate_barcode(payload: ValidationRequest, auth: AccountAuthenticated, session: DbSession):
    record_activity(session,account_scope(auth),"validation")
    try:
        parsed = parse_barcode(payload.value)
    except BarcodeError as exc:
        return {"valid": False, "error": str(exc)}
    return {"valid": True, "value": parsed.value, "format": parsed.barcode_type,
            "expected": parsed.value[-1], "gtin14": parsed.gtin14, "equivalents": parsed.equivalents}


@router.post("/v1/labels/render", response_class=Response)
def generate_labels(payload: LabelsRequest, session: DbSession, auth: Authenticated, settings: SettingsDep):
    try:
        data, count = render_labels(payload)
    except ValueError as exc:
        raise invalid_tool_request(exc) from exc
    headers = consume_service_usage(session, auth, settings, "labels", count)
    headers["Content-Disposition"] = 'attachment; filename="barcodenest-labels.pdf"'
    return Response(data, media_type="application/pdf", headers=headers)


@router.post("/v1/account/labels/render", response_class=Response)
def account_generate_labels(payload: LabelsRequest, session: DbSession, auth: AccountAuthenticated, settings: SettingsDep):
    return generate_labels(payload, session, auth, settings)


@router.post("/v1/labels/from-csv", response_class=Response)
def labels_from_csv(payload: CsvRequest, session: DbSession, auth: Authenticated, settings: SettingsDep):
    _, rows = read_csv(payload)
    try:
        items = [LabelItem(
            value=row["barcode"], symbology=row.get("symbology") or "ean13",
            title=row.get("title") or "", detail=row.get("detail") or "",
            copies=row.get("copies") or 1,
        ) for row in rows]
        request = LabelsRequest(items=items)
    except ValueError as exc:
        raise invalid_tool_request(exc) from exc
    return generate_labels(request, session, auth, settings)


def read_csv(payload: CsvRequest):
    try:
        return parse_csv(payload.csv_text)
    except (ValueError, csv.Error) as exc:
        raise invalid_tool_request(ValueError(str(exc))) from exc


@router.post("/v1/products/enrichment-preview")
def preview_enrichment(payload: CsvRequest, session: DbSession, auth: Authenticated, settings: SettingsDep):
    _, rows = read_csv(payload)
    found, invalid = 0, 0
    for row in rows:
        try:
            parsed = parse_barcode(row["barcode"])
        except BarcodeError:
            invalid += 1
            continue
        found += int(ProductRepository(session).find_by_barcode(parsed) is not None)
    return {
        "rows": len(rows), "units_required": len(rows), "invalid_rows": invalid,
        "local_matches": found, "local_unmatched": len(rows) - invalid - found,
        "balance": service_balances(session, auth, settings)["categories"]["enrichment"],
        "note": "Preview checks the current local catalog. Processing can also use configured fallback sources.",
    }


@router.post("/v1/products/enrich-csv", response_class=Response)
def enrich_products(payload: CsvRequest, session: DbSession, auth: Authenticated, settings: SettingsDep):
    fields, rows = read_csv(payload)
    headers = consume_service_usage(session, auth, settings, "enrichment", len(rows))
    resolutions = [resolve_product(session, row["barcode"], settings=settings) for row in rows]
    results = [resolution.result for resolution in resolutions]
    output = enriched_csv(fields, rows, results)
    session.rollback()
    outcomes = [LookupOutcome(
        item.result.canonical_gtin, item.result.barcode_type, item.result.found,
        item.local_found, item.fallback_attempted, tuple(item.providers_attempted), item.provider_found,
    ) for item in resolutions if item.result.valid and item.result.canonical_gtin and item.result.barcode_type]
    record_lookup_outcomes_safely(session, auth, outcomes, endpoint_type="enrichment",
                                retention_days=settings.lookup_analytics_retention_days)
    headers["Content-Disposition"] = 'attachment; filename="enriched-products.csv"'
    headers["X-Enrichment-Matched"] = str(sum(item.found for item in results))
    return Response(output.encode("utf-8-sig"), media_type="text/csv", headers=headers)
