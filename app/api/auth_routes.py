from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, Response, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import current_period, issue_api_key
from app.config import Settings, get_settings
from app.db import get_db
from app.models import ApiClient, ApiKey, AuthSession, MonthlyUsage, Subscription, User
from app.oauth import (
    OAUTH_COOKIE_PATH, OAUTH_STATE_COOKIE, OAUTH_VERIFIER_COOKIE,
    authenticate_oauth_identity, create_oauth_request, fetch_provider_identity,
    validate_oauth_state,
)
from app.registration import create_registration
from app.schemas import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeySummary,
    LoginRequest,
    RefreshRequest,
    RegistrationCreate,
    RegistrationVerified,
    SubscriptionSummary,
    TokenResponse,
    UsageSummary,
    UserResponse,
)
from app.user_auth import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    UserContext,
    authenticate_credentials,
    ensure_allowed_origin,
    get_current_user,
    issue_session_tokens,
    rotate_refresh_token,
    revoke_session,
    set_auth_cookies,
    clear_auth_cookies,
    bearer,
    decode_token,
)


router = APIRouter(prefix="/v1", tags=["account"])
DbSession = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
CurrentUser = Annotated[UserContext, Depends(get_current_user)]


def token_response(tokens, settings: Settings) -> TokenResponse:
    return TokenResponse(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_in=settings.access_token_minutes * 60,
    )


def account_client(session: Session, user: User) -> ApiClient:
    client = session.scalar(
        select(ApiClient).where(ApiClient.owner_user_id == user.id)
    )
    if client is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "account_not_provisioned", "message": "The account has no API client"},
        )
    return client


@router.get("/auth/oauth/{provider}", include_in_schema=False)
def oauth_start(provider: str, settings: SettingsDep) -> RedirectResponse:
    url, state_value, verifier = create_oauth_request(provider, settings)
    response = RedirectResponse(url, status_code=status.HTTP_302_FOUND)
    cookie_options = {"httponly": True, "secure": settings.auth_cookie_secure, "samesite": "lax", "path": OAUTH_COOKIE_PATH, "max_age": settings.oauth_state_minutes * 60}
    response.set_cookie(OAUTH_STATE_COOKIE, state_value, **cookie_options)
    response.set_cookie(OAUTH_VERIFIER_COOKIE, verifier, **cookie_options)
    return response


@router.get("/auth/oauth/{provider}/callback", include_in_schema=False)
async def oauth_callback(
    provider: str,
    session: DbSession,
    settings: SettingsDep,
    code: Annotated[str | None, Query()] = None,
    state_value: Annotated[str | None, Query(alias="state")] = None,
    provider_error: Annotated[str | None, Query(alias="error")] = None,
    state_cookie: Annotated[str | None, Cookie(alias=OAUTH_STATE_COOKIE)] = None,
    verifier: Annotated[str | None, Cookie(alias=OAUTH_VERIFIER_COOKIE)] = None,
) -> RedirectResponse:
    login_url = f"{settings.website_url.rstrip('/')}/login/"
    if provider_error or not code or not state_value or not verifier:
        return RedirectResponse(f"{login_url}?oauth_error=cancelled", status_code=302)
    validate_oauth_state(state_value, state_cookie, provider, settings)
    identity = await fetch_provider_identity(provider, code, verifier, settings)
    user, registration = authenticate_oauth_identity(session, identity, settings)
    tokens = issue_session_tokens(session, user, settings)
    if registration is None:
        destination = f"{settings.website_url.rstrip('/')}/account/"
    else:
        destination = f"{settings.website_url.rstrip('/')}/oauth-complete/#api_key={registration.api_key}"
    response = RedirectResponse(destination, status_code=302)
    response.delete_cookie(OAUTH_STATE_COOKIE, path=OAUTH_COOKIE_PATH, secure=settings.auth_cookie_secure, samesite="lax")
    response.delete_cookie(OAUTH_VERIFIER_COOKIE, path=OAUTH_COOKIE_PATH, secure=settings.auth_cookie_secure, samesite="lax")
    set_auth_cookies(response, tokens, settings)
    return response


@router.post("/auth/register", response_model=RegistrationVerified, status_code=201)
def register_account(
    payload: RegistrationCreate, request: Request, response: Response,
    session: DbSession, settings: SettingsDep,
) -> RegistrationVerified:
    ensure_allowed_origin(request, settings)
    user, registration = create_registration(session, payload, settings)
    tokens = issue_session_tokens(session, user, settings)
    set_auth_cookies(response, tokens, settings)
    return registration


@router.post("/auth/login", response_model=TokenResponse)
def login(
    payload: LoginRequest, request: Request, response: Response,
    session: DbSession, settings: SettingsDep,
) -> TokenResponse:
    ensure_allowed_origin(request, settings)
    user = authenticate_credentials(session, payload.email, payload.password)
    tokens = issue_session_tokens(session, user, settings)
    set_auth_cookies(response, tokens, settings)
    return token_response(tokens, settings)


@router.post("/auth/refresh", response_model=TokenResponse)
def refresh(
    payload: RefreshRequest, request: Request, response: Response,
    session: DbSession, settings: SettingsDep,
    refresh_cookie: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
) -> TokenResponse:
    ensure_allowed_origin(request, settings)
    raw_token = payload.refresh_token or refresh_cookie
    if not raw_token:
        raise HTTPException(
            status_code=401,
            detail={"code": "refresh_token_required", "message": "A refresh token is required"},
        )
    _, tokens = rotate_refresh_token(session, raw_token, settings)
    set_auth_cookies(response, tokens, settings)
    return token_response(tokens, settings)


@router.post("/auth/logout", status_code=204)
def logout(
    request: Request, response: Response, session: DbSession, settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer)],
    access_cookie: Annotated[str | None, Cookie(alias=ACCESS_COOKIE)] = None,
    refresh_cookie: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
) -> Response:
    ensure_allowed_origin(request, settings)
    raw_token = credentials.credentials if credentials else access_cookie
    token_type = "access"
    if raw_token is None:
        raw_token = refresh_cookie
        token_type = "refresh"
    if raw_token:
        try:
            payload = decode_token(raw_token, token_type, settings)
            auth_session = session.get(AuthSession, payload["sid"])
            if auth_session is not None:
                revoke_session(session, auth_session)
        except HTTPException:
            pass
    clear_auth_cookies(response, settings)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/account", response_model=UserResponse)
def account(context: CurrentUser, session: DbSession) -> UserResponse:
    client = account_client(session, context.user)
    return UserResponse(
        id=context.user.id,
        email=context.user.email,
        display_name=context.user.display_name,
        organization=context.user.organization,
        plan=client.plan,
        created_at=context.user.created_at,
        is_admin=context.user.is_admin,
    )


@router.get("/account/api-keys", response_model=list[ApiKeySummary])
def list_api_keys(context: CurrentUser, session: DbSession) -> list[ApiKeySummary]:
    keys = session.scalars(
        select(ApiKey)
        .where(ApiKey.owner_user_id == context.user.id)
        .order_by(ApiKey.created_at.desc())
    ).all()
    return [ApiKeySummary.model_validate(key, from_attributes=True) for key in keys]


@router.post("/account/api-keys", response_model=ApiKeyCreated, status_code=201)
def create_api_key(
    payload: ApiKeyCreate, request: Request, context: CurrentUser,
    session: DbSession, settings: SettingsDep,
) -> ApiKeyCreated:
    ensure_allowed_origin(request, settings)
    client = account_client(session, context.user)
    key, raw_key = issue_api_key(
        session, client, settings, name=payload.name, commit=False
    )
    key.owner_user_id = context.user.id
    session.commit()
    session.refresh(key)
    return ApiKeyCreated(
        id=key.id, name=key.name, key_prefix=key.key_prefix, active=key.active,
        created_at=key.created_at, last_used_at=key.last_used_at, api_key=raw_key,
    )


@router.delete("/account/api-keys/{key_id}", status_code=204)
def revoke_api_key(
    key_id: str, request: Request, response: Response, context: CurrentUser,
    session: DbSession, settings: SettingsDep,
) -> Response:
    ensure_allowed_origin(request, settings)
    key = session.scalar(
        select(ApiKey).where(
            ApiKey.id == key_id, ApiKey.owner_user_id == context.user.id
        )
    )
    if key is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "api_key_not_found", "message": "The API key was not found"},
        )
    key.active = False
    session.commit()
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post(
    "/account/api-keys/{key_id}/regenerate",
    response_model=ApiKeyCreated,
    status_code=201,
)
def regenerate_api_key(
    key_id: str, request: Request, context: CurrentUser,
    session: DbSession, settings: SettingsDep,
) -> ApiKeyCreated:
    """Revoke an owned key and return its replacement plaintext exactly once."""
    ensure_allowed_origin(request, settings)
    old_key = session.scalar(
        select(ApiKey)
        .where(ApiKey.id == key_id, ApiKey.owner_user_id == context.user.id)
        .with_for_update()
    )
    if old_key is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "api_key_not_found", "message": "The API key was not found"},
        )
    client = account_client(session, context.user)
    old_key.active = False
    key, raw_key = issue_api_key(
        session, client, settings, name=old_key.name or "default", commit=False
    )
    key.owner_user_id = context.user.id
    session.commit()
    session.refresh(key)
    return ApiKeyCreated(
        id=key.id,
        name=key.name,
        key_prefix=key.key_prefix,
        active=key.active,
        created_at=key.created_at,
        last_used_at=key.last_used_at,
        api_key=raw_key,
    )


@router.get("/account/usage", response_model=UsageSummary)
def usage(context: CurrentUser, session: DbSession, settings: SettingsDep) -> UsageSummary:
    client = account_client(session, context.user)
    period = current_period()
    request_count, lookup_count = session.execute(
        select(
            func.coalesce(func.sum(MonthlyUsage.request_count), 0),
            func.coalesce(func.sum(MonthlyUsage.lookup_count), 0),
        )
        .join(ApiKey, ApiKey.id == MonthlyUsage.api_key_id)
        .where(ApiKey.owner_user_id == context.user.id, MonthlyUsage.period_start == period)
    ).one()
    limit = settings.plan_limits[client.plan.upper()].monthly_lookups
    return UsageSummary(
        period_start=period, request_count=request_count, lookup_count=lookup_count,
        monthly_limit=limit, remaining=max(0, limit - lookup_count),
    )


@router.get("/account/subscription", response_model=SubscriptionSummary)
def subscription(context: CurrentUser, session: DbSession) -> SubscriptionSummary:
    record = session.scalar(
        select(Subscription)
        .where(Subscription.user_id == context.user.id)
        .order_by(Subscription.created_at.desc())
    )
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "subscription_not_found", "message": "No subscription was found"},
        )
    return SubscriptionSummary(
        plan=record.plan_code, status=record.status,
        price_cents=record.plan.price_cents, currency=record.plan.currency,
        current_period_end=record.current_period_end,
        cancel_at_period_end=record.cancel_at_period_end,
    )
