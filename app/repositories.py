from sqlalchemy import select
from sqlalchemy.orm import Session

from app.barcodes import Barcode
from app.models import Product


class ProductRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find_by_barcode(self, barcode: Barcode) -> Product | None:
        return self.session.scalar(
            select(Product).where(Product.barcode.in_(barcode.equivalents))
        )

    def upsert(self, product: Product) -> Product:
        existing = self.session.get(Product, product.barcode)
        if existing is None:
            self.session.add(product)
            result = product
        else:
            for field in (
                "barcode_type",
                "name",
                "brand",
                "categories",
                "quantity",
                "image_url",
                "ingredients",
                "allergens",
                "nutrition",
                "countries",
                "source",
                "source_id",
                "source_updated_at",
            ):
                setattr(existing, field, getattr(product, field))
            result = existing
        self.session.commit()
        self.session.refresh(result)
        return result

