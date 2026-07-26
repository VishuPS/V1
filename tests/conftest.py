from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import Product


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
def client(session_factory: sessionmaker[Session]) -> Generator[TestClient, None, None]:
    def override_db() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()

