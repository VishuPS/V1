from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

import httpx

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.barcodes import BarcodeError, parse_barcode
from app.config import Settings
from app.identifiers import normalize_isbn
from app.ingestion.multi_source import MappedSourceProduct, apply_mapped_record
from app.ingestion.open_food_facts import clean_text, normalize_tags, select_image_url
from app.models import FallbackProviderState, Product, new_uuid

logger = logging.getLogger(__name__)


def _retry_after(headers: dict[str, str], default: int = 60) -> int:
    try:
        return max(1, int(headers.get("Retry-After", str(default))))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)


class HttpTransport(Protocol):
    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> HttpResponse: ...


class UrllibTransport:
    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> HttpResponse:
        with httpx.Client(
            timeout=httpx.Timeout(timeout), follow_redirects=True, headers=headers
        ) as client:
            response = client.get(url)
        return HttpResponse(response.status_code, response.content, dict(response.headers))


@dataclass(slots=True)
class FallbackCandidate:
    mapped: MappedSourceProduct
    persist_allowed: bool


@dataclass(slots=True)
class ProviderResult:
    status: str
    candidate: FallbackCandidate | None = None
    retry_after_seconds: int | None = None
    detail: str | None = None


@dataclass(slots=True)
class Resolution:
    product: Product | None = None
    transient: MappedSourceProduct | None = None
    providers_attempted: list[str] = field(default_factory=list)
    provider_found: str | None = None
    fallback_ms: float = 0.0


def _json(response: HttpResponse) -> Any:
    return json.loads(response.body.decode("utf-8"))


def _valid_candidate(requested_gtin: str, source_gtin: str | None) -> str | None:
    try:
        parsed = parse_barcode(source_gtin or "")
    except BarcodeError:
        return None
    return parsed.gtin14 if parsed.gtin14 == requested_gtin else None


def _mapped(
    gtin: str, *, source: str, source_id: str, name: str, license_name: str,
    source_url: str | None = None, brand: str | None = None,
    categories: list[str] | None = None, image_url: str | None = None,
    metadata: dict[str, Any] | None = None, priority: int = 250,
) -> MappedSourceProduct:
    parsed = parse_barcode(gtin)
    return MappedSourceProduct(
        canonical_gtin=parsed.gtin14, barcode_type=parsed.barcode_type, name=name,
        source=source, source_product_id=source_id, source_gtin=gtin,
        license=license_name, source_url=source_url, brand=brand,
        categories=categories or [], image_url=image_url, priority=priority,
        source_metadata=metadata or {},
    )


class ProviderAdapter(Protocol):
    name: str
    negative_ttl: int
    def lookup(self, canonical_gtin: str) -> ProviderResult: ...


class OpenFactsFallback:
    name = "OPEN_FACTS_API"

    def __init__(self, settings: Settings, transport: HttpTransport) -> None:
        self.transport, self.timeout = transport, settings.open_facts_timeout_seconds
        self.negative_ttl, self.user_agent = settings.open_facts_negative_ttl_seconds, settings.fallback_user_agent

    def lookup(self, canonical_gtin: str) -> ProviderResult:
        code = canonical_gtin.lstrip("0") or canonical_gtin
        fields = "code,product_name,generic_name,brands,categories_tags,quantity,image_front_url,image_url,ingredients_text,allergens_tags,nutriments,countries_tags,last_modified_t"
        # Product Opener v3 can route across food, beauty, pet-food, and general
        # Open Facts databases with product_type=all, avoiding four blind calls.
        url = f"https://world.openfoodfacts.org/api/v3/product/{code}?" + urllib.parse.urlencode({"fields": fields, "product_type": "all"})
        response = self.transport.get(url, headers={"User-Agent": self.user_agent, "Accept": "application/json"}, timeout=self.timeout)
        if response.status == 404:
            return ProviderResult("miss")
        if response.status == 429 or response.status >= 500:
            return ProviderResult("unavailable", retry_after_seconds=_retry_after(response.headers))
        if response.status != 200:
            return ProviderResult("error", detail=f"http_{response.status}")
        data = _json(response)
        product = data.get("product") if isinstance(data, dict) else None
        if not isinstance(product, dict) or data.get("status") == 0:
            return ProviderResult("miss")
        returned = clean_text(product.get("code")) or code
        if not _valid_candidate(canonical_gtin, returned):
            return ProviderResult("invalid", detail="gtin_mismatch")
        name = clean_text(product.get("product_name") or product.get("generic_name") or product.get("brands"))
        if not name:
            return ProviderResult("invalid", detail="missing_title")
        mapped = _mapped(
            returned, source=self.name, source_id=returned, name=name, license_name="ODbL-1.0",
            source_url=f"https://world.openfoodfacts.org/product/{returned}",
            brand=clean_text(product.get("brands")), categories=normalize_tags(product.get("categories_tags")),
            image_url=select_image_url(product, returned),
            metadata={"contents_license": "DbCL-1.0", "image_license": "CC-BY-SA", "retrieved_via": "api_v3_product_type_all"},
            priority=180,
        )
        return ProviderResult("found", FallbackCandidate(mapped, True))


class UPCItemDBFallback:
    name = "UPCITEMDB"
    _lock = threading.Lock()
    _last_call = 0.0

    def __init__(self, settings: Settings, transport: HttpTransport) -> None:
        self.transport, self.timeout = transport, settings.upcitemdb_timeout_seconds
        self.negative_ttl, self.user_agent = settings.upcitemdb_negative_ttl_seconds, settings.fallback_user_agent
        self.api_key, self.min_interval = settings.upcitemdb_api_key, settings.upcitemdb_min_interval_seconds
        self.persistence_enabled = settings.upcitemdb_persistence_enabled

    def lookup(self, canonical_gtin: str) -> ProviderResult:
        with self._lock:
            elapsed = time.monotonic() - self._last_call
            if elapsed < self.min_interval:
                return ProviderResult("unavailable", retry_after_seconds=max(1, int(self.min_interval - elapsed)), detail="local_rate_limit")
            type(self)._last_call = time.monotonic()
        paid = bool(self.api_key)
        endpoint = "v1" if paid else "trial"
        code = canonical_gtin.lstrip("0") or canonical_gtin
        url = f"https://api.upcitemdb.com/prod/{endpoint}/lookup?" + urllib.parse.urlencode({"upc": code})
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        if self.api_key:
            headers.update({"user_key": self.api_key, "key_type": "3scale"})
        response = self.transport.get(url, headers=headers, timeout=self.timeout)
        if response.status == 404:
            return ProviderResult("miss")
        if response.status == 429 or response.status >= 500:
            return ProviderResult("unavailable", retry_after_seconds=_retry_after(response.headers))
        if response.status != 200:
            return ProviderResult("error", detail=f"http_{response.status}")
        data = _json(response)
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list) or not items:
            return ProviderResult("miss")
        item = items[0]
        returned = clean_text(item.get("ean") or item.get("upc"))
        if not _valid_candidate(canonical_gtin, returned):
            return ProviderResult("invalid", detail="gtin_mismatch")
        name = clean_text(item.get("title"))
        if not name:
            return ProviderResult("invalid", detail="missing_title")
        images = item.get("images") if isinstance(item.get("images"), list) else []
        mapped = _mapped(
            returned, source=self.name, source_id=clean_text(item.get("ean")) or returned,
            name=name, license_name="UPCITEMDB-WRITTEN-PERMISSION",
            # UPCitemdb marketplace taxonomy is not consistently a product
            # taxonomy (live validation classified shock absorbers as
            # watercraft parts). Preserve it for provenance, not canonical API
            # categories.
            brand=clean_text(item.get("brand")), categories=[],
            image_url=clean_text(images[0]) if images else None,
            metadata={"description": clean_text(item.get("description")),
                      "source_category": clean_text(item.get("category")),
                      "permission_basis": "written_provider_permission_confirmed_by_operator"}, priority=300,
        )
        return ProviderResult("found", FallbackCandidate(mapped, self.persistence_enabled))


def _localized_text(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    preferred = clean_text(value.get("en"))
    if preferred:
        return preferred
    return next((text for item in value.values() if (text := clean_text(item))), None)


class EANDBFallback:
    """Final authenticated fallback using EAN-DB's Product API v2."""

    name = "EANDB"

    def __init__(self, settings: Settings, transport: HttpTransport) -> None:
        self.transport = transport
        self.timeout = settings.eandb_timeout_seconds
        self.negative_ttl = settings.eandb_negative_ttl_seconds
        self.api_key = settings.eandb_api_key
        self.persistence_enabled = settings.eandb_persistence_enabled
        self.user_agent = settings.eandb_user_agent or settings.fallback_user_agent

    def lookup(self, canonical_gtin: str) -> ProviderResult:
        if not self.api_key:
            return ProviderResult("unavailable", detail="missing_api_key")
        code = canonical_gtin.lstrip("0") or canonical_gtin
        response = self.transport.get(
            f"https://ean-db.com/api/v2/product/{code}",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
                "User-Agent": self.user_agent,
            },
            timeout=self.timeout,
        )
        if response.status == 404:
            return ProviderResult("miss")
        if response.status == 400:
            return ProviderResult("invalid", detail="provider_rejected_gtin")
        if response.status in {401, 403}:
            return ProviderResult(
                "unavailable", retry_after_seconds=300, detail="access_denied"
            )
        if response.status == 429 or response.status >= 500:
            return ProviderResult(
                "unavailable", retry_after_seconds=_retry_after(response.headers)
            )
        if response.status != 200:
            return ProviderResult("error", detail=f"http_{response.status}")

        data = _json(response)
        product = data.get("product") if isinstance(data, dict) else None
        if not isinstance(product, dict):
            return ProviderResult("invalid", detail="missing_product")
        returned = clean_text(product.get("barcode"))
        if not _valid_candidate(canonical_gtin, returned):
            return ProviderResult("invalid", detail="gtin_mismatch")
        name = _localized_text(product.get("titles"))
        if not name:
            return ProviderResult("invalid", detail="missing_title")

        manufacturer = product.get("manufacturer")
        brand = (
            _localized_text(manufacturer.get("titles"))
            if isinstance(manufacturer, dict) else None
        )
        categories = [
            title
            for category in product.get("categories", [])
            if isinstance(category, dict)
            and (title := _localized_text(category.get("titles")))
        ]
        images = product.get("images")
        image_url = (
            next(
                (
                    url
                    for image in images
                    if isinstance(image, dict)
                    and (url := clean_text(image.get("url")))
                    and url.startswith(("https://", "http://"))
                ),
                None,
            )
            if isinstance(images, list)
            else None
        )
        details = product.get("barcodeDetails")
        mapped = _mapped(
            returned,
            source=self.name,
            source_id=returned,
            name=name,
            license_name="EANDB-PROPRIETARY-API",
            source_url="https://ean-db.com/",
            brand=brand,
            categories=categories,
            image_url=image_url,
            metadata={
                "barcode_details": details if isinstance(details, dict) else {},
                "persistence": (
                    "operator-confirmed-separate-permission"
                    if self.persistence_enabled else "disabled_provider_terms"
                ),
            },
            priority=350,
        )
        return ProviderResult(
            "found", FallbackCandidate(mapped, self.persistence_enabled)
        )


class GoogleBooksFallback:
    name = "GOOGLE_BOOKS"

    def __init__(self, settings: Settings, transport: HttpTransport) -> None:
        self.transport, self.timeout = transport, settings.google_books_timeout_seconds
        self.negative_ttl, self.api_key = settings.google_books_negative_ttl_seconds, settings.google_books_api_key

    def lookup(self, canonical_gtin: str) -> ProviderResult:
        isbn, _ = normalize_isbn(canonical_gtin[-13:])
        params = {"q": f"isbn:{isbn}", "maxResults": 5, "printType": "books"}
        if self.api_key:
            params["key"] = self.api_key
        response = self.transport.get("https://www.googleapis.com/books/v1/volumes?" + urllib.parse.urlencode(params), headers={"Accept": "application/json"}, timeout=self.timeout)
        if response.status == 429 or response.status >= 500:
            return ProviderResult("unavailable", retry_after_seconds=60)
        if response.status != 200:
            return ProviderResult("error", detail=f"http_{response.status}")
        data = _json(response)
        for item in data.get("items", []) if isinstance(data, dict) else []:
            info = item.get("volumeInfo", {})
            identifiers = info.get("industryIdentifiers", [])
            returned = next((entry.get("identifier") for entry in identifiers if entry.get("type") == "ISBN_13"), None)
            if not _valid_candidate(canonical_gtin, returned):
                continue
            title = clean_text(info.get("title"))
            if not title:
                continue
            subtitle = clean_text(info.get("subtitle"))
            images = info.get("imageLinks") or {}
            mapped = _mapped(
                returned, source=self.name, source_id=str(item.get("id") or returned),
                name=f"{title}: {subtitle}" if subtitle else title,
                license_name="GOOGLE-API-NO-PERSIST", source_url=clean_text(info.get("infoLink")),
                brand=clean_text(info.get("publisher")), categories=[str(x) for x in info.get("categories", [])],
                image_url=clean_text(images.get("thumbnail")),
                metadata={"authors": info.get("authors", []), "published_date": info.get("publishedDate"), "description": clean_text(info.get("description")), "identifiers": identifiers, "persistence": "disabled_google_api_terms"},
            )
            return ProviderResult("found", FallbackCandidate(mapped, False))
        return ProviderResult("miss")


class OpenLibraryFallback:
    name = "OPEN_LIBRARY"

    def __init__(self, settings: Settings, transport: HttpTransport) -> None:
        self.transport, self.timeout = transport, settings.open_library_timeout_seconds
        self.negative_ttl, self.user_agent = settings.open_library_negative_ttl_seconds, settings.fallback_user_agent

    def lookup(self, canonical_gtin: str) -> ProviderResult:
        isbn, _ = normalize_isbn(canonical_gtin[-13:])
        key = f"ISBN:{isbn}"
        url = "https://openlibrary.org/api/books?" + urllib.parse.urlencode({"bibkeys": key, "jscmd": "data", "format": "json"})
        response = self.transport.get(url, headers={"User-Agent": self.user_agent, "Accept": "application/json"}, timeout=self.timeout)
        if response.status in (403, 429) or response.status >= 500:
            return ProviderResult("unavailable", retry_after_seconds=60)
        if response.status != 200:
            return ProviderResult("error", detail=f"http_{response.status}")
        data = _json(response)
        item = data.get(key) if isinstance(data, dict) else None
        if not isinstance(item, dict):
            return ProviderResult("miss")
        title = clean_text(item.get("title"))
        if not title:
            return ProviderResult("invalid", detail="missing_title")
        authors = [x.get("name") for x in item.get("authors", []) if isinstance(x, dict) and x.get("name")]
        publishers = [x.get("name") for x in item.get("publishers", []) if isinstance(x, dict) and x.get("name")]
        subjects = [x.get("name") for x in item.get("subjects", []) if isinstance(x, dict) and x.get("name")]
        cover = item.get("cover") or {}
        mapped = _mapped(
            isbn, source=self.name, source_id=clean_text(item.get("key")) or isbn,
            name=title, license_name="OPEN-LIBRARY-CONTRIBUTIONS-CC0",
            source_url=clean_text(item.get("url")), brand=publishers[0] if publishers else None,
            categories=subjects, image_url=None,
            metadata={"authors": authors, "publishers": publishers, "publish_date": item.get("publish_date"), "isbn": isbn,
                      "source_cover_url": clean_text(cover.get("large") or cover.get("medium"))}, priority=220,
        )
        return ProviderResult("found", FallbackCandidate(mapped, True))


class FallbackResolver:
    def __init__(self, session: Session, settings: Settings, *, transport: HttpTransport | None = None, providers: list[ProviderAdapter] | None = None) -> None:
        self.session, self.settings = session, settings
        transport = transport or UrllibTransport()
        self.providers = providers or self._providers(transport)

    def _providers(self, transport: HttpTransport) -> list[ProviderAdapter]:
        general: list[ProviderAdapter] = []
        if self.settings.open_facts_fallback_enabled:
            general.append(OpenFactsFallback(self.settings, transport))
        if self.settings.upcitemdb_enabled:
            general.append(UPCItemDBFallback(self.settings, transport))
        if self.settings.eandb_enabled and self.settings.eandb_api_key:
            general.append(EANDBFallback(self.settings, transport))
        return general

    def _route(self, gtin: str) -> list[ProviderAdapter]:
        is_isbn = gtin[-13:].startswith(("978", "979"))
        if not is_isbn:
            return self.providers
        books: list[ProviderAdapter] = []
        transport = getattr(self.providers[0], "transport", UrllibTransport()) if self.providers else UrllibTransport()
        if self.settings.google_books_enabled:
            books.append(GoogleBooksFallback(self.settings, transport))
        if self.settings.open_library_enabled:
            books.append(OpenLibraryFallback(self.settings, transport))
        return books + [
            p for p in self.providers if p.name in {"UPCITEMDB", "EANDB"}
        ]

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    def _cached(self, gtin: str, provider: str, now: datetime) -> bool:
        state = self.session.scalar(select(FallbackProviderState).where(FallbackProviderState.canonical_gtin == gtin, FallbackProviderState.provider == provider))
        return bool(state and self._aware(state.expires_at) > now and state.status in {"miss", "unavailable", "error", "invalid"})

    def _record(self, gtin: str, provider: ProviderAdapter, result: ProviderResult, now: datetime) -> None:
        state = self.session.scalar(select(FallbackProviderState).where(FallbackProviderState.canonical_gtin == gtin, FallbackProviderState.provider == provider.name))
        ttl = result.retry_after_seconds or provider.negative_ttl
        if state is None:
            state = FallbackProviderState(id=new_uuid(), canonical_gtin=gtin, provider=provider.name, status=result.status, checked_at=now, expires_at=now + timedelta(seconds=ttl))
            self.session.add(state)
        state.status, state.checked_at, state.expires_at, state.detail = result.status, now, now + timedelta(seconds=ttl), result.detail
        state.retry_after_at = now + timedelta(seconds=result.retry_after_seconds) if result.retry_after_seconds else None

    def resolve(self, canonical_gtin: str) -> Resolution:
        started, now = time.perf_counter(), datetime.now(timezone.utc)
        resolution = Resolution()
        for provider in self._route(canonical_gtin):
            if self._cached(canonical_gtin, provider.name, now):
                continue
            resolution.providers_attempted.append(provider.name)
            provider_started = time.perf_counter()
            try:
                result = provider.lookup(canonical_gtin)
            except Exception as exc:
                logger.warning("Fallback provider failed provider=%s gtin=%s error=%s", provider.name, canonical_gtin, type(exc).__name__)
                result = ProviderResult("unavailable", retry_after_seconds=60, detail=type(exc).__name__)
            logger.info("Fallback provider timing provider=%s gtin=%s provider_ms=%.1f status=%s", provider.name, canonical_gtin, (time.perf_counter() - provider_started) * 1000, result.status)
            self._record(canonical_gtin, provider, result, now)
            if result.status != "found" or result.candidate is None:
                continue
            candidate = result.candidate
            if candidate.mapped.canonical_gtin != canonical_gtin:
                continue
            resolution.provider_found = provider.name
            if candidate.persist_allowed:
                resolution.product = apply_mapped_record(self.session, candidate.mapped)
                self.session.commit()
            else:
                resolution.transient = candidate.mapped
                self.session.commit()  # provider state only
            break
        resolution.fallback_ms = (time.perf_counter() - started) * 1000
        return resolution
