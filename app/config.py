from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Grocery & Retail Product Barcode API"
    app_env: str = "development"
    database_url: str = "sqlite:///./products.db"
    auto_create_tables: bool = True
    cors_allowed_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:3000",
        "http://localhost:5173",
    ]
    log_level: str = "INFO"
    batch_limit: int = 100
    open_food_facts_dataset_url: str = (
        "https://static.openfoodfacts.org/data/openfoodfacts-products.jsonl.gz"
    )
    ingestion_batch_size: int = 1_000

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("batch_limit")
    @classmethod
    def validate_batch_limit(cls, value: int) -> int:
        if value < 1:
            raise ValueError("BATCH_LIMIT must be at least 1")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
