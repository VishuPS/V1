from sqlalchemy import select

from app.config import Settings, get_settings
from app.models import ApiClient, ApiKey, AuthSession, Subscription, User
from app.registration import create_registration, registration_network_hash
from app.schemas import RegistrationCreate
from fastapi import HTTPException
import pytest


PAYLOAD = {
    "name": "Ada Builder",
    "email": " Ada@Example.com ",
    "organization": "Example Labs",
    "use_case": "Inventory application",
    "password": "correct horse battery staple",
}


def test_registration_immediately_issues_free_key_and_session(
    unauthenticated_client, session_factory
):
    response = unauthenticated_client.post("/v1/auth/register", json=PAYLOAD)
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "ada@example.com"
    assert body["plan"] == "FREE"
    assert body["monthly_lookups"] == 250
    assert body["requests_per_minute"] == 30
    assert body["api_key"].startswith("gpa_")
    assert "HttpOnly" in response.headers["set-cookie"]

    with session_factory() as session:
        user = session.scalar(select(User).where(User.email == "ada@example.com"))
        assert user is not None
        assert user.password_hash.startswith("$argon2id$")
        assert user.email_verified_at is None
        client = session.scalar(select(ApiClient).where(ApiClient.identifier == "ada@example.com"))
        assert client is not None
        assert client.owner_user_id == user.id
        assert client.plan == "FREE"
        assert len(session.scalars(select(ApiKey).where(ApiKey.client_id == client.id)).all()) == 1
        assert session.scalar(select(Subscription).where(Subscription.user_id == user.id)).plan_code == "FREE"
        assert session.scalar(select(AuthSession).where(AuthSession.user_id == user.id)) is not None

    account = unauthenticated_client.get("/v1/account")
    assert account.status_code == 200
    assert account.json()["email"] == "ada@example.com"
    lookup = unauthenticated_client.get(
        "/v1/products/3017620422003", headers={"X-API-Key": body["api_key"]}
    )
    assert lookup.status_code == 200


def test_duplicate_email_is_rejected_without_another_key(
    unauthenticated_client, session_factory
):
    assert unauthenticated_client.post("/v1/auth/register", json=PAYLOAD).status_code == 201
    response = unauthenticated_client.post("/v1/auth/register", json=PAYLOAD)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_already_registered"
    with session_factory() as session:
        assert len(session.scalars(select(User)).all()) == 1
        assert len(session.scalars(select(ApiKey)).all()) == 1


def test_invalid_email_is_rejected(unauthenticated_client):
    response = unauthenticated_client.post(
        "/v1/auth/register",
        json={"name": "Test User", "email": "not-an-email", "password": "correct horse battery staple"},
    )
    assert response.status_code == 422


def test_registration_can_be_disabled(unauthenticated_client, monkeypatch):
    monkeypatch.setattr(get_settings(), "registration_enabled", False)
    response = unauthenticated_client.post("/v1/auth/register", json=PAYLOAD)
    assert response.status_code == 503


def test_free_tier_network_claim_allows_three_and_blocks_fourth(session_factory):
    configured = Settings(
        _env_file=None,
        free_tier_ip_limit_enabled=True,
        api_key_hash_secret="test-secret-at-least-thirty-two-characters",
    )
    first = RegistrationCreate(**PAYLOAD)
    second = RegistrationCreate(
        name="Second Account", email="second@example.com",
        password="correct horse battery staple",
    )
    with session_factory() as session:
        create_registration(
            session, first, configured, registration_ip="203.0.113.10"
        )
        create_registration(session, second, configured, registration_ip="203.0.113.10")
        create_registration(session, RegistrationCreate(name="Third Account", email="third@example.com", password=PAYLOAD["password"]), configured, registration_ip="203.0.113.10")
        with pytest.raises(HTTPException) as exc:
            create_registration(session, RegistrationCreate(name="Fourth Account", email="fourth@example.com", password=PAYLOAD["password"]), configured, registration_ip="203.0.113.10")
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "free_tier_network_limit_reached"
        assert len(session.scalars(select(User)).all()) == 3


def test_free_tier_network_claim_allows_a_different_network(session_factory):
    configured = Settings(
        _env_file=None,
        free_tier_ip_limit_enabled=True,
        api_key_hash_secret="test-secret-at-least-thirty-two-characters",
    )
    with session_factory() as session:
        create_registration(
            session, RegistrationCreate(**PAYLOAD), configured,
            registration_ip="203.0.113.10",
        )
        create_registration(
            session,
            RegistrationCreate(
                name="Second Account", email="second@example.com",
                password="correct horse battery staple",
            ),
            configured,
            registration_ip="203.0.113.11",
        )
        assert len(session.scalars(select(User)).all()) == 2
        assert all(user.free_tier_registration_ip_hash for user in session.scalars(select(User)))


def test_registration_network_hash_uses_ipv6_64_and_never_raw_address():
    configured = Settings(
        _env_file=None,
        free_tier_ip_limit_enabled=True,
        api_key_hash_secret="test-secret-at-least-thirty-two-characters",
    )
    first = registration_network_hash("2001:db8:1:2::1", configured)
    rotated = registration_network_hash("2001:db8:1:2:abcd::5", configured)
    assert first == rotated
    assert first != "2001:db8:1:2::1"
    assert len(first) == 64


@pytest.mark.parametrize("domain", ["nesscoegroup.com", "outlook.com", "gmail.com", "hotmail.com"])
def test_domain_exemption_is_exact_and_does_not_consume_slots(session_factory, domain):
    configured = Settings(_env_file=None, free_tier_ip_limit_enabled=True)
    with session_factory() as session:
        for i in range(5):
            user, _ = create_registration(session, RegistrationCreate(
                name="Exempt User", email=f"user{i}@{domain.upper()}", password=PAYLOAD["password"]
            ), configured, registration_ip="203.0.113.10")
            assert user.free_tier_registration_ip_hash is None
        for email in ("a@example.com", f"b@sub.{domain}", f"c@fake-{domain}"):
            create_registration(session, RegistrationCreate(name="Regular User", email=email, password=PAYLOAD["password"]), configured, registration_ip="203.0.113.10")
        with pytest.raises(HTTPException) as exc:
            create_registration(session, RegistrationCreate(name="Fourth User", email="d@example.com", password=PAYLOAD["password"]), configured, registration_ip="203.0.113.10")
        assert exc.value.detail["code"] == "free_tier_network_limit_reached"


@pytest.mark.parametrize("domain", ["nesscoegroup.com", "outlook.com", "gmail.com", "hotmail.com"])
def test_existing_exempt_claim_frees_a_slot(session_factory, domain):
    configured = Settings(_env_file=None, free_tier_ip_limit_enabled=True)
    with session_factory() as session:
        session.add(User(email=f"legacy@{domain}", display_name="Legacy",
                         free_tier_registration_ip_hash=registration_network_hash("203.0.113.10", configured)))
        session.commit()
        for i in range(3):
            create_registration(session, RegistrationCreate(name="Regular User", email=f"regular{i}@example.com", password=PAYLOAD["password"]), configured, registration_ip="203.0.113.10")
        assert session.scalar(select(User).where(User.email == f"legacy@{domain}")).free_tier_registration_ip_hash is None


@pytest.mark.parametrize("peer,expected", [("10.25.119.181", "198.51.100.8"), ("198.51.100.9", "198.51.100.9")])
def test_forwarded_address_only_trusted_from_render_network(peer, expected):
    import asyncio
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
    seen = []
    async def capture(scope, receive, send):
        seen.append(scope["client"][0])
    middleware = ProxyHeadersMiddleware(capture, trusted_hosts="127.0.0.1,10.0.0.0/8")
    scope = {"type": "http", "client": (peer, 1234), "scheme": "http",
             "headers": [(b"x-forwarded-for", b"203.0.113.55, 198.51.100.8")]}
    asyncio.run(middleware(scope, None, None))
    assert seen == [expected]


def test_cloudflare_and_render_chain_uses_client_not_rotating_edge():
    import asyncio
    import re
    from pathlib import Path
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
    trusted = re.search(r"FORWARDED_ALLOW_IPS:-([^}]+)", Path("Dockerfile").read_text()).group(1)
    seen = []
    async def capture(scope, receive, send):
        seen.append(scope["client"][0])
    middleware = ProxyHeadersMiddleware(capture, trusted_hosts=trusted)
    for edge in ("172.64.32.1", "104.16.10.1"):
        scope = {"type": "http", "client": ("10.25.119.181", 1234), "scheme": "http",
                 "headers": [(b"x-forwarded-for", f"192.0.2.99, 198.51.100.8, {edge}".encode())]}
        asyncio.run(middleware(scope, None, None))
    assert seen == ["198.51.100.8", "198.51.100.8"]
