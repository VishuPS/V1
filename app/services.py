from sqlalchemy.orm import Session

from app.barcodes import BarcodeError, parse_barcode
from app.repositories import ProductRepository
from app.schemas import LookupResult, ProductData, SourceData


def lookup_product(session: Session, raw_barcode: str) -> LookupResult:
    try:
        barcode = parse_barcode(raw_barcode)
    except BarcodeError as exc:
        return LookupResult(
            barcode=raw_barcode,
            barcode_type=None,
            canonical_gtin=None,
            valid=False,
            found=False,
            error=str(exc),
        )

    product = ProductRepository(session).find_by_barcode(barcode)
    if product is None:
        return LookupResult(
            barcode=barcode.value,
            barcode_type=barcode.barcode_type,
            canonical_gtin=barcode.gtin14,
            valid=True,
            found=False,
        )
    return LookupResult(
        barcode=barcode.value,
        barcode_type=barcode.barcode_type,
        canonical_gtin=barcode.gtin14,
        valid=True,
        found=True,
        product=ProductData.model_validate(product),
        source=SourceData(name=product.source, source_id=product.source_id),
    )
