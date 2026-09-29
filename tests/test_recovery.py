from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.recovery import enqueue, claim, run_one
from app.models import ProductRecovery

GTIN = "03017620422003"
NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


def test_api_miss_enqueues_when_enabled(client, session_factory, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "recovery_enabled", True)
    monkeypatch.setattr(get_settings(), "fallback_lookups_enabled", False)
    assert client.get("/v1/products/4006381333931").status_code == 404
    with session_factory() as session:
        assert session.get(ProductRecovery, "04006381333931").status == "pending"


def test_worker_persists_eligible_candidate_only(session_factory):
    from tests.test_fallbacks import Provider, candidate, settings
    from app.fallbacks import FallbackResolver
    from app.models import Product
    transient = Provider("TEMP", candidate(persist=False))
    durable = Provider("TEST", candidate())
    def resolver(session, config):
        return FallbackResolver(session, config, providers=[transient, durable])
    with session_factory() as session:
        enqueue(session, ["4006381333931"], NOW)
        session.commit()
    run_one(session_factory, settings(), resolver_factory=resolver, now=NOW)
    with session_factory() as session:
        assert session.get(ProductRecovery, "04006381333931").status == "recovered"
        assert session.get(Product, "04006381333931").source == "TEST"


def test_deduplicate_and_count_requests(session_factory):
    with session_factory() as session:
        enqueue(session, [GTIN, "3017620422003"], NOW)
        enqueue(session, [GTIN], NOW)
        session.commit()
        assert session.get(ProductRecovery, GTIN).request_count == 2


def test_claim_lease_and_reclaim(session_factory):
    with session_factory() as session:
        enqueue(session, [GTIN], NOW)
        session.commit()
    first = claim(session_factory, NOW)
    assert first
    assert claim(session_factory, NOW) is None
    second = claim(session_factory, NOW + timedelta(minutes=16))
    assert second and second[1] != first[1]


def test_already_imported_product(session_factory):
    with session_factory() as session:
        enqueue(session, [GTIN], NOW)
        session.commit()
    assert run_one(session_factory, SimpleNamespace(), now=NOW)
    with session_factory() as session:
        assert session.get(ProductRecovery, GTIN).status == "recovered"


def test_miss_retries_then_stops(session_factory):
    missing = "00000000000000"
    class Resolver:
        def __init__(self, *args): pass
        def resolve(self, gtin, *, persistent_only):
            assert persistent_only
            return SimpleNamespace(product=None, provider_found=None)
    with session_factory() as session:
        enqueue(session, [missing], NOW)
        session.commit()
    for attempt in range(5):
        assert run_one(session_factory, SimpleNamespace(), resolver_factory=Resolver,
                       now=NOW + timedelta(days=20 * attempt))
    with session_factory() as session:
        row = session.get(ProductRecovery, missing)
        assert row.status == "unresolved"
        assert row.attempts == 5
    assert claim(session_factory, NOW + timedelta(days=120)) is None
