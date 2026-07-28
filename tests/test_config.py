import pytest
from pydantic import ValidationError

from app.config import Settings


def test_comma_separated_cors_origins(monkeypatch) -> None:
    monkeypatch.setenv(
        "CORS_ALLOWED_ORIGINS",
        "https://app.example.com, https://admin.example.com",
    )
    settings = Settings(_env_file=None)
    assert settings.cors_allowed_origins == [
        "https://app.example.com",
        "https://admin.example.com",
    ]


def test_comma_separated_trusted_hosts(monkeypatch) -> None:
    monkeypatch.setenv("TRUSTED_HOSTS", "api.barcodenest.com, provider.example")
    settings = Settings(_env_file=None)
    assert settings.trusted_hosts == [
        "api.barcodenest.com",
        "provider.example",
    ]


@pytest.mark.parametrize(
    ("override", "message"),
    [
        (
            {"api_key_hash_secret": "development-only-change-me"},
            "API_KEY_HASH_SECRET",
        ),
        ({"auto_create_tables": True}, "AUTO_CREATE_TABLES=false"),
        ({"database_url": "sqlite:///./products.db"}, "PostgreSQL DATABASE_URL"),
    ],
)
def test_production_rejects_unsafe_configuration(
    override: dict[str, object], message: str
) -> None:
    values: dict[str, object] = {
        "_env_file": None,
        "app_env": "production",
        "api_key_hash_secret": "a-secure-production-secret-that-is-long-enough",
        "auto_create_tables": False,
        "database_url": "postgresql+psycopg://user:pass@db/api",
    }
    values.update(override)
    with pytest.raises(ValidationError, match=message):
        Settings(**values)


def test_safe_production_configuration_is_accepted() -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        api_key_hash_secret="a-secure-production-secret-that-is-long-enough",
        auto_create_tables=False,
        database_url="postgresql+psycopg://user:pass@db/api",
    )
    assert settings.app_env == "production"
