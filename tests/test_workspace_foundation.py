from datetime import datetime,timezone
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine,text,select
from app.config import get_settings
from app.entitlements import account_entitlements,capacity_for
from app.models import PlatformStatistic,Subscription,SubscriptionPlan,User
from tests.test_user_auth import create_account,login


def test_new_customer_and_legacy_capacity_do_not_change_lookup_allowance(unauthenticated_client,session_factory):
    client=unauthenticated_client;uid,_=create_account(session_factory)
    with session_factory() as session:
        session.add(SubscriptionPlan(code="CREATOR",name="Creator",monthly_lookups=0,requests_per_minute=120))
        sub=session.scalar(select(Subscription).where(Subscription.user_id==uid));sub.plan_code="CREATOR"
        user=session.get(User,uid);assert user.workspace_policy=="platform_v1"
        session.commit()
        result=account_entitlements(session,user,get_settings())
        assert result["vault_capacity"]==500 and result["features"]["sku_management"]
        assert not result["features"]["inventory"] and not result["features"]["team_workspace"]
        user.workspace_policy="legacy";session.commit()
        assert account_entitlements(session,user,get_settings())["vault_capacity"]==250
        sub.status="unpaid";session.commit()
        result=account_entitlements(session,user,get_settings())
        assert result["read_only"] and not result["features"]["advanced_exports"]
        assert sub.monthly_call_limit==250  # No billing fields mutated by entitlement reads.


def test_statistics_endpoint_only_returns_verified_snapshot(unauthenticated_client,session_factory):
    client=unauthenticated_client
    assert client.get("/v1/public/statistics").json()["product_count"] is None
    with session_factory() as session:
        session.add(PlatformStatistic(key="public_product_count",value=2,verified_at=datetime.now(timezone.utc),method="exact_count"));session.commit()
    response=client.get("/v1/public/statistics")
    assert response.json()["product_count"]==2
    assert response.json()["verified_at"] and "max-age=300" in response.headers["cache-control"]
    assert client.get("/v1/account/entitlements").status_code==401


def test_internal_csv_labels_use_account_session(unauthenticated_client,session_factory):
    client=unauthenticated_client;create_account(session_factory)
    token=login(client).json()["access_token"]
    response=client.post("/v1/account/labels/from-csv",headers={"Authorization":"Bearer "+token},json={"csv_text":"barcode,symbology,title\nSKU-1,code128,Box\n"})
    assert response.status_code==200 and response.content.startswith(b"%PDF")


def test_policy_migration_grandfathers_existing_users(tmp_path,monkeypatch):
    database=tmp_path/"cohorts.db";url="sqlite:///"+database.as_posix()
    monkeypatch.setenv("DATABASE_URL",url);get_settings.cache_clear()
    config=Config(str(Path(__file__).resolve().parents[1]/"alembic.ini"))
    command.upgrade(config,"20261124_0023")
    engine=create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id,email,display_name,active,is_admin,created_at,updated_at) VALUES ('old','old@example.invalid','Old',1,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
    command.upgrade(config,"20261125_0024")
    with engine.begin() as conn:
        assert conn.execute(text("SELECT workspace_policy FROM users WHERE id='old'")).scalar()=="legacy"
        conn.execute(text("INSERT INTO users (id,email,display_name,active,is_admin,created_at,updated_at) VALUES ('new','new@example.invalid','New',1,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
        assert conn.execute(text("SELECT workspace_policy FROM users WHERE id='new'")).scalar()=="platform_v1"
    command.downgrade(config,"20261124_0023")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM users")).scalar()==2
    engine.dispose();get_settings.cache_clear()
