from datetime import datetime, timedelta, timezone

from sqlalchemy import inspect, select

from app.models import ApiKey, LookupAnalytics
from tests.test_admin import auth_headers, create_user


def test_valid_lookup_outcomes_and_admin_hit_rate(
    client, unauthenticated_client, session_factory
):
    assert client.get("/v1/products/3017620422003").status_code == 200
    assert client.get("/v1/products/4006381333931").status_code == 404
    assert client.get("/v1/products/not-a-barcode").status_code == 400
    batch = client.post(
        "/v1/products/batch",
        json={"barcodes": ["3017620422003", "5449000000996", "123"]},
    )
    assert batch.status_code == 200

    with session_factory() as session:
        events = session.scalars(
            select(LookupAnalytics)
        ).all()
        assert len(events) == 4
        assert {
            (event.canonical_gtin, event.endpoint_type): event.found for event in events
        } == {
            ("03017620422003", "single"): True,
            ("04006381333931", "single"): False,
            ("03017620422003", "batch"): True,
            ("05449000000996", "batch"): False,
        }
        assert all(len(event.canonical_gtin) == 14 for event in events)

    create_user(
        session_factory, email="analytics-admin@example.com", name="Admin", is_admin=True
    )
    headers = auth_headers(unauthenticated_client, "analytics-admin@example.com")
    summary = unauthenticated_client.get(
        "/v1/admin/analytics/lookups?days=30", headers=headers
    )
    assert summary.status_code == 200
    assert summary.json() == {
        "period_days": 30,
        "valid_lookups": 4,
        "found_lookups": 2,
        "missed_lookups": 2,
        "hit_rate": 50.0,
        "unique_gtins": 3,
        "unique_missed_gtins": 2,
        "single_lookups": 2,
        "batch_lookups": 2,
    }
    misses = unauthenticated_client.get(
        "/v1/admin/analytics/misses?days=30", headers=headers
    )
    assert misses.status_code == 200
    assert {item["canonical_gtin"] for item in misses.json()["items"]} == {
        "04006381333931", "05449000000996"
    }


def test_analytics_routes_are_admin_only(unauthenticated_client, session_factory):
    create_user(session_factory, email="member-analytics@example.com", name="Member")
    assert unauthenticated_client.get("/v1/admin/analytics/lookups").status_code == 401
    headers = auth_headers(unauthenticated_client, "member-analytics@example.com")
    assert unauthenticated_client.get(
        "/v1/admin/analytics/lookups", headers=headers
    ).status_code == 403
    assert unauthenticated_client.get(
        "/v1/admin/analytics/misses", headers=headers
    ).status_code == 403


def test_analytics_schema_omits_network_and_credential_data(session_factory):
    with session_factory() as session:
        columns = {column["name"] for column in inspect(session.bind).get_columns("lookup_analytics")}
    assert not columns.intersection(
        {"api_key", "key_hash", "ip", "ip_address", "user_agent", "headers", "response_body"}
    )


def test_expired_analytics_are_removed(client, session_factory):
    with session_factory() as session:
        key = session.scalar(select(ApiKey))
        session.add(
            LookupAnalytics(
                request_id="old-request",
                api_key_id=key.id,
                canonical_gtin="04006381333931",
                barcode_type="EAN-13",
                endpoint_type="single",
                found=False,
                plan_code="FREE",
                occurred_at=datetime.now(timezone.utc) - timedelta(days=181),
            )
        )
        session.commit()
    assert client.get("/v1/products/3017620422003").status_code == 200
    with session_factory() as session:
        assert session.scalar(
            select(LookupAnalytics).where(LookupAnalytics.request_id == "old-request")
        ) is None


def test_analytics_database_failure_does_not_fail_lookup(client, session_factory):
    with session_factory() as session:
        LookupAnalytics.__table__.drop(session.bind)
    response = client.get("/v1/products/3017620422003")
    assert response.status_code == 200
    assert response.json()["found"] is True
