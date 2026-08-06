from datetime import datetime, timezone

from sqlalchemy import select

from app.auth import issue_api_key
from app.config import get_settings
from app.models import ApiClient, ApiKey, AuthSession, Subscription, SubscriptionPlan, User
from app.user_auth import hash_password


PASSWORD = "correct horse battery staple"


def create_account(session_factory, email: str = "owner@example.com") -> tuple[str, str]:
    settings = get_settings()
    with session_factory() as session:
        plan = session.get(SubscriptionPlan, "FREE")
        if plan is None:
            session.add(
                SubscriptionPlan(
                    code="FREE", name="Free", monthly_lookups=500,
                    requests_per_minute=30, price_cents=0, currency="EUR",
                )
            )
        user = User(
            email=email,
            password_hash=hash_password(PASSWORD),
            display_name="Account Owner",
            email_verified_at=datetime.now(timezone.utc),
        )
        session.add(user)
        session.flush()
        client = ApiClient(
            owner_user_id=user.id,
            identifier=email,
            display_name="Account Owner",
            plan="FREE",
        )
        session.add(client)
        session.flush()
        key, raw_key = issue_api_key(
            session, client, settings, name="initial", commit=False
        )
        key.owner_user_id = user.id
        session.add(Subscription(user_id=user.id, plan_code="FREE", status="active"))
        session.commit()
        return user.id, raw_key


def login(client, email: str = "owner@example.com"):
    return client.post(
        "/v1/auth/login", json={"email": email, "password": PASSWORD}
    )


def test_login_uses_argon2_and_access_token_reads_account(
    unauthenticated_client, session_factory
):
    user_id, _ = create_account(session_factory)
    response = login(unauthenticated_client)
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["refresh_token"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]

    account = unauthenticated_client.get(
        "/v1/account",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert account.status_code == 200
    assert account.json()["id"] == user_id
    assert account.json()["email"] == "owner@example.com"
    assert account.json()["plan"] == "FREE"

    with session_factory() as session:
        user = session.get(User, user_id)
        assert user.password_hash.startswith("$argon2id$")
        assert user.last_login_at is not None


def test_invalid_login_is_generic(unauthenticated_client, session_factory):
    create_account(session_factory)
    wrong_password = unauthenticated_client.post(
        "/v1/auth/login",
        json={"email": "owner@example.com", "password": "wrong"},
    )
    unknown_email = unauthenticated_client.post(
        "/v1/auth/login",
        json={"email": "unknown@example.com", "password": "wrong"},
    )
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()


def test_refresh_rotates_and_reuse_revokes_session(
    unauthenticated_client, session_factory
):
    user_id, _ = create_account(session_factory)
    first = login(unauthenticated_client).json()
    refreshed = unauthenticated_client.post(
        "/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert refreshed.status_code == 200
    second = refreshed.json()
    assert second["refresh_token"] != first["refresh_token"]

    reused = unauthenticated_client.post(
        "/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert reused.status_code == 401
    account = unauthenticated_client.get(
        "/v1/account",
        headers={"Authorization": f"Bearer {second['access_token']}"},
    )
    assert account.status_code == 401
    with session_factory() as session:
        record = session.scalar(select(AuthSession).where(AuthSession.user_id == user_id))
        assert record.revoked_at is not None


def test_logout_revokes_access_token(unauthenticated_client, session_factory):
    create_account(session_factory)
    tokens = login(unauthenticated_client).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    response = unauthenticated_client.post("/v1/auth/logout", headers=headers)
    assert response.status_code == 204
    assert unauthenticated_client.get("/v1/account", headers=headers).status_code == 401


def test_user_manages_only_owned_api_keys_and_usage(
    unauthenticated_client, session_factory
):
    _, initial_key = create_account(session_factory)
    tokens = login(unauthenticated_client).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    lookup = unauthenticated_client.get(
        "/v1/products/3017620422003", headers={"X-API-Key": initial_key}
    )
    assert lookup.status_code == 200
    usage = unauthenticated_client.get("/v1/account/usage", headers=headers)
    assert usage.status_code == 200
    assert usage.json()["lookup_count"] == 1
    assert usage.json()["remaining"] == 499

    created = unauthenticated_client.post(
        "/v1/account/api-keys", json={"name": "production"}, headers=headers
    )
    assert created.status_code == 201
    assert created.json()["api_key"].startswith("gpa_")
    key_id = created.json()["id"]
    listed = unauthenticated_client.get("/v1/account/api-keys", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 2
    assert all("api_key" not in item for item in listed.json())

    regenerated = unauthenticated_client.post(
        f"/v1/account/api-keys/{key_id}/regenerate", headers=headers
    )
    assert regenerated.status_code == 201
    replacement = regenerated.json()
    assert replacement["api_key"].startswith("gpa_")
    assert replacement["api_key"] != created.json()["api_key"]
    assert replacement["id"] != key_id
    with session_factory() as session:
        old_record = session.get(ApiKey, key_id)
        new_record = session.get(ApiKey, replacement["id"])
        assert old_record.active is False
        assert new_record.active is True
        assert new_record.key_hash != replacement["api_key"]
        assert replacement["api_key"] not in new_record.key_hash

    relisted = unauthenticated_client.get("/v1/account/api-keys", headers=headers)
    assert all("api_key" not in item for item in relisted.json())

    revoked = unauthenticated_client.delete(
        f"/v1/account/api-keys/{replacement['id']}", headers=headers
    )
    assert revoked.status_code == 204
    with session_factory() as session:
        assert session.get(ApiKey, replacement["id"]).active is False

    subscription = unauthenticated_client.get(
        "/v1/account/subscription", headers=headers
    )
    assert subscription.status_code == 200
    assert subscription.json()["plan"] == "FREE"


def test_state_change_rejects_untrusted_browser_origin(
    unauthenticated_client, session_factory
):
    create_account(session_factory)
    response = unauthenticated_client.post(
        "/v1/auth/login",
        json={"email": "owner@example.com", "password": PASSWORD},
        headers={"Origin": "https://attacker.example"},
    )
    assert response.status_code == 403
