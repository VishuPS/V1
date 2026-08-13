from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.admin_auth import AdminContext
from app.admin_schemas import (
    AdminAccountStatusUpdate,
    AdminDashboardSummary,
    AdminRegenerateKeyRequest,
    AdminRegeneratedKey,
    AdminRoleUpdate,
    AdminUserDetails,
    AdminUserList,
    LookupAnalyticsSummary,
    LookupMissList,
)
from app.admin_service import (
    dashboard_summary,
    list_users,
    lookup_analytics_summary,
    lookup_misses,
    regenerate_user_key,
    set_account_active,
    set_admin_role,
    user_details,
)
from app.config import Settings, get_settings
from app.db import get_db
from app.user_auth import ensure_allowed_origin


router = APIRouter(prefix="/v1/admin", tags=["admin"])
DbSession = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("/dashboard", response_model=AdminDashboardSummary)
def dashboard(_: AdminContext, session: DbSession) -> AdminDashboardSummary:
    return dashboard_summary(session)


@router.get("/analytics/lookups", response_model=LookupAnalyticsSummary)
def lookup_summary(
    _: AdminContext,
    session: DbSession,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> LookupAnalyticsSummary:
    return lookup_analytics_summary(session, days=days)


@router.get("/analytics/misses", response_model=LookupMissList)
def misses(
    _: AdminContext,
    session: DbSession,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> LookupMissList:
    return lookup_misses(session, days=days, limit=limit)


@router.get("/users", response_model=AdminUserList)
def users(
    _: AdminContext,
    session: DbSession,
    search: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AdminUserList:
    return list_users(session, search=search, limit=limit, offset=offset)


@router.get("/users/{user_id}", response_model=AdminUserDetails)
def user(user_id: str, _: AdminContext, session: DbSession) -> AdminUserDetails:
    return user_details(session, user_id)


@router.post("/users/{user_id}/status", response_model=AdminUserDetails)
def update_status(
    user_id: str,
    payload: AdminAccountStatusUpdate,
    request: Request,
    context: AdminContext,
    session: DbSession,
    settings: SettingsDep,
) -> AdminUserDetails:
    ensure_allowed_origin(request, settings)
    return set_account_active(
        session, actor_id=context.user.id, user_id=user_id, active=payload.active
    )


@router.post("/users/{user_id}/admin-role", response_model=AdminUserDetails)
def update_admin_role(
    user_id: str,
    payload: AdminRoleUpdate,
    request: Request,
    context: AdminContext,
    session: DbSession,
    settings: SettingsDep,
) -> AdminUserDetails:
    ensure_allowed_origin(request, settings)
    return set_admin_role(
        session, actor_id=context.user.id, user_id=user_id, is_admin=payload.is_admin
    )


@router.post("/users/{user_id}/api-keys/regenerate", response_model=AdminRegeneratedKey)
def regenerate_key(
    user_id: str,
    payload: AdminRegenerateKeyRequest,
    request: Request,
    _: AdminContext,
    session: DbSession,
    settings: SettingsDep,
) -> AdminRegeneratedKey:
    ensure_allowed_origin(request, settings)
    return regenerate_user_key(
        session, settings, user_id=user_id, key_id=payload.key_id, name=payload.name
    )
