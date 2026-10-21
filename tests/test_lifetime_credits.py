from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from app.models import Subscription
from tests.test_subscription_billing import account
from tests.test_user_auth import login

def test_free_batch_charges_matches_and_rolls_back_oversized_batch(unauthenticated_client, session_factory):
    uid,key=account(session_factory, used=248)
    headers={"X-API-Key":key}
    batch={"barcodes":["3017620422003","012345678905","4006381333931"]}
    response=unauthenticated_client.post("/v1/products/batch",json=batch,headers=headers)
    assert response.status_code==200
    assert response.headers["X-Monthly-Quota-Remaining"]=="0"
    assert response.headers["X-Allowance-Type"]=="lifetime"
    assert unauthenticated_client.post("/v1/products/batch",json=batch,headers=headers).status_code==429
    empty=unauthenticated_client.post("/v1/products/batch",json={"barcodes":["4006381333931"]},headers=headers)
    assert empty.status_code==429
    with session_factory() as s:
        r=s.scalar(select(Subscription).where(Subscription.user_id==uid))
        assert r.free_credits_used==250
        assert r.monthly_calls_used==248

def test_rejected_batch_does_not_partially_charge(unauthenticated_client, session_factory):
    uid,key=account(session_factory, used=249)
    response=unauthenticated_client.post("/v1/products/batch",json={"barcodes":["3017620422003","012345678905"]},headers={"X-API-Key":key})
    assert response.status_code==429
    with session_factory() as s:
        assert s.scalar(select(Subscription.free_credits_used).where(Subscription.user_id==uid))==249

def test_free_dashboard_ignores_historical_monthly_usage(unauthenticated_client, session_factory):
    uid,key=account(session_factory)
    with session_factory() as s:
        r=s.scalar(select(Subscription).where(Subscription.user_id==uid))
        r.monthly_calls_used=250
        r.usage_period_end=datetime.now(timezone.utc)-timedelta(days=3)
        s.commit()
    token=login(unauthenticated_client,"free@example.com").json()["access_token"]
    headers={"Authorization":"Bearer "+token}
    data=unauthenticated_client.get("/v1/account/usage",headers=headers).json()
    assert data["allowance_type"]=="lifetime"
    assert data["remaining"]==250 and data["lookup_count"]==0
    assert data["period_end"] is None
    data=unauthenticated_client.get("/v1/account/subscription",headers=headers).json()
    assert data["usage_period_end"] is None
    assert data["monthly_calls_used"]==0

def test_paid_batch_preserves_per_barcode_accounting(unauthenticated_client, session_factory):
    uid,key=account(session_factory,"STARTER",2000,0)
    response=unauthenticated_client.post("/v1/products/batch",json={"barcodes":["3017620422003","012345678905"]},headers={"X-API-Key":key})
    assert response.status_code==200
    with session_factory() as s:
        r=s.scalar(select(Subscription).where(Subscription.user_id==uid))
        assert r.monthly_calls_used==2 and r.free_credits_used==0


def test_free_credits_shared_between_keys_and_do_not_reset(unauthenticated_client, session_factory):
    from app.auth import issue_api_key
    from app.config import get_settings
    from app.models import ApiClient
    uid, first = account(session_factory, used=249)
    with session_factory() as session:
        sub = session.scalar(select(Subscription).where(Subscription.user_id == uid))
        sub.usage_period_end = datetime.now(timezone.utc) - timedelta(days=60)
        client = session.scalar(select(ApiClient).where(ApiClient.owner_user_id == uid))
        key, second = issue_api_key(session, client, get_settings(), commit=False)
        key.owner_user_id = uid
        session.commit()
    assert unauthenticated_client.get('/v1/products/3017620422003', headers={'X-API-Key':first}).status_code == 200
    response = unauthenticated_client.get('/v1/products/3017620422003', headers={'X-API-Key':second})
    assert response.status_code == 429
    assert response.json()['error']['upgrade_url'] == 'https://barcodenest.com/billing/'
    assert response.json()['error']['allowance_type'] == 'lifetime'


def test_migration_preserves_usage_across_keys_and_months():
    import importlib.util
    from pathlib import Path
    from sqlalchemy import create_engine, text
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    engine = create_engine('sqlite://')
    path = Path(__file__).parents[1] / 'migrations/versions/20261006_0019_lifetime_credits.py'
    spec = importlib.util.spec_from_file_location('lifetime_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as conn:
        for sql in [
            'CREATE TABLE api_clients (id TEXT PRIMARY KEY, owner_user_id TEXT)',
            'CREATE TABLE api_keys (id TEXT PRIMARY KEY, client_id TEXT)',
            'CREATE TABLE monthly_usage (api_key_id TEXT, lookup_count INTEGER)',
            'CREATE TABLE subscriptions (id TEXT PRIMARY KEY, user_id TEXT, plan_code TEXT, monthly_calls_used INTEGER)',
            "INSERT INTO api_clients VALUES ('c','u')",
            "INSERT INTO api_keys VALUES ('a','c'),('b','c')",
            "INSERT INTO monthly_usage VALUES ('a',100),('a',80),('b',70)",
            "INSERT INTO subscriptions VALUES ('s','u','FREE',70)",
        ]: conn.execute(text(sql))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        assert conn.scalar(text('SELECT free_credits_used FROM subscriptions')) == 250
        assert conn.scalar(text('SELECT free_credits_used FROM api_clients')) == 250


def test_exhausted_free_account_does_not_resolve_products(unauthenticated_client, session_factory, monkeypatch):
    _, key = account(session_factory, used=250)
    def unexpected(*args, **kwargs):
        raise AssertionError("Exhausted free accounts must be blocked before provider work")
    monkeypatch.setattr('app.api.routes.resolve_product', unexpected)
    headers = {'X-API-Key':key}
    for response in [
        unauthenticated_client.get('/v1/products/3017620422003', headers=headers),
        unauthenticated_client.post('/v1/products/batch', json={'barcodes':['3017620422003']}, headers=headers),
    ]:
        assert response.status_code == 429
        assert response.json()['error']['upgrade_url'] == 'https://barcodenest.com/billing/'
