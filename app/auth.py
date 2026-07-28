import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, joinedload

from app.config import PlanLimit, Settings, get_settings
from app.db import get_db
from app.models import ApiClient, ApiKey, MonthlyUsage, new_uuid


api_key_header = APIKeyHeader(
    name="X-API-Key",
    scheme_name="ApiKeyAuth",
    description="Developer API key issued by the API-key administration CLI.",
    auto_error=False,
)


def auth_error(code: str, message: str, *, headers: dict[str, str] | None = None) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": code, "message": message},
        headers=headers,
    )


def generate_api_key() -> tuple[str, str]:
    # Keep the public lookup component delimiter-safe. The secret remains
    # high-entropy URL-safe material and may legitimately contain underscores.
    public_id = secrets.token_hex(12)
    raw_key = f"gpa_{public_id}_{secrets.token_urlsafe(32)}"
    return raw_key, f"gpa_{public_id}"


def hash_api_key(raw_key: str, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"), raw_key.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def issue_api_key(
    session: Session,
    client: ApiClient,
    settings: Settings,
    *,
    name: str | None = None,
    expires_at: datetime | None = None,
) -> tuple[ApiKey, str]:
    raw_key, key_prefix = generate_api_key()
    record = ApiKey(
        client=client,
        name=name,
        key_prefix=key_prefix,
        key_hash=hash_api_key(raw_key, settings.api_key_hash_secret),
        expires_at=expires_at,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record, raw_key


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    limit: int
    remaining: int
    reset_epoch: int


class InMemoryRateLimiter:
    """Single-process fixed-window boundary; replace with Redis when distributed."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(
        self, key_id: str, limit: int, *, now: float | None = None
    ) -> RateLimitResult:
        current = time.time() if now is None else now
        cutoff = current - 60
        with self._lock:
            events = self._events[key_id]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                reset = int(events[0] + 60)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={
                        "code": "rate_limit_exceeded",
                        "message": "The per-minute request rate limit was exceeded",
                    },
                    headers={
                        "X-RateLimit-Limit": str(limit),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(reset),
                        "Retry-After": str(max(1, reset - int(current))),
                    },
                )
            events.append(current)
            reset = int(events[0] + 60)
            return RateLimitResult(
                limit=limit,
                remaining=max(0, limit - len(events)),
                reset_epoch=reset,
            )

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


rate_limiter = InMemoryRateLimiter()


@dataclass(frozen=True, slots=True)
class AuthContext:
    api_key: ApiKey
    client: ApiClient
    plan: PlanLimit
    rate_limit: RateLimitResult


def _extract_prefix(raw_key: str) -> str | None:
    parts = raw_key.split("_", 2)
    if len(parts) != 3 or parts[0] != "gpa" or not parts[1] or not parts[2]:
        return None
    return f"gpa_{parts[1]}"


def _aware_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def authenticate_api_key(
    raw_key: Annotated[str | None, Security(api_key_header)],
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthContext:
    if not raw_key:
        raise auth_error("missing_api_key", "X-API-Key header is required")
    key_prefix = _extract_prefix(raw_key)
    if key_prefix is None:
        raise auth_error("invalid_api_key", "The API key is invalid")
    record = session.scalar(
        select(ApiKey)
        .options(joinedload(ApiKey.client))
        .where(ApiKey.key_prefix == key_prefix)
    )
    candidate_hash = hash_api_key(raw_key, settings.api_key_hash_secret)
    stored_hash = record.key_hash if record is not None else "0" * 64
    valid_hash = hmac.compare_digest(candidate_hash, stored_hash)
    if record is None or not valid_hash:
        raise auth_error("invalid_api_key", "The API key is invalid")
    if not record.active or not record.client.active:
        raise auth_error("revoked_api_key", "The API key is revoked")
    if record.expires_at and _aware_utc(record.expires_at) <= datetime.now(timezone.utc):
        raise auth_error("expired_api_key", "The API key has expired")
    plan = settings.plan_limits.get(record.client.plan.upper())
    if plan is None:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "invalid_plan_configuration",
                "message": "The API key references an unavailable plan",
            },
        )
    rate = rate_limiter.check(record.id, plan.requests_per_minute)
    return AuthContext(api_key=record, client=record.client, plan=plan, rate_limit=rate)


def current_period(now: datetime | None = None) -> date:
    value = now or datetime.now(timezone.utc)
    return date(value.year, value.month, 1)


@dataclass(frozen=True, slots=True)
class UsageResult:
    quota_limit: int
    quota_remaining: int
    period_start: date
    request_count: int
    lookup_count: int


def consume_usage(
    session: Session,
    context: AuthContext,
    lookup_units: int,
    *,
    now: datetime | None = None,
) -> UsageResult:
    if lookup_units < 1:
        raise ValueError("lookup_units must be at least 1")
    timestamp = now or datetime.now(timezone.utc)
    period = current_period(timestamp)
    quota = context.plan.monthly_lookups
    if lookup_units > quota:
        raise _quota_error(quota, 0, period)

    values = {
        "id": new_uuid(),
        "api_key_id": context.api_key.id,
        "period_start": period,
        "request_count": 1,
        "lookup_count": lookup_units,
        "last_request_at": timestamp,
        "updated_at": timestamp,
    }
    dialect_name = session.bind.dialect.name if session.bind is not None else ""
    if dialect_name == "postgresql":
        insert_statement = postgresql_insert(MonthlyUsage).values(**values)
    elif dialect_name == "sqlite":
        insert_statement = sqlite_insert(MonthlyUsage).values(**values)
    else:
        raise RuntimeError(f"Unsupported metering database dialect: {dialect_name}")
    statement = insert_statement.on_conflict_do_update(
        index_elements=[MonthlyUsage.api_key_id, MonthlyUsage.period_start],
        set_={
            "request_count": MonthlyUsage.request_count + 1,
            "lookup_count": MonthlyUsage.lookup_count + lookup_units,
            "last_request_at": timestamp,
            "updated_at": timestamp,
        },
        where=MonthlyUsage.lookup_count + lookup_units <= quota,
    ).returning(MonthlyUsage.request_count, MonthlyUsage.lookup_count)
    row = session.execute(statement).one_or_none()
    if row is None:
        current = session.scalar(
            select(MonthlyUsage.lookup_count).where(
                MonthlyUsage.api_key_id == context.api_key.id,
                MonthlyUsage.period_start == period,
            )
        ) or 0
        raise _quota_error(quota, max(0, quota - current), period)
    session.execute(
        update(ApiKey)
        .where(ApiKey.id == context.api_key.id)
        .values(last_used_at=timestamp)
    )
    session.commit()
    return UsageResult(
        quota_limit=quota,
        quota_remaining=max(0, quota - row.lookup_count),
        period_start=period,
        request_count=row.request_count,
        lookup_count=row.lookup_count,
    )


def _quota_error(quota: int, remaining: int, period: date) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "code": "monthly_quota_exceeded",
            "message": "The monthly barcode lookup quota was exceeded",
        },
        headers={
            "X-Monthly-Quota-Limit": str(quota),
            "X-Monthly-Quota-Remaining": str(remaining),
            "X-Monthly-Quota-Period": period.isoformat(),
        },
    )


def usage_headers(context: AuthContext, usage: UsageResult) -> dict[str, str]:
    return {
        "X-RateLimit-Limit": str(context.rate_limit.limit),
        "X-RateLimit-Remaining": str(context.rate_limit.remaining),
        "X-RateLimit-Reset": str(context.rate_limit.reset_epoch),
        "X-Monthly-Quota-Limit": str(usage.quota_limit),
        "X-Monthly-Quota-Remaining": str(usage.quota_remaining),
        "X-Monthly-Quota-Period": usage.period_start.isoformat(),
    }
