from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.barcodes import BarcodeError, parse_barcode
from app.auth import AuthContext, authenticate_api_key, consume_usage, usage_headers
from app.config import Settings, get_settings
from app.db import get_db
from app.schemas import BatchRequest, BatchResponse, ErrorResponse, LookupResult
from app.services import resolve_product
from app.email_service import send_usage_warning_safely
from app.lookup_analytics import LookupOutcome, record_lookup_outcomes_safely

router = APIRouter()
DbSession = Annotated[Session, Depends(get_db)]
Authenticated = Annotated[AuthContext, Depends(authenticate_api_key)]


def api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


@router.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/v1/products/{barcode}",
    response_model=LookupResult,
    responses={
        401: {"model": ErrorResponse, "description": "Missing or invalid API key"},
        400: {"model": ErrorResponse, "description": "Invalid barcode"},
        404: {"model": ErrorResponse, "description": "Product not found"},
        429: {"model": ErrorResponse, "description": "Rate or monthly quota exceeded"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
    tags=["products"],
)
def get_product(
    barcode: str,
    session: DbSession,
    auth: Authenticated,
    response: Response,
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)],
) -> LookupResult:
    try:
        parsed = parse_barcode(barcode)
    except BarcodeError as exc:
        raise api_error(status.HTTP_400_BAD_REQUEST, "invalid_barcode", str(exc)) from exc
    resolution = resolve_product(session, barcode, settings=settings)
    result = resolution.result
    if not result.found:
        session.rollback()
        record_lookup_outcomes_safely(
            session,
            auth,
            [LookupOutcome(parsed.gtin14, parsed.barcode_type, False,
                           resolution.local_found, resolution.fallback_attempted,
                           tuple(resolution.providers_attempted), resolution.provider_found)],
            endpoint_type="single",
            retention_days=settings.lookup_analytics_retention_days,
        )
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "product_not_found",
            "The barcode is valid, but no product was found",
        )
    session.rollback()
    record_lookup_outcomes_safely(
        session,
        auth,
        [LookupOutcome(parsed.gtin14, parsed.barcode_type, True,
                       resolution.local_found, resolution.fallback_attempted,
                       tuple(resolution.providers_attempted), resolution.provider_found)],
        endpoint_type="single",
        retention_days=settings.lookup_analytics_retention_days,
    )
    usage = consume_usage(session, auth, 1)
    for header, value in usage_headers(auth, usage).items():
        response.headers[header] = value
    background_tasks.add_task(send_usage_warning_safely, settings, usage.warning)
    return result


@router.post(
    "/v1/products/batch",
    response_model=BatchResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Missing or invalid API key"},
        422: {"model": ErrorResponse, "description": "Invalid batch"},
        429: {"model": ErrorResponse, "description": "Rate or monthly quota exceeded"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
    tags=["products"],
)
def batch_products(
    payload: BatchRequest,
    session: DbSession,
    settings: Annotated[Settings, Depends(get_settings)],
    auth: Authenticated,
    response: Response,
    background_tasks: BackgroundTasks,
) -> BatchResponse:
    if not payload.barcodes:
        raise api_error(422, "empty_batch", "At least one barcode is required")
    if len(payload.barcodes) > settings.batch_limit:
        raise api_error(
            422,
            "batch_limit_exceeded",
            f"A maximum of {settings.batch_limit} barcodes is allowed",
        )
    resolutions = [resolve_product(session, barcode, settings=settings) for barcode in payload.barcodes]
    results = [resolution.result for resolution in resolutions]
    outcomes = [
        LookupOutcome(result.canonical_gtin, result.barcode_type, result.found,
                      resolution.local_found, resolution.fallback_attempted,
                      tuple(resolution.providers_attempted), resolution.provider_found)
        for result, resolution in zip(results, resolutions)
        if result.valid and result.canonical_gtin and result.barcode_type
    ]
    session.rollback()
    record_lookup_outcomes_safely(
        session,
        auth,
        outcomes,
        endpoint_type="batch",
        retention_days=settings.lookup_analytics_retention_days,
    )
    usage = consume_usage(session, auth, 1)
    for header, value in usage_headers(auth, usage).items():
        response.headers[header] = value
    background_tasks.add_task(send_usage_warning_safely, settings, usage.warning)
    return BatchResponse(results=results)
