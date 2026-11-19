import csv
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.auth import AuthContext, authenticate_api_key
from app.barcode_tools import CsvRequest, LabelItem, LabelsRequest, RenderRequest, enriched_csv, parse_csv, render_labels, render_symbol
from app.barcodes import BarcodeError, parse_barcode
from app.config import Settings, get_settings
from app.db import get_db
from app.lookup_analytics import LookupOutcome, record_lookup_outcomes_safely
from app.repositories import ProductRepository
from app.service_usage import consume_service_usage, service_balances
from app.services import resolve_product

router = APIRouter(tags=["barcode tools"])
DbSession = Annotated[Session, Depends(get_db)]
Authenticated = Annotated[AuthContext, Depends(authenticate_api_key)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def invalid_tool_request(exc: ValueError) -> HTTPException:
    return HTTPException(422, detail={"code": "invalid_tool_request", "message": str(exc)})


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


@router.post("/v1/labels/render", response_class=Response)
def generate_labels(payload: LabelsRequest, session: DbSession, auth: Authenticated, settings: SettingsDep):
    try:
        data, count = render_labels(payload)
    except ValueError as exc:
        raise invalid_tool_request(exc) from exc
    headers = consume_service_usage(session, auth, settings, "labels", count)
    headers["Content-Disposition"] = 'attachment; filename="barcodenest-labels.pdf"'
    return Response(data, media_type="application/pdf", headers=headers)


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
