from datetime import datetime, timezone

from sqlalchemy import select

from app.auth import issue_api_key
from app.config import get_settings
from app.models import ApiClient, ApiKey, Subscription, SubscriptionPlan, User
from app.user_auth import hash_password


PASSWORD = "correct horse battery staple"


def create_user(
    session_factory,
    *,
    email: str,
    name: str,
    organization: str | None = None,
    is_admin: bool = False,
) -> str:
    with session_factory() as session:
        if session.get(SubscriptionPlan, "FREE") is None:
            session.add(
                SubscriptionPlan(
                    code="FREE",
                    name="Free",
                    monthly_lookups=250,
                    requests_per_minute=30,
                    price_cents=0,
                    currency="EUR",
                )
            )
        user = User(
            email=email,
            display_name=name,
            organization=organization,
            password_hash=hash_password(PASSWORD),
            email_verified_at=datetime.now(timezone.utc),
            is_admin=is_admin,
        )
        session.add(user)
        session.flush()
        api_client = ApiClient(
            owner_user_id=user.id,
            identifier=email,
            display_name=name,
            plan="FREE",
        )
        session.add(api_client)
        session.flush()
        key, _ = issue_api_key(
            session, api_client, get_settings(), name="initial", commit=False
        )
        key.owner_user_id = user.id
        session.add(Subscription(user_id=user.id, plan_code="FREE", status="active"))
        session.commit()
        return user.id


def auth_headers(client, email: str) -> dict[str, str]:
    response = client.post(
        "/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_admin_routes_require_authentication_and_admin_role(
    unauthenticated_client, session_factory
):
    create_user(
        session_factory, email="member@example.com", name="Member", is_admin=False
    )
    assert unauthenticated_client.get("/v1/admin/dashboard").status_code == 401

    headers = auth_headers(unauthenticated_client, "member@example.com")
    response = unauthenticated_client.get("/v1/admin/dashboard", headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "admin_required"


def test_admin_dashboard_and_searchable_user_list(
    unauthenticated_client, session_factory
):
    create_user(
        session_factory, email="admin@example.com", name="Admin", is_admin=True
    )
    target_id = create_user(
        session_factory,
        email="developer@example.com",
        name="Developer One",
        organization="Nest Labs",
    )
    headers = auth_headers(unauthenticated_client, "admin@example.com")

    dashboard = unauthenticated_client.get("/v1/admin/dashboard", headers=headers)
    assert dashboard.status_code == 200
    assert dashboard.json()["total_users"] == 2
    assert dashboard.json()["total_api_keys"] == 2
    assert dashboard.json()["active_subscriptions"] == 0
    assert dashboard.json()["subscriptions_connected"] is True

    listed = unauthenticated_client.get(
        "/v1/admin/users?search=Nest", headers=headers
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == target_id
    assert listed.json()["items"][0]["api_key_status"] == "active"

    details = unauthenticated_client.get(
        f"/v1/admin/users/{target_id}", headers=headers
    )
    assert details.status_code == 200
    assert details.json()["organization"] == "Nest Labs"
    assert len(details.json()["api_keys"]) == 1


def test_admin_disables_account_changes_role_and_regenerates_key(
    unauthenticated_client, session_factory
):
    admin_id = create_user(
        session_factory, email="admin@example.com", name="Admin", is_admin=True
    )
    target_id = create_user(
        session_factory, email="target@example.com", name="Target"
    )
    headers = auth_headers(unauthenticated_client, "admin@example.com")

    promoted = unauthenticated_client.post(
        f"/v1/admin/users/{target_id}/admin-role",
        json={"is_admin": True},
        headers=headers,
    )
    assert promoted.status_code == 200
    assert promoted.json()["is_admin"] is True

    regenerated = unauthenticated_client.post(
        f"/v1/admin/users/{target_id}/api-keys/regenerate",
        json={},
        headers=headers,
    )
    assert regenerated.status_code == 200
    assert regenerated.json()["api_key"].startswith("gpa_")
    with session_factory() as session:
        keys = session.scalars(
            select(ApiKey).where(ApiKey.owner_user_id == target_id)
        ).all()
        assert len(keys) == 2
        assert sum(key.active for key in keys) == 1

    disabled = unauthenticated_client.post(
        f"/v1/admin/users/{target_id}/status",
        json={"active": False},
        headers=headers,
    )
    assert disabled.status_code == 200
    assert disabled.json()["active"] is False
    assert (
        unauthenticated_client.post(
            "/v1/auth/login",
            json={"email": "target@example.com", "password": PASSWORD},
        ).status_code
        == 401
    )

    self_disable = unauthenticated_client.post(
        f"/v1/admin/users/{admin_id}/status",
        json={"active": False},
        headers=headers,
    )
    assert self_disable.status_code == 409
    self_demote = unauthenticated_client.post(
        f"/v1/admin/users/{admin_id}/admin-role",
        json={"is_admin": False},
        headers=headers,
    )
    assert self_demote.status_code == 409
