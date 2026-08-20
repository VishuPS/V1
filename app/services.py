import time
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.barcodes import BarcodeError, parse_barcode
from app.repositories import ProductRepository
from app.schemas import LookupResult, ProductData, SourceData
from app.config import Settings, get_settings
from app.fallbacks import FallbackResolver
from app.ingestion.multi_source import MappedSourceProduct


@dataclass(slots=True)
class LookupResolution:
    result: LookupResult
    local_found: bool = False
    fallback_attempted: bool = False
    providers_attempted: list[str] = field(default_factory=list)
    provider_found: str | None = None
    local_lookup_ms: float = 0.0
    fallback_total_ms: float = 0.0


def _result_from_mapped(raw: str, mapped: MappedSourceProduct) -> LookupResult:
    return LookupResult(
        barcode=raw, barcode_type=mapped.barcode_type,
        canonical_gtin=mapped.canonical_gtin, valid=True, found=True,
        product=ProductData(
            name=mapped.name, brand=mapped.brand, categories=mapped.categories,
            quantity=mapped.quantity, image_url=mapped.image_url,
            ingredients=mapped.ingredients, allergens=mapped.allergens,
            nutrition=mapped.nutrition, countries=mapped.countries,
        ),
        source=SourceData(name=mapped.source, source_id=mapped.source_product_id),
    )


def resolve_product(
    session: Session, raw_barcode: str, *, settings: Settings | None = None,
    fallback_resolver: FallbackResolver | None = None,
) -> LookupResolution:
    started = time.perf_counter()
    try:
        barcode = parse_barcode(raw_barcode)
    except BarcodeError as exc:
        return LookupResolution(LookupResult(
            barcode=raw_barcode, barcode_type=None, canonical_gtin=None,
            valid=False, found=False, error=str(exc),
        ))
    product = ProductRepository(session).find_by_barcode(barcode)
    local_ms = (time.perf_counter() - started) * 1000
    if product is not None:
        return LookupResolution(
            LookupResult(
                barcode=barcode.value, barcode_type=barcode.barcode_type,
                canonical_gtin=barcode.gtin14, valid=True, found=True,
                product=ProductData.model_validate(product),
                source=SourceData(name=product.source, source_id=product.source_id),
            ), local_found=True, local_lookup_ms=local_ms,
        )
    active_settings = settings or get_settings()
    if not active_settings.fallback_lookups_enabled:
        return LookupResolution(
            LookupResult(barcode=barcode.value, barcode_type=barcode.barcode_type,
                         canonical_gtin=barcode.gtin14, valid=True, found=False),
            local_lookup_ms=local_ms,
        )
    resolver = fallback_resolver or FallbackResolver(session, active_settings)
    fallback = resolver.resolve(barcode.gtin14)
    if fallback.product is not None:
        result = LookupResult(
            barcode=barcode.value, barcode_type=barcode.barcode_type,
            canonical_gtin=barcode.gtin14, valid=True, found=True,
            product=ProductData.model_validate(fallback.product),
            source=SourceData(name=fallback.product.source, source_id=fallback.product.source_id),
        )
    elif fallback.transient is not None:
        result = _result_from_mapped(barcode.value, fallback.transient)
    else:
        result = LookupResult(barcode=barcode.value, barcode_type=barcode.barcode_type,
                              canonical_gtin=barcode.gtin14, valid=True, found=False)
    return LookupResolution(
        result=result, local_found=False, fallback_attempted=bool(fallback.providers_attempted),
        providers_attempted=fallback.providers_attempted,
        provider_found=fallback.provider_found, local_lookup_ms=local_ms,
        fallback_total_ms=fallback.fallback_ms,
    )


def lookup_product(session: Session, raw_barcode: str) -> LookupResult:
    return resolve_product(session, raw_barcode).result
