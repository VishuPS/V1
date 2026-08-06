from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.auth import (
    AuthContext,
    RateLimitResult,
    consume_usage,
    current_period,
)
from app.config import PlanLimit, Settings, get_settings
from app.db import Base, get_db
from app.main import app
from app.models import ApiClient, ApiKey, MonthlyUsage, Subscription, SubscriptionPlan, User


def key_prefix(raw_key: str) -> str:
    return "_".join(raw_key.split("_", 2)[:2])


def test_health_is_public(unauthenticated_client: TestClient) -> None:
    response = unauthenticated_client.get("/health")
    assert response.status_code == 200


def test_missing_and_invalid_api_key(unauthenticated_client: TestClient) -> None:
    missing = unauthenticated_client.get("/v1/products/3017620422003")
    invalid = unauthenticated_client.get(
        "/v1/products/3017620422003",
        headers={"X-API-Key": "gpa_unknown_invalid"},
    )
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "missing_api_key"
    assert invalid.status_code == 401
    assert invalid.json()["error"]["code"] == "invalid_api_key"


def test_revoked_and_expired_keys(
    unauthenticated_client: TestClient,
    session_factory: sessionmaker[Session],
    api_key: str,
) -> None:
    with session_factory() as session:
        record = session.scalar(
            select(ApiKey).where(ApiKey.key_prefix == key_prefix(api_key))
        )
        assert record is not None
        record.active = False
        session.commit()
    revoked = unauthenticated_client.get(
        "/v1/products/3017620422003", headers={"X-API-Key": api_key}
    )
    assert revoked.status_code == 401
    assert revoked.json()["error"]["code"] == "revoked_api_key"

    with session_factory() as session:
        record = session.scalar(
            select(ApiKey).where(ApiKey.key_prefix == key_prefix(api_key))
        )
        record.active = True
        record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()
    expired = unauthenticated_client.get(
        "/v1/products/3017620422003", headers={"X-API-Key": api_key}
    )
    assert expired.status_code == 401
    assert expired.json()["error"]["code"] == "expired_api_key"


def test_key_is_hashed_and_never_returned_or_logged(
    client: TestClient,
    session_factory: sessionmaker[Session],
    api_key: str,
    caplog,
) -> None:
    with session_factory() as session:
        record = session.scalar(select(ApiKey))
        assert record is not None
        assert record.key_hash != api_key
        assert api_key not in record.key_hash
        assert len(record.key_hash) == 64
    with caplog.at_level("INFO"):
        response = client.get("/v1/products/3017620422003")
    assert response.status_code == 200
    assert api_key not in response.text
    assert api_key not in caplog.text


def limited_settings(*, quota: int = 2, rate: int = 100) -> Settings:
    return Settings(
        _env_file=None,
        plan_limits={
            "FREE": PlanLimit(
                monthly_lookups=quota,
                requests_per_minute=rate,
            )
        },
    )


def test_quota_accounting_and_exhaustion(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    settings = limited_settings(quota=2)
    app.dependency_overrides[get_settings] = lambda: settings
    first = client.get("/v1/products/3017620422003")
    second = client.get("/v1/products/3017620422003")
    exhausted = client.get("/v1/products/3017620422003")
    assert first.status_code == second.status_code == 200
    assert exhausted.status_code == 429
    assert exhausted.json()["error"]["code"] == "usage_limit_reached"
    assert exhausted.headers["X-Monthly-Quota-Remaining"] == "0"
    with session_factory() as session:
        usage = session.scalar(select(MonthlyUsage))
        assert usage is not None
        assert usage.request_count == 2
        assert usage.lookup_count == 2
    app.dependency_overrides.pop(get_settings, None)


def test_batch_charged_as_one_successful_api_call(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    response = client.post(
        "/v1/products/batch",
        json={"barcodes": ["3017620422003", "4006381333931", "123"]},
    )
    assert response.status_code == 200
    assert response.headers["X-Monthly-Quota-Remaining"] == "249"
    with session_factory() as session:
        usage = session.scalar(select(MonthlyUsage))
        assert usage.request_count == 1
        assert usage.lookup_count == 1


def test_monthly_period_rollover(
    client: TestClient, session_factory: sessionmaker[Session], api_key: str
) -> None:
    previous_period = date(2020, 1, 1)
    with session_factory() as session:
        record = session.scalar(
            select(ApiKey).where(ApiKey.key_prefix == key_prefix(api_key))
        )
        session.add(
            MonthlyUsage(
                api_key_id=record.id,
                period_start=previous_period,
                request_count=99,
                lookup_count=499,
            )
        )
        session.commit()
    response = client.get("/v1/products/3017620422003")
    assert response.status_code == 200
    with session_factory() as session:
        periods = set(session.scalars(select(MonthlyUsage.period_start)))
    assert periods == {previous_period, current_period()}


def test_per_key_rate_limit(client: TestClient) -> None:
    settings = limited_settings(quota=100, rate=2)
    app.dependency_overrides[get_settings] = lambda: settings
    assert client.get("/v1/products/3017620422003").status_code == 200
    assert client.get("/v1/products/3017620422003").status_code == 200
    limited = client.get("/v1/products/3017620422003")
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limit_exceeded"
    assert limited.headers["X-RateLimit-Remaining"] == "0"
    assert "Retry-After" in limited.headers
    app.dependency_overrides.pop(get_settings, None)


def test_atomic_concurrent_usage_updates(tmp_path: Path) -> None:
    database = tmp_path / "concurrent.db"
    engine = create_engine(
        f"sqlite:///{database.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        client = ApiClient(identifier="concurrent", plan="FREE")
        key = ApiKey(
            client=client,
            key_prefix="gpa_concurrent",
            key_hash="0" * 64,
        )
        session.add_all([client, key])
        session.commit()
        key_id = key.id
        client_id = client.id

    def consume_once(_: int) -> None:
        with factory() as session:
            key = session.get(ApiKey, key_id)
            client = session.get(ApiClient, client_id)
            context = AuthContext(
                api_key=key,
                client=client,
                plan=PlanLimit(monthly_lookups=1_000, requests_per_minute=1_000),
                rate_limit=RateLimitResult(1_000, 999, 0),
            )
            consume_usage(session, context, 1)

    with ThreadPoolExecutor(max_workers=5) as executor:
        list(executor.map(consume_once, range(20)))
    with factory() as session:
        usage = session.scalar(select(MonthlyUsage))
        assert usage.request_count == 20
        assert usage.lookup_count == 20
        assert session.scalar(select(func.count()).select_from(MonthlyUsage)) == 1


def test_concurrent_account_calls_cannot_exceed_subscription_limit(tmp_path: Path) -> None:
    database = tmp_path / "subscription-concurrent.db"
    engine = create_engine(f"sqlite:///{database.as_posix()}", connect_args={"check_same_thread": False, "timeout": 30})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        plan = SubscriptionPlan(code="FREE", name="Free", monthly_lookups=10, requests_per_minute=1_000, price_cents=0, currency="USD")
        user = User(email="concurrent@example.com", display_name="Concurrent", email_verified_at=datetime.now(timezone.utc))
        session.add_all([plan, user]); session.flush()
        client = ApiClient(identifier="subscription-concurrent", owner_user_id=user.id, plan="FREE")
        session.add(client); session.flush()
        key = ApiKey(client=client, owner_user_id=user.id, key_prefix="gpa_subscription", key_hash="0" * 64)
        subscription = Subscription(user_id=user.id, plan_code="FREE", monthly_call_limit=10)
        session.add_all([key, subscription]); session.commit()
        key_id, client_id, subscription_id = key.id, client.id, subscription.id

    def consume_once(_: int) -> bool:
        with factory() as session:
            context = AuthContext(api_key=session.get(ApiKey, key_id), client=session.get(ApiClient, client_id), plan=PlanLimit(monthly_lookups=10, requests_per_minute=1_000), rate_limit=RateLimitResult(1_000, 999, 0))
            try:
                consume_usage(session, context, 1)
                return True
            except HTTPException as exc:
                assert exc.status_code == 429
                return False

    with ThreadPoolExecutor(max_workers=5) as executor:
        outcomes = list(executor.map(consume_once, range(20)))
    assert sum(outcomes) == 10
    with factory() as session:
        assert session.get(Subscription, subscription_id).monthly_calls_used == 10
