"""Opt-in real PostgreSQL concurrency checks; never target a production database."""
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,select,func
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from app.config import get_settings
from app.db import get_db
from app.main import app
from app.models import Subscription,SkuAllocation,ServiceUsage
from tests.test_user_auth import create_account,login

URL=os.environ.get("WORKSPACE_POSTGRES_TEST_URL")
pytestmark=pytest.mark.skipif(not URL,reason="Set WORKSPACE_POSTGRES_TEST_URL to an isolated local workspace_test database")


@pytest.fixture
def postgres_workspace(monkeypatch):
    url=make_url(URL)
    assert url.host in {"127.0.0.1","localhost"} and url.database.startswith("workspace_test"),"Only an explicitly named local test database is permitted"
    monkeypatch.setenv("DATABASE_URL",URL);monkeypatch.setenv("APP_ENV","test");monkeypatch.setenv("AUTO_CREATE_TABLES","false");get_settings.cache_clear()
    config=Config(str(Path(__file__).resolve().parents[1]/"alembic.ini"));command.upgrade(config,"head")
    engine=create_engine(URL,pool_size=10,max_overflow=5);factory=sessionmaker(bind=engine,expire_on_commit=False)
    def db():
        with factory() as session:
            yield session
    app.dependency_overrides[get_db]=db
    email="concurrency-"+uuid4().hex+"@example.invalid"
    uid,_=create_account(factory,email)
    with factory() as session:
        sub=session.scalar(select(Subscription).where(Subscription.user_id==uid));sub.plan_code="CREATOR";session.commit()
    with TestClient(app,raise_server_exceptions=False) as client:
        token=login(client,email).json()["access_token"]
        yield client,factory,uid,{"Authorization":"Bearer "+token}
    app.dependency_overrides.clear();engine.dispose();get_settings.cache_clear()


def test_postgres_concurrent_sku_sequence_and_quota_rollback(postgres_workspace):
    client,factory,uid,headers=postgres_workspace
    def generate(_):
        return client.post("/v1/account/skus/generate",headers=headers,json={"prefix":"PG","quantity":1})
    with ThreadPoolExecutor(max_workers=10) as pool:
        responses=list(pool.map(generate,range(20)))
    assert all(response.status_code==200 for response in responses),[(response.status_code,response.text[:200]) for response in responses]
    codes=[response.json()["items"][0]["code"] for response in responses]
    assert len(set(codes))==20
    assert sorted(codes)==[f"PG-{index:06d}" for index in range(1,21)]
    settings=get_settings().model_copy(update={"workspace_monthly_limits":{"CREATOR":{"sku_generation":21,"bulk_rows":1000}}})
    app.dependency_overrides[get_settings]=lambda:settings
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(generate,range(2)))
    assert sorted(response.status_code for response in responses)==[200,429]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(SkuAllocation).where(SkuAllocation.user_id==uid))==21
        assert session.scalar(select(ServiceUsage.units).where(ServiceUsage.account_scope=="user:"+uid,ServiceUsage.category=="sku_generation"))==21
