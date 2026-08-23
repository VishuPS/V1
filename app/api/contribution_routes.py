from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.admin_auth import AdminContext
from app.config import Settings, get_settings
from app.contribution_schemas import (
    AdminContributionItem, AdminContributionSummary, BrandSubmissionCreate,
    BulkSubmissionCreate, MyContributions, OfferCreate, ProductCheck,
    ProductSubmissionCreate, PublicProfile, ReviewAction, StoreSubmissionCreate,
    SubmissionResponse,
)
from app.contribution_service import (
    admin_list, admin_summary, check_product, create_brand_submission,
    create_bulk_submission, create_offer, create_product_submission,
    create_store_submission, my_contributions, public_brand, public_store,
    review_submission,
)
from app.db import get_db
from app.user_auth import UserContext, ensure_allowed_origin, get_current_user


router = APIRouter(prefix="/v1/contributions", tags=["contributions"])
admin_router = APIRouter(prefix="/v1/admin/contributions", tags=["admin", "contributions"])
DbSession = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
CurrentUser = Annotated[UserContext, Depends(get_current_user)]


@router.get("/products/check/{barcode}", response_model=ProductCheck)
def product_check(barcode: str, session: DbSession) -> ProductCheck:
    return check_product(session, barcode)


@router.post("/products", response_model=SubmissionResponse, status_code=201)
def submit_product(payload: ProductSubmissionCreate, request: Request, context: CurrentUser, session: DbSession, settings: SettingsDep):
    ensure_allowed_origin(request, settings)
    return create_product_submission(session, context.user, payload)


@router.post("/stores", response_model=SubmissionResponse, status_code=201)
def submit_store(payload: StoreSubmissionCreate, request: Request, context: CurrentUser, session: DbSession, settings: SettingsDep):
    ensure_allowed_origin(request, settings)
    return create_store_submission(session, context.user, payload)


@router.post("/brands", response_model=SubmissionResponse, status_code=201)
def submit_brand(payload: BrandSubmissionCreate, request: Request, context: CurrentUser, session: DbSession, settings: SettingsDep):
    ensure_allowed_origin(request, settings)
    return create_brand_submission(session, context.user, payload)


@router.post("/bulk", response_model=SubmissionResponse, status_code=201)
def submit_bulk(payload: BulkSubmissionCreate, request: Request, context: CurrentUser, session: DbSession, settings: SettingsDep):
    ensure_allowed_origin(request, settings)
    return create_bulk_submission(session, context.user, payload)


@router.post("/offers", response_model=SubmissionResponse, status_code=201)
def submit_offer(payload: OfferCreate, request: Request, context: CurrentUser, session: DbSession, settings: SettingsDep):
    ensure_allowed_origin(request, settings)
    return create_offer(session, context.user, payload)


@router.get("/mine", response_model=MyContributions)
def mine(context: CurrentUser, session: DbSession) -> MyContributions:
    return my_contributions(session, context.user.id)


@router.get("/stores/{slug}", response_model=PublicProfile)
def store_profile(slug: str, session: DbSession) -> PublicProfile:
    return public_store(session, slug)


@router.get("/brands/{slug}", response_model=PublicProfile)
def brand_profile(slug: str, session: DbSession) -> PublicProfile:
    return public_brand(session, slug)


@admin_router.get("/summary", response_model=AdminContributionSummary)
def moderation_summary(_: AdminContext, session: DbSession) -> AdminContributionSummary:
    return admin_summary(session)


@admin_router.get("/{kind}", response_model=list[AdminContributionItem])
def moderation_list(kind: str, _: AdminContext, session: DbSession, status_filter: Annotated[str, Query(alias="status", pattern="^(PENDING|APPROVED|REJECTED|NEEDS_CHANGES)$")] = "PENDING"):
    return admin_list(session, kind.upper(), status_filter)


@admin_router.post("/{kind}/{submission_id}/review", response_model=SubmissionResponse)
def moderation_review(kind: str, submission_id: str, payload: ReviewAction, request: Request, context: AdminContext, session: DbSession, settings: SettingsDep):
    ensure_allowed_origin(request, settings)
    return review_submission(session, kind.upper(), submission_id, payload, context.user.id)
