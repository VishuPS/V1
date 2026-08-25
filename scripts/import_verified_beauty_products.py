"""Idempotently import the administrator-verified beauty product batch.

The command is dry-run by default. Pass --apply only after reviewing its plan.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.barcodes import BarcodeError, normalize_gtin, parse_barcode
from app.db import SessionLocal
from app.ingestion.multi_source import MappedSourceProduct, apply_mapped_record, merge_canonical
from app.models import ProductSourceRecord, new_uuid
from app.repositories import ProductRepository


SOURCE = "MANUAL_VERIFIED"
LICENSE = "BARCODENEST_ADMIN_VERIFIED"
BATCH_ID = "verified-beauty-2026-08-25"


PRODUCTS = [
    dict(raw_gtins=["4052136160291"], name="Artdeco Eyeliner for Sensitive Eyes 1ml - Shade: 6 Ocean Eyes", brand="ARTDECO", categories=["Personal Care > Cosmetics > Makeup > Eye Makeup > Eyeliner"], quantity="1 ml", metadata={"shade":"6 Ocean Eyes"}),
    dict(raw_gtins=["8410088000083"], name="Alcohol 96 Antiseptico Kelsia 1 Ud", brand="KELSIA", categories=["Personal Care / Health & Beauty"], quantity=None, metadata={}),
    dict(raw_gtins=["8022297019857"], name="Alfaparf Equipment Double Defence Cream - 5.07 Oz", brand="Alfaparf", categories=["Personal Care > Hair Care > Hair Color Removers"], quantity="5.07 oz", metadata={}),
    dict(raw_gtins=["8717496442772"], name="KIS KeraDirect Color Direktziehende Farben Fun-Box, Alle Nuancen Je 200ml", brand="KIS", categories=["Health & Beauty"], quantity="200 ml", metadata={}),
    dict(raw_gtins=["639370908014", "0639370908014"], name="CND Vinylux Weekly Nail Polish Crushed Rose 0.5 Fl Oz", brand="CND", categories=["Personal Care > Cosmetics > Cosmetic Tools > Nail Tools"], quantity="0.5 fl oz", metadata={"variant":"Crushed Rose"}),
    dict(raw_gtins=["639370909646", "0639370909646"], name="CND Vinylux Weekly Nail Polish Mauve Maverick 0.5 Fl Oz", brand="CND", categories=["Personal Care > Cosmetics > Nail Care"], quantity="0.5 fl oz", metadata={"variant":"Mauve Maverick"}),
    dict(raw_gtins=["8436044510595"], name="Desodorante Mineral", brand="ECO BEAUTY", categories=["Personal Care"], quantity="120 g", metadata={"product_type":"mineral deodorant"}),
    dict(raw_gtins=["4971710519990"], name="Decorte Aq Absolute Brightening Cream 25ml", brand="DECORTÉ", categories=["Health & Beauty > Skin Care"], quantity="25 ml", metadata={"description":"Decorte Aq Absolute Brightening Cream 25ml.","mpn":"4971710519990","brand_normalization":{"rejected_source_value":"Ninguno","verified_value":"DECORTÉ"}}),
    dict(raw_gtins=["8412122400644"], name="Esmalte Youth Color 064 Think Pink", brand="Beter", categories=["Personal Care > Cosmetics > Makeup"], quantity=None, metadata={"variant":"064 Think Pink","product_type":"Nail Polish"}),
]


@dataclass(slots=True)
class PlanItem:
    gtin: str
    name: str
    action: str
    canonical_gtin: str | None
    existing_product_id: str | None = None
    provenance_exists: bool = False
    reason: str | None = None


def mapped_products() -> tuple[list[MappedSourceProduct], list[PlanItem]]:
    mapped: list[MappedSourceProduct] = []
    rejected: list[PlanItem] = []
    seen: set[str] = set()
    for item in PRODUCTS:
        parsed = []
        try:
            for raw in item["raw_gtins"]:
                parsed.append((raw, parse_barcode(raw), normalize_gtin(raw)))
        except BarcodeError as exc:
            rejected.append(PlanItem(item["raw_gtins"][0], item["name"], "SKIP_INVALID", None, reason=str(exc)))
            continue
        canonicals = {canonical for _, _, canonical in parsed}
        if len(canonicals) != 1:
            rejected.append(PlanItem(item["raw_gtins"][0], item["name"], "SKIP_CONFLICT", None, reason=f"Identifiers normalize to {sorted(canonicals)}"))
            continue
        canonical = canonicals.pop()
        if canonical in seen:
            rejected.append(PlanItem(item["raw_gtins"][0], item["name"], "SKIP_DUPLICATE", canonical, reason="Duplicate canonical GTIN in batch"))
            continue
        seen.add(canonical)
        mapped.append(MappedSourceProduct(
            canonical_gtin=canonical, barcode_type="GTIN-14", name=item["name"], brand=item["brand"],
            categories=item["categories"], quantity=item["quantity"], source=SOURCE,
            source_product_id=f"{BATCH_ID}:{canonical}", source_gtin=parsed[0][0], license=LICENSE,
            priority=10, source_metadata={"batch_id":BATCH_ID,"verified_by":"BarcodeNest administrator","identifiers":[raw for raw,_,_ in parsed],**item["metadata"]},
        ))
    return mapped, rejected


def _would_enrich(existing, incoming: MappedSourceProduct) -> bool:
    if (not existing.brand and incoming.brand) or (not existing.quantity and incoming.quantity):
        return True
    present = {value.casefold() for value in existing.categories or []}
    return any(value.casefold() not in present for value in incoming.categories)


def build_plan(session: Session) -> tuple[list[MappedSourceProduct], list[PlanItem]]:
    mapped, plan = mapped_products()
    repository = ProductRepository(session)
    for incoming in mapped:
        existing = repository.find_by_barcode(parse_barcode(incoming.source_gtin))
        provenance = session.scalar(select(ProductSourceRecord).where(
            ProductSourceRecord.source == SOURCE,
            ProductSourceRecord.source_product_id == incoming.source_product_id,
        ))
        if existing is None:
            action = "INSERT"
        elif _would_enrich(existing, incoming):
            action = "ENRICH_EXISTING"
        else:
            action = "ALREADY_EXISTS"
        plan.append(PlanItem(incoming.source_gtin, incoming.name, action, incoming.canonical_gtin, existing.barcode if existing else None, provenance is not None))
    return mapped, plan


def apply_plan(session: Session, records: list[MappedSourceProduct]) -> None:
    repository = ProductRepository(session)
    now = datetime.now(timezone.utc)
    for incoming in records:
        if session.bind.dialect.name == "postgresql":
            session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:gtin))"), {"gtin":incoming.canonical_gtin})
        existing = repository.find_by_barcode(parse_barcode(incoming.source_gtin))
        if existing is None:
            product = apply_mapped_record(session, incoming)
        else:
            merge_canonical(existing, incoming)
            product = existing
        provenance = session.scalar(select(ProductSourceRecord).where(
            ProductSourceRecord.source == SOURCE,
            ProductSourceRecord.source_product_id == incoming.source_product_id,
        ))
        if provenance is None:
            provenance = ProductSourceRecord(id=new_uuid(), product_barcode=product.barcode, source=SOURCE,
                source_product_id=incoming.source_product_id, source_gtin=incoming.source_gtin, source_url=None,
                license=LICENSE, priority=incoming.priority, imported_at=now, last_seen_at=now,
                source_metadata=incoming.source_metadata)
            session.add(provenance)
        else:
            provenance.product_barcode=product.barcode; provenance.last_seen_at=now; provenance.source_metadata=incoming.source_metadata
    session.commit()


def target_label(database_url: str) -> str:
    url = make_url(database_url)
    return f"{url.host or 'local'}/{url.database or ''}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Import the verified beauty product batch (dry-run by default)")
    parser.add_argument("--apply", action="store_true", help="Commit the reviewed plan")
    args = parser.parse_args()
    with SessionLocal() as session:
        records, plan = build_plan(session)
        print(json.dumps({"target":target_label(str(session.bind.url)),"apply":args.apply,"summary":{
            "existing_matches":sum(p.action in {"ALREADY_EXISTS","ENRICH_EXISTING"} for p in plan),
            "to_insert":sum(p.action=="INSERT" for p in plan),"to_enrich":sum(p.action=="ENRICH_EXISTING" for p in plan),
            "conflicts":sum(p.action in {"SKIP_CONFLICT","SKIP_DUPLICATE"} for p in plan),
            "invalid":sum(p.action=="SKIP_INVALID" for p in plan)},"items":[asdict(p) for p in plan]}, ensure_ascii=False, indent=2))
        if args.apply:
            apply_plan(session, records)


if __name__ == "__main__":
    main()
