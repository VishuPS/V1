from datetime import datetime, timedelta, timezone
from app.provider_cooldowns import reserve, postpone
from app.fallbacks import FallbackResolver, ProviderResult, _retry_after
from app.recovery import enqueue, run_one
from app.models import ProductRecovery
from tests.test_fallbacks import Provider, settings

def test_shared_reservation_and_cooldown(session_factory):
    now = datetime.now(timezone.utc)
    with session_factory() as a, session_factory() as b:
        assert reserve(a, "TEST", 10, now) is None
        assert reserve(b, "TEST", 10, now)[1] == "shared_rate_limit"
        postpone(a, "TEST", 3600, "access_denied", now)
        postpone(b, "TEST", 60, "timeout", now)
        assert reserve(b, "TEST", 10, now + timedelta(seconds=11))[1] == "access_denied"
        assert reserve(a, "TEST", 10, now + timedelta(seconds=3601)) is None

def test_global_denial_skips_other_products(session_factory):
    provider = Provider("TEST", ProviderResult("unavailable", retry_after_seconds=3600, detail="access_denied"))
    with session_factory() as a:
        first = FallbackResolver(a, settings(), providers=[provider]).resolve("04006381333931")
    with session_factory() as b:
        second = FallbackResolver(b, settings(), providers=[provider]).resolve("00000000000000")
    assert first.infrastructure_failure and second.infrastructure_failure
    assert second.providers_attempted == []
    assert provider.calls == 1

def test_cached_unavailability_preserves_classification(session_factory):
    provider = Provider("TEST", ProviderResult("unavailable", retry_after_seconds=3600))
    with session_factory() as session:
        resolver = FallbackResolver(session, settings(), providers=[provider])
        resolver.resolve("04006381333931")
        result = resolver.resolve("04006381333931")
        assert result.infrastructure_failure and result.retry_after_seconds > 0
        assert provider.calls == 1

def test_infrastructure_retries_do_not_consume_misses_and_are_bounded(session_factory):
    from types import SimpleNamespace
    now = datetime.now(timezone.utc)
    gtin = "00000000000000"
    class Resolver:
        def __init__(self, *args): pass
        def resolve(self, *args, **kwargs):
            return SimpleNamespace(product=None, infrastructure_failure=True, retry_after_seconds=3600)
    with session_factory() as session:
        enqueue(session, [gtin], now)
        session.commit()
    for index in range(20):
        assert run_one(session_factory, settings(), resolver_factory=Resolver, now=now+timedelta(days=index*2))
    with session_factory() as session:
        row = session.get(ProductRecovery, gtin)
        assert row.attempts == 0
        assert row.infrastructure_attempts == 20
        assert row.status == "unresolved"
        assert row.detail == "infrastructure_retry_limit"

def test_retry_after_case_and_http_date():
    from email.utils import format_datetime
    assert _retry_after({"retry-after": "123"}) == 123
    future = datetime.now(timezone.utc) + timedelta(seconds=300)
    assert 298 <= _retry_after({"Retry-After": format_datetime(future)}) <= 300
    assert _retry_after({"Retry-After": "invalid"}) == 60


def test_concurrent_reservations_allow_one_request(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import ProviderCooldown
    engine = create_engine("sqlite:///" + str(tmp_path / "shared.db"))
    ProviderCooldown.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    barrier = Barrier(4)
    now = datetime.now(timezone.utc)
    def compete(_):
        with factory() as session:
            barrier.wait()
            return reserve(session, "TEST", 10, now) is None
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(compete, range(4))) == 1
    engine.dispose()

def test_expired_lease_uses_infrastructure_budget(session_factory):
    from app.recovery import claim
    now = datetime.now(timezone.utc)
    gtin = "00000000000000"
    with session_factory() as session:
        enqueue(session, [gtin], now)
        session.commit()
    assert claim(session_factory, now)
    assert claim(session_factory, now + timedelta(minutes=16))
    with session_factory() as session:
        row = session.get(ProductRecovery, gtin)
        assert row.attempts == 0
        assert row.infrastructure_attempts == 1


def test_eandb_empty_balance_is_classified():
    from tests.test_fallbacks import Transport, response
    from app.fallbacks import EANDBFallback
    provider = EANDBFallback(settings(eandb_api_key="test"), Transport(
        response({"error": {"code": 403, "description": "Your account balance is empty"}}, status=403)))
    result = provider.lookup("04006381333931")
    assert result.status == "unavailable"
    assert result.detail == "account_balance_empty"
    assert result.retry_after_seconds == 3600
