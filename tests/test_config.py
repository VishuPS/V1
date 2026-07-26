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
