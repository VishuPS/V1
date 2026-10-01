"""Atomic shared reservations. Commit before making any network request."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from app.models import ProviderCooldown

def reserve(session, provider, interval=0, now=None):
    now = now or datetime.now(timezone.utc)
    insert = pg_insert if session.bind.dialect.name == "postgresql" else sqlite_insert
    session.execute(insert(ProviderCooldown).values(provider=provider, next_allowed_at=now)
                    .on_conflict_do_nothing(index_elements=["provider"]))
    claimed = session.execute(update(ProviderCooldown).where(
        ProviderCooldown.provider == provider, ProviderCooldown.next_allowed_at <= now,
    ).values(next_allowed_at=now + timedelta(seconds=interval), detail="shared_rate_limit"))
    session.commit()
    if claimed.rowcount:
        return None
    row = session.get(ProviderCooldown, provider, populate_existing=True)
    until = row.next_allowed_at
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until, row.detail

def postpone(session, provider, seconds, detail, now=None):
    now = now or datetime.now(timezone.utc)
    until = now + timedelta(seconds=seconds)
    # Never shorten a cooldown imposed by another request.
    session.execute(update(ProviderCooldown).where(
        ProviderCooldown.provider == provider, ProviderCooldown.next_allowed_at < until,
    ).values(next_allowed_at=until, detail=detail))
    session.commit()
