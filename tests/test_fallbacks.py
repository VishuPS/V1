import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.config import Settings
from app.fallbacks import (
    FallbackCandidate,
    EANDBFallback,
    FallbackResolver,
    GoogleBooksFallback,
    HttpResponse,
    OpenFactsFallback,
    OpenIcecatFallback,
    OpenLibraryFallback,
    ProviderResult,
    UPCItemDBFallback,
)
from app.ingestion.multi_source import MappedSourceProduct
from app.models import FallbackProviderState, MonthlyUsage, Product, ProductSourceRecord
from app.schemas import LookupResult, ProductData, SourceData
from app.services import LookupResolution, resolve_product


class Transport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, *, headers, timeout):
        self.calls.append((url, headers, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class Provider:
    negative_ttl = 86400

    def __init__(self, name, *results):
        self.name, self.results, self.calls = name, list(results), 0

    def lookup(self, canonical_gtin):
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def response(payload, status=200, headers=None):
    return HttpResponse(status, json.dumps(payload).encode(), headers or {})


def settings(**overrides):
    values = dict(_env_file=None, fallback_lookups_enabled=True,
                  upcitemdb_min_interval_seconds=0, open_icecat_min_interval_seconds=0)
    values.update(overrides)
    return Settings(**values)


def candidate(gtin="4006381333931", *, source="TEST", persist=True, name="Recovered Product"):
    mapped = MappedSourceProduct(
        canonical_gtin=gtin.zfill(14), barcode_type="EAN-13", name=name,
        source=source, source_product_id=gtin, source_gtin=gtin,
        license="TEST", brand="Good Brand", categories=["Useful"],
    )
    return ProviderResult("found", FallbackCandidate(mapped, persist))


def test_local_hit_causes_zero_fallback_requests(session_factory):
    provider = Provider("TEST", candidate())
    with session_factory() as session:
        resolver = FallbackResolver(session, settings(), providers=[provider])
        result = resolve_product(session, "3017620422003", settings=settings(), fallback_resolver=resolver)
    assert result.result.found and result.local_found
    assert provider.calls == 0


def test_local_miss_triggers_resolver_persists_provenance_and_future_hit(session_factory):
    provider = Provider("TEST", candidate())
    with session_factory() as session:
        resolver = FallbackResolver(session, settings(), providers=[provider])
        first = resolve_product(session, "4006381333931", settings=settings(), fallback_resolver=resolver)
        assert first.result.found and first.provider_found == "TEST"
        assert session.get(Product, "04006381333931").name == "Recovered Product"
        assert session.scalar(select(func.count()).select_from(ProductSourceRecord).where(ProductSourceRecord.source == "TEST")) == 1
        second = resolve_product(session, "4006381333931", settings=settings(), fallback_resolver=resolver)
    assert second.local_found is True
    assert provider.calls == 1


def test_negative_cache_and_expiry(session_factory):
    provider = Provider("TEST", ProviderResult("miss"), candidate())
    with session_factory() as session:
        resolver = FallbackResolver(session, settings(), providers=[provider])
        assert not resolver.resolve("04006381333931").product
        assert not resolver.resolve("04006381333931").product
        assert provider.calls == 1
        state = session.scalar(select(FallbackProviderState))
        ttl = state.expires_at.replace(tzinfo=timezone.utc) - state.checked_at.replace(tzinfo=timezone.utc)
        assert ttl.total_seconds() == 86400
        state.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()
        assert resolver.resolve("04006381333931").product
    assert provider.calls == 2


def test_provider_outage_is_isolated(session_factory):
    broken = Provider("BROKEN", TimeoutError("late"))
    healthy = Provider("HEALTHY", candidate())
    with session_factory() as session:
        resolver = FallbackResolver(session, settings(), providers=[broken, healthy])
        result = resolve_product(session, "4006381333931", settings=settings(), fallback_resolver=resolver)
    assert result.result.found and result.provider_found == "HEALTHY"


def test_wrong_gtin_candidate_is_rejected(session_factory):
    provider = Provider("TEST", candidate("5449000000996"))
    with session_factory() as session:
        result = FallbackResolver(session, settings(), providers=[provider]).resolve("04006381333931")
    assert result.product is None and result.transient is None


def test_conservative_merge_does_not_replace_existing_values(session_factory):
    incoming = candidate("3017620422003", name="Weak Name").candidate.mapped
    incoming.brand = "Marketplace Seller"
    with session_factory() as session:
        from app.ingestion.multi_source import merge_canonical
        product = session.get(Product, "3017620422003")
        merge_canonical(product, incoming)
        assert product.name == "Nutella"
        assert product.brand == "Ferrero"
        assert "Useful" in product.categories


def test_open_facts_hit_and_malformed_response():
    good = response({"status": 1, "product": {"code": "4006381333931", "product_name": "Example", "brands": "Brand", "categories_tags": ["en:test"]}})
    transport = Transport(good, response({"unexpected": True}))
    adapter = OpenFactsFallback(settings(), transport)
    found = adapter.lookup("04006381333931")
    assert found.status == "found" and found.candidate.persist_allowed
    assert adapter.lookup("04006381333931").status == "miss"


def test_upcitemdb_hit_is_persistable_with_written_permission_and_handles_failures():
    hit = response({"items": [{"ean": "4006381333931", "title": "Component", "brand": "Bilstein", "category": "Automotive", "images": ["https://example.test/a.jpg"]}]})
    transport = Transport(hit, HttpResponse(404, b"{}"), HttpResponse(429, b"{}", {"Retry-After": "90"}), TimeoutError())
    adapter = UPCItemDBFallback(settings(), transport)
    result = adapter.lookup("04006381333931")
    assert result.status == "found" and result.candidate.persist_allowed
    assert result.candidate.mapped.categories == []
    assert result.candidate.mapped.source_metadata["source_category"] == "Automotive"
    assert result.candidate.mapped.license == "UPCITEMDB-WRITTEN-PERMISSION"
    assert adapter.lookup("04006381333931").status == "miss"
    limited = adapter.lookup("04006381333931")
    assert limited.status == "unavailable" and limited.retry_after_seconds == 90
    try:
        adapter.lookup("04006381333931")
    except TimeoutError:
        pass
    else:
        raise AssertionError("transport timeout should be isolated by resolver")


def test_upcitemdb_wrong_gtin_and_malformed_json():
    transport = Transport(
        response({"items": [{"ean": "5449000000996", "title": "Wrong"}]}),
        HttpResponse(200, b"not-json"),
    )
    adapter = UPCItemDBFallback(settings(), transport)
    assert adapter.lookup("04006381333931").status == "invalid"
    try:
        adapter.lookup("04006381333931")
    except json.JSONDecodeError:
        pass
    else:
        raise AssertionError("malformed JSON should be isolated by resolver")


def test_eandb_maps_localized_product_and_is_transient_by_default():
    payload = {"balance": 247, "product": {
        "barcode": "0893594002037",
        "titles": {"en": "PopCorners Cheesy Jalapeño"},
        "manufacturer": {"titles": {"en": "POPCORNERS"}},
        "categories": [{"id": "422", "titles": {"en": "Food Items"}}],
        "images": [{"url": "https://ean-db.com/example.jpg"}],
        "barcodeDetails": {"type": "EAN-13", "country": "us"},
    }}
    transport = Transport(response(payload))
    adapter = EANDBFallback(
        settings(eandb_api_key="secret", eandb_user_agent="Account Owner"),
        transport,
    )

    result = adapter.lookup("00893594002037")

    assert result.status == "found"
    assert result.candidate.persist_allowed is False
    assert result.candidate.mapped.name == "PopCorners Cheesy Jalapeño"
    assert result.candidate.mapped.brand == "POPCORNERS"
    assert result.candidate.mapped.categories == ["Food Items"]
    assert result.candidate.mapped.image_url == "https://ean-db.com/example.jpg"
    assert transport.calls[0][1]["Authorization"] == "Bearer secret"
    assert transport.calls[0][1]["User-Agent"] == "Account Owner"


def test_eandb_rejects_wrong_gtin_and_handles_provider_statuses():
    transport = Transport(
        response({"product": {"barcode": "5449000000996", "titles": {"en": "Wrong"}}}),
        HttpResponse(404, b"{}"),
        HttpResponse(403, b"{}"),
        HttpResponse(429, b"{}", {"Retry-After": "45"}),
    )
    adapter = EANDBFallback(settings(eandb_api_key="secret"), transport)

    assert adapter.lookup("04006381333931").status == "invalid"
    assert adapter.lookup("04006381333931").status == "miss"
    denied = adapter.lookup("04006381333931")
    assert denied.status == "unavailable" and denied.retry_after_seconds == 300
    limited = adapter.lookup("04006381333931")
    assert limited.status == "unavailable" and limited.retry_after_seconds == 45


def test_eandb_is_final_general_fallback_and_can_persist_with_permission(session_factory):
    transport = Transport(response({"product": {
        "barcode": "5010775181694",
        "titles": {"en": "Kinnerton Tango chocolate bar"},
        "manufacturer": {"titles": {"en": "Kinnerton"}},
        "categories": [], "images": [],
    }}))
    configured = settings(
        open_facts_fallback_enabled=False,
        upcitemdb_enabled=False,
        eandb_enabled=True,
        eandb_api_key="secret",
        eandb_persistence_enabled=True,
    )
    with session_factory() as session:
        resolver = FallbackResolver(session, configured, transport=transport)
        assert [provider.name for provider in resolver.providers] == ["EANDB"]
        result = resolve_product(
            session, "5010775181694", settings=configured,
            fallback_resolver=resolver,
        )
        assert result.provider_found == "EANDB"
        assert session.get(Product, "05010775181694").name == "Kinnerton Tango chocolate bar"


def test_eandb_is_ordered_after_upcitemdb(session_factory):
    configured = settings(eandb_enabled=True, eandb_api_key="secret")
    with session_factory() as session:
        resolver = FallbackResolver(session, configured, transport=Transport())
    assert [provider.name for provider in resolver.providers] == [
        "OPEN_FACTS_API", "UPCITEMDB", "EANDB"
    ]


def test_open_icecat_is_final_and_exact_gtin_only(session_factory):
    xml = b'''<ICECAT-interface><files.index><file Product_ID="832848" Prod_ID="1447B006" Catid="575" Model_Name="EOS 400D"><M_Prod_ID Supplier_name="Canon"/><EAN_UPCS><EAN_UPC Value="4960999358246"/></EAN_UPCS></file></files.index></ICECAT-interface>'''
    configured = settings(open_icecat_enabled=True, open_icecat_api_token="token")
    adapter = OpenIcecatFallback(configured, Transport(HttpResponse(200, xml)))
    result = adapter.lookup("04960999358246")
    assert result.status == "found"
    assert result.candidate.persist_allowed is False
    assert result.candidate.mapped.name == "EOS 400D"
    assert result.candidate.mapped.brand == "Canon"
    assert result.candidate.mapped.source_metadata["icecat_category_id"] == "575"

    wrong = xml.replace(b"4960999358246", b"4006381333931")
    assert OpenIcecatFallback(configured, Transport(HttpResponse(200, wrong))).lookup("04960999358246").status == "invalid"

    with session_factory() as session:
        resolver = FallbackResolver(session, configured, transport=Transport())
    assert [provider.name for provider in resolver.providers][-1] == "OPEN_ICECAT"


def test_open_icecat_verified_hit_persists_and_next_lookup_stays_local(session_factory):
    xml = b'''<ICECAT-interface><files.index><file Product_ID="832848" Prod_ID="1447B006" Catid="575" Model_Name="EOS 400D"><M_Prod_ID Supplier_name="Canon"/><EAN_UPCS><EAN_UPC Value="4960999358246"/></EAN_UPCS></file></files.index></ICECAT-interface>'''
    configured = settings(
        open_icecat_enabled=True,
        open_icecat_api_token="token",
        open_icecat_persistence_enabled=True,
    )
    transport = Transport(HttpResponse(200, xml))

    with session_factory() as session:
        resolver = FallbackResolver(
            session,
            configured,
            providers=[OpenIcecatFallback(configured, transport)],
        )
        first = resolve_product(
            session, "4960999358246", settings=configured,
            fallback_resolver=resolver,
        )
        second = resolve_product(
            session, "4960999358246", settings=configured,
            fallback_resolver=resolver,
        )

        assert first.provider_found == "OPEN_ICECAT"
        assert second.local_found is True
        product = session.get(Product, "04960999358246")
        assert product is not None
        assert product.name == "EOS 400D"
        assert product.brand == "Canon"
        provenance = session.scalar(select(ProductSourceRecord).where(
            ProductSourceRecord.source == "OPEN_ICECAT"
        ))
        assert provenance is not None
        assert provenance.license == "OPEN-ICECAT-OCL-1.4"
        assert provenance.source_metadata["attribution_required"] == "Specs Icecat"

    assert len(transport.calls) == 1


def test_open_icecat_handles_miss_auth_rate_limit_and_invalid_xml():
    configured = settings(open_icecat_api_token="token")
    transport = Transport(
        HttpResponse(200, b"<ICECAT-interface><files.index/></ICECAT-interface>"),
        HttpResponse(403, b""), HttpResponse(429, b"", {"Retry-After": "45"}),
        HttpResponse(200, b"not xml"),
    )
    adapter = OpenIcecatFallback(configured, transport)
    assert adapter.lookup("04960999358246").status == "miss"
    assert adapter.lookup("04960999358246").status == "unavailable"
    limited = adapter.lookup("04960999358246")
    assert limited.status == "unavailable" and limited.retry_after_seconds == 45
    assert adapter.lookup("04960999358246").status == "invalid"


def test_google_books_isbn_hit_is_transient():
    payload = {"items": [{"id": "abc", "volumeInfo": {
        "title": "Matilda", "authors": ["Roald Dahl"], "publisher": "Puffin",
        "industryIdentifiers": [{"type": "ISBN_13", "identifier": "9780140328721"}],
        "categories": ["Juvenile Fiction"], "imageLinks": {"thumbnail": "https://example.test/book.jpg"},
    }}]}
    result = GoogleBooksFallback(settings(), Transport(response(payload))).lookup("09780140328721")
    assert result.status == "found" and not result.candidate.persist_allowed
    assert result.candidate.mapped.source_metadata["authors"] == ["Roald Dahl"]


def test_open_library_isbn_hit_is_persistable():
    payload = {"ISBN:9780140328721": {"key": "/books/OL7353617M", "title": "Matilda",
        "authors": [{"name": "Roald Dahl"}], "publishers": [{"name": "Puffin"}],
        "subjects": [{"name": "Children's stories"}], "url": "https://openlibrary.org/books/OL7353617M/Matilda"}}
    result = OpenLibraryFallback(settings(), Transport(response(payload))).lookup("09780140328721")
    assert result.status == "found" and result.candidate.persist_allowed


def test_isbn_routing_uses_book_sources_before_general(session_factory):
    transport = Transport(response({"totalItems": 0}), response({}), HttpResponse(404, b"{}"))
    resolver = FallbackResolver(session_factory(), settings(open_facts_fallback_enabled=True, google_books_enabled=True), transport=transport)
    names = [provider.name for provider in resolver._route("09780140328721")]
    assert names[:2] == ["GOOGLE_BOOKS", "OPEN_LIBRARY"]
    assert "OPEN_FACTS_API" not in names


def test_transient_fallback_is_returned_without_product_persistence(session_factory):
    provider = Provider("UPCITEMDB", candidate(persist=False))
    with session_factory() as session:
        resolver = FallbackResolver(session, settings(), providers=[provider])
        result = resolve_product(session, "4006381333931", settings=settings(), fallback_resolver=resolver)
        assert result.result.found
        assert session.get(Product, "04006381333931") is None


def test_upcitemdb_persistence_can_be_disabled_outside_permission_scope():
    hit = response({"items": [{"ean": "4006381333931", "title": "Component"}]})
    adapter = UPCItemDBFallback(
        settings(upcitemdb_persistence_enabled=False), Transport(hit)
    )
    result = adapter.lookup("04006381333931")
    assert result.status == "found"
    assert result.candidate.persist_allowed is False


def test_upcitemdb_first_lookup_persists_and_second_lookup_stays_local(session_factory):
    hit = response({"items": [{
        "ean": "4006381333931",
        "title": "Recovered Component",
        "brand": "Example Brand",
        "images": ["https://example.test/product.jpg"],
    }]})
    transport = Transport(hit)

    with session_factory() as session:
        resolver = FallbackResolver(
            session,
            settings(upcitemdb_persistence_enabled=True),
            providers=[UPCItemDBFallback(settings(), transport)],
        )
        first = resolve_product(
            session, "4006381333931", settings=settings(),
            fallback_resolver=resolver,
        )
        second = resolve_product(
            session, "4006381333931", settings=settings(),
            fallback_resolver=resolver,
        )

        assert first.provider_found == "UPCITEMDB"
        assert second.local_found is True
        assert session.get(Product, "04006381333931").name == "Recovered Component"
        provenance = session.scalar(select(ProductSourceRecord).where(
            ProductSourceRecord.source == "UPCITEMDB"
        ))
        assert provenance is not None
        assert provenance.license == "UPCITEMDB-WRITTEN-PERMISSION"

    assert len(transport.calls) == 1


def test_fallback_hit_consumes_one_customer_lookup_unit(client, session_factory, monkeypatch):
    recovered = LookupResolution(
        result=LookupResult(
            barcode="4006381333931", barcode_type="EAN-13",
            canonical_gtin="04006381333931", valid=True, found=True,
            product=ProductData(name="Recovered"),
            source=SourceData(name="TEST", source_id="4006381333931"),
        ),
        fallback_attempted=True, providers_attempted=["TEST"], provider_found="TEST",
    )
    monkeypatch.setattr("app.api.routes.resolve_product", lambda *args, **kwargs: recovered)
    with session_factory() as session:
        before = session.scalar(select(MonthlyUsage))
        assert before is None
    assert client.get("/v1/products/4006381333931").status_code == 200
    with session_factory() as session:
        usage = session.scalar(select(MonthlyUsage))
        assert usage.lookup_count == 1
