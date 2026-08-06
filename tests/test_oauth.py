from sqlalchemy import func, select

from app.api import auth_routes
from app.config import Settings, get_settings
from app.main import app
from app.models import ApiKey, OAuthIdentity, User
from app.oauth import ProviderIdentity


def oauth_settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url="sqlite://",
        jwt_secret="oauth-test-secret-that-is-at-least-32-characters",
        jwt_issuer="https://api.barcodenest.com",
        website_url="https://barcodenest.com",
        google_oauth_client_id="google-client",
        google_oauth_client_secret="google-secret",
        github_oauth_client_id="github-client",
        github_oauth_client_secret="github-secret",
    )


def test_oauth_start_uses_state_pkce_and_secure_callback(unauthenticated_client):
    settings = oauth_settings()
    app.dependency_overrides[get_settings] = lambda: settings
    response = unauthenticated_client.get("/v1/auth/oauth/google", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith("https://accounts.google.com/")
    assert "code_challenge=" in response.headers["location"]
    assert "state=" in response.headers["location"]
    assert "barcodenest_oauth_state=" in response.headers["set-cookie"]


def test_new_oauth_user_gets_one_key_and_repeat_login_does_not_create_another(
    unauthenticated_client, session_factory, monkeypatch
):
    settings = oauth_settings()
    app.dependency_overrides[get_settings] = lambda: settings

    async def identity(*_args, **_kwargs):
        return ProviderIdentity("github", "durable-123", "builder@example.com", "Builder")

    monkeypatch.setattr(auth_routes, "fetch_provider_identity", identity)

    first_start = unauthenticated_client.get("/v1/auth/oauth/github", follow_redirects=False)
    state = unauthenticated_client.cookies.get("barcodenest_oauth_state")
    first = unauthenticated_client.get(
        f"/v1/auth/oauth/github/callback?code=test-code&state={state}",
        follow_redirects=False,
    )
    assert first.status_code == 302
    assert first.headers["location"].startswith("https://barcodenest.com/oauth-complete/#api_key=gpa_")

    with session_factory() as session:
        user = session.scalar(select(User).where(User.email == "builder@example.com"))
        assert user is not None
        assert user.password_hash is None
        assert session.scalar(select(func.count()).select_from(OAuthIdentity)) == 1
        assert session.scalar(select(func.count()).select_from(ApiKey).where(ApiKey.owner_user_id == user.id)) == 1

    second_start = unauthenticated_client.get("/v1/auth/oauth/github", follow_redirects=False)
    second_state = unauthenticated_client.cookies.get("barcodenest_oauth_state")
    second = unauthenticated_client.get(
        f"/v1/auth/oauth/github/callback?code=test-code&state={second_state}",
        follow_redirects=False,
    )
    assert second.status_code == 302
    assert second.headers["location"] == "https://barcodenest.com/dashboard/"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ApiKey)) == 1


def test_unconfigured_oauth_provider_is_unavailable(unauthenticated_client):
    settings = Settings(_env_file=None)
    app.dependency_overrides[get_settings] = lambda: settings
    response = unauthenticated_client.get("/v1/auth/oauth/google", follow_redirects=False)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "oauth_provider_unavailable"


def test_returning_admin_oauth_user_is_sent_to_admin(
    unauthenticated_client, session_factory, monkeypatch
):
    settings = oauth_settings()
    app.dependency_overrides[get_settings] = lambda: settings

    async def identity(*_args, **_kwargs):
        return ProviderIdentity("google", "admin-123", "admin@example.com", "Admin")

    monkeypatch.setattr(auth_routes, "fetch_provider_identity", identity)
    unauthenticated_client.get("/v1/auth/oauth/google", follow_redirects=False)
    state = unauthenticated_client.cookies.get("barcodenest_oauth_state")
    unauthenticated_client.get(
        f"/v1/auth/oauth/google/callback?code=test-code&state={state}", follow_redirects=False
    )
    with session_factory() as session:
        user = session.scalar(select(User).where(User.email == "admin@example.com"))
        user.is_admin = True
        session.commit()

    start = unauthenticated_client.get("/v1/auth/oauth/google", follow_redirects=False)
    state = unauthenticated_client.cookies.get("barcodenest_oauth_state")
    response = unauthenticated_client.get(
        f"/v1/auth/oauth/google/callback?code=test-code&state={state}", follow_redirects=False
    )
    assert start.status_code == 302
    assert response.headers["location"] == "https://barcodenest.com/admin/"
