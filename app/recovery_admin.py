"""Read-only admin visibility into the durable recovery queue."""
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.admin_auth import AdminContext
from app.config import get_settings
from app.db import get_db
from app.models import FallbackProviderState, ProductRecovery

router = APIRouter(prefix="/v1/admin/recovery", tags=["admin"])
QueueStatus = Literal["pending", "running", "retry", "recovered", "unresolved"]


class ProviderCheck(BaseModel):
    provider: str
    status: str
    detail: str | None
    checked_at: datetime
    expires_at: datetime


class RecoveryItem(BaseModel):
    canonical_gtin: str
    status: str
    request_count: int
    attempts: int
    first_seen: datetime
    last_seen: datetime
    next_attempt: datetime | None
    provider: str | None
    detail: str | None
    reason: str
    checks: list[ProviderCheck]


class ProviderSummary(BaseModel):
    provider: str
    status: str
    detail: str | None
    products: int


class RecoveryDashboard(BaseModel):
    generated_at: datetime
    capture_enabled: bool
    worker_configured: bool
    counts: dict[str, int]
    provider_summary: list[ProviderSummary]
    total: int
    limit: int
    offset: int
    items: list[RecoveryItem]


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def reason(row, checks):
    if row.status == "recovered":
        return "Available in the local database"
    if row.status == "pending":
        return "Waiting for first attempt"
    if row.status == "running":
        return "Worker lease active; expired leases are retried"
    if row.detail and row.detail != "not_found":
        return f"Worker error: {row.detail}"
    if any(c.detail == "access_denied" for c in checks):
        return "Provider access denied; check provider account or credentials"
    if any(c.status in {"unavailable", "error"} for c in checks):
        return "Provider unavailable; may be throttling or an upstream error"
    if any(c.status == "invalid" for c in checks):
        return "Source data failed validation"
    if any(c.status == "found" for c in checks):
        return "Source match was not stored; check validation and storage eligibility"
    return "No match in latest source checks" if checks else "No provider checks recorded"


def recovery_dashboard(session, *, status=None, limit=25, offset=0):
    counts = dict.fromkeys(["pending", "running", "retry", "recovered", "unresolved"], 0)
    counts.update(dict(session.execute(select(ProductRecovery.status, func.count())
                                      .group_by(ProductRecovery.status)).all()))
    query = select(ProductRecovery)
    if status:
        query = query.where(ProductRecovery.status == status)
    rows = session.scalars(query.order_by(ProductRecovery.last_seen.desc(), ProductRecovery.canonical_gtin)
                           .offset(offset).limit(limit)).all()
    checks = {}
    if rows:
        for check in session.scalars(select(FallbackProviderState).where(
            FallbackProviderState.canonical_gtin.in_([r.canonical_gtin for r in rows])
        ).order_by(FallbackProviderState.provider)):
            checks.setdefault(check.canonical_gtin, []).append(ProviderCheck(
                provider=check.provider, status=check.status, detail=check.detail,
                checked_at=aware(check.checked_at), expires_at=aware(check.expires_at),
            ))
    provider_rows = session.execute(select(
        FallbackProviderState.provider, FallbackProviderState.status,
        FallbackProviderState.detail, func.count(),
    ).join(ProductRecovery, ProductRecovery.canonical_gtin == FallbackProviderState.canonical_gtin)
      .where(ProductRecovery.status != "recovered")
      .group_by(FallbackProviderState.provider, FallbackProviderState.status, FallbackProviderState.detail)
      .order_by(FallbackProviderState.provider, FallbackProviderState.status)).all()
    settings = get_settings()
    return RecoveryDashboard(
        generated_at=datetime.now(timezone.utc), capture_enabled=settings.recovery_enabled,
        worker_configured=settings.recovery_worker_enabled, counts=counts,
        provider_summary=[ProviderSummary(provider=p, status=s, detail=d, products=n) for p,s,d,n in provider_rows],
        total=counts.get(status, 0) if status else sum(counts.values()), limit=limit, offset=offset,
        items=[RecoveryItem(
            canonical_gtin=r.canonical_gtin, status=r.status, request_count=r.request_count,
            attempts=r.attempts, first_seen=aware(r.first_seen), last_seen=aware(r.last_seen),
            next_attempt=aware(r.next_attempt) if r.status in {"pending", "retry", "running"} else None,
            provider=r.provider, detail=r.detail, reason=reason(r, checks.get(r.canonical_gtin, [])),
            checks=checks.get(r.canonical_gtin, []),
        ) for r in rows],
    )


@router.get("", response_model=RecoveryDashboard)
def dashboard(
    _: AdminContext, response: Response,
    session: Session = Depends(get_db),
    status: QueueStatus | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100000),
):
    response.headers["Cache-Control"] = "no-store"
    return recovery_dashboard(session, status=status, limit=limit, offset=offset)
