"""Durable recovery with atomic claims; network work never holds a queue lock."""
import argparse
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import case, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.barcodes import parse_barcode
from app.models import ProductRecovery
from app.repositories import ProductRepository
from app.fallbacks import FallbackResolver


def enqueue(session, barcodes, now=None):
    now = now or datetime.now(timezone.utc)
    insert = pg_insert if session.bind.dialect.name == "postgresql" else sqlite_insert
    for gtin in sorted({parse_barcode(code).gtin14 for code in barcodes}):
        stmt = insert(ProductRecovery).values(
            canonical_gtin=gtin, status="pending", request_count=1, attempts=0,
            first_seen=now, last_seen=now, next_attempt=now,
        )
        session.execute(stmt.on_conflict_do_update(
            index_elements=["canonical_gtin"],
            set_={"request_count": ProductRecovery.request_count + 1, "last_seen": now},
        ))


def claim(factory, now=None, max_attempts=5):
    now = now or datetime.now(timezone.utc)
    with factory() as session:
        # A crashed worker is infrastructure failure, not evidence of a missing product.
        session.execute(update(ProductRecovery).where(
            ProductRecovery.status == "running", ProductRecovery.next_attempt <= now,
        ).values(
            infrastructure_attempts=ProductRecovery.infrastructure_attempts + 1,
            status=case((ProductRecovery.infrastructure_attempts >= 19, "unresolved"), else_="retry"),
            detail="worker_lease_expired", lease_token=None,
        ))
        eligible = (
            ProductRecovery.status.in_(["pending", "retry", "running"]),
            ProductRecovery.next_attempt <= now, ProductRecovery.attempts < max_attempts, ProductRecovery.infrastructure_attempts < 20,
        )
        gtin = session.scalar(select(ProductRecovery.canonical_gtin).where(*eligible)
                              .order_by(ProductRecovery.request_count.desc(), ProductRecovery.first_seen).limit(1))
        if gtin is None:
            session.commit()
            return None
        token = str(uuid4())
        result = session.execute(update(ProductRecovery).where(
            ProductRecovery.canonical_gtin == gtin, *eligible,
        ).values(status="running", lease_token=token,
                 next_attempt=now + timedelta(minutes=15)))
        session.commit()
        return (gtin, token) if result.rowcount == 1 else None


def run_one(factory, settings, *, resolver_factory=FallbackResolver, now=None):
    now = now or datetime.now(timezone.utc)
    job = claim(factory, now)
    if job is None:
        return False
    gtin, token = job
    recovered, provider, detail = False, None, "not_found"
    infrastructure_failure, retry_after = False, 0
    try:
        with factory() as session:
            product = ProductRepository(session).find_by_barcode(parse_barcode(gtin))
            if product is not None:
                recovered, provider = True, product.source
            else:
                result = resolver_factory(session, settings).resolve(gtin, persistent_only=True)
                recovered = result.product is not None
                provider = result.provider_found if recovered else None
                infrastructure_failure = getattr(result, "infrastructure_failure", False)
                retry_after = getattr(result, "retry_after_seconds", 0)
    except Exception as exc:
        detail = type(exc).__name__
        infrastructure_failure = True
    with factory() as session:
        row = session.get(ProductRecovery, gtin)
        if row.lease_token != token:
            return True
        if infrastructure_failure and not recovered:
            row.infrastructure_attempts += 1
            detail = "provider_unavailable" if detail == "not_found" else detail
            if row.infrastructure_attempts >= 20:
                detail = "infrastructure_retry_limit"
        if not recovered and not infrastructure_failure:
            row.attempts += 1
        row.status = "recovered" if recovered else "unresolved" if row.attempts >= 5 or row.infrastructure_attempts >= 20 else "retry"
        row.provider, row.detail, row.lease_token = provider, None if recovered else detail, None
        # A full-day initial cooldown respects existing provider negative caches.
        if infrastructure_failure and not recovered:
            row.next_attempt = now + timedelta(seconds=max(retry_after, min(300 * 2 ** min(row.infrastructure_attempts - 1, 8), 86400)))
        else:
            row.next_attempt = now + timedelta(days=min(2 ** max(0, row.attempts - 1), 14))
        session.commit()
    return True


def main():
    from app.config import get_settings
    from app.db import SessionLocal
    parser = argparse.ArgumentParser(description="Process or inspect missing-product recovery")
    parser.add_argument("--limit", type=int, default=20, help="Maximum jobs for this invocation")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--watch", action="store_true", help="Repeat bounded batches every five minutes")
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error("limit must be between 1 and 1000")
    if args.status:
        with SessionLocal() as session:
            rows = session.scalars(select(ProductRecovery).order_by(ProductRecovery.last_seen.desc()).limit(args.limit))
            print(json.dumps([{"gtin": r.canonical_gtin, "status": r.status, "requests": r.request_count,
                               "attempts": r.attempts, "provider": r.provider, "detail": r.detail,
                               "next_attempt": r.next_attempt.isoformat()} for r in rows], indent=2))
        return
    settings = get_settings()
    if not settings.recovery_enabled:
        parser.error("Set RECOVERY_ENABLED=true after applying migrations")
    while True:
        completed = 0
        try:
            for _ in range(args.limit):
                if not run_one(SessionLocal, settings):
                    break
                completed += 1
        except Exception:
            logging.exception("Recovery batch failed; durable jobs will retry")
            if not args.watch:
                raise
        print(f"Processed {completed} recovery jobs", flush=True)
        if not args.watch:
            break
        time.sleep(300)


if __name__ == "__main__":
    main()
