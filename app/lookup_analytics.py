import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.auth import AuthContext
from app.models import LookupAnalytics


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LookupOutcome:
    canonical_gtin: str
    barcode_type: str
    found: bool


def record_lookup_outcomes_safely(
    source_session: Session,
    context: AuthContext,
    outcomes: list[LookupOutcome],
    *,
    endpoint_type: str,
    retention_days: int,
    now: datetime | None = None,
) -> None:
    """Persist valid lookup outcomes independently; telemetry must never fail an API call."""
    if not outcomes:
        return
    timestamp = now or datetime.now(timezone.utc)
    request_id = str(uuid4())
    bind = source_session.get_bind()
    try:
        with Session(bind=bind, expire_on_commit=False) as analytics_session:
            analytics_session.execute(
                delete(LookupAnalytics).where(
                    LookupAnalytics.occurred_at < timestamp - timedelta(days=retention_days)
                )
            )
            analytics_session.add_all(
                LookupAnalytics(
                    request_id=request_id,
                    api_key_id=context.api_key.id,
                    owner_user_id=context.api_key.owner_user_id,
                    canonical_gtin=outcome.canonical_gtin,
                    barcode_type=outcome.barcode_type,
                    endpoint_type=endpoint_type,
                    found=outcome.found,
                    plan_code=context.client.plan,
                    occurred_at=timestamp,
                )
                for outcome in outcomes
            )
            analytics_session.commit()
    except Exception:
        logger.exception("Lookup analytics write failed")
