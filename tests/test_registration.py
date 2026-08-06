from sqlalchemy import select

from app.config import get_settings
from app.models import ApiClient, ApiKey, AuthSession, Subscription, User


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
    assert body["monthly_lookups"] == 500
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
