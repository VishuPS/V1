from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.auth import issue_api_key, rate_limiter
from app.config import get_settings
from app.main import app
from app.models import ApiClient, Product


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    rate_limiter.reset()
    yield
    rate_limiter.reset()


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add(
            Product(
                barcode="3017620422003",
                barcode_type="EAN-13",
                name="Nutella",
                brand="Ferrero",
                categories=[],
                allergens=[],
                nutrition={},
                countries=[],
                source="Open Food Facts",
                source_id="3017620422003",
            )
        )
        session.add(
            Product(
                barcode="012345678905",
                barcode_type="UPC-A",
                name="Example UPC Product",
                categories=[],
                allergens=[],
                nutrition={},
                countries=[],
                source="Test Source",
                source_id="upc-1",
            )
        )
        session.commit()
    return factory


@pytest.fixture
def api_key(session_factory: sessionmaker[Session]) -> str:
    with session_factory() as session:
        client = ApiClient(
            identifier="test-client",
            display_name="Test Client",
            plan="FREE",
        )
        session.add(client)
        session.flush()
        _, raw_key = issue_api_key(session, client, get_settings(), name="test-key")
        return raw_key


def _override_db(session_factory: sessionmaker[Session]):
    def override_db() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session
    return override_db


@pytest.fixture
def client(
    session_factory: sessionmaker[Session], api_key: str
) -> Generator[TestClient, None, None]:
    app.dependency_overrides[get_db] = _override_db(session_factory)
    with TestClient(
        app,
        raise_server_exceptions=False,
        headers={"X-API-Key": api_key},
    ) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def unauthenticated_client(
    session_factory: sessionmaker[Session],
) -> Generator[TestClient, None, None]:
    app.dependency_overrides[get_db] = _override_db(session_factory)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()
