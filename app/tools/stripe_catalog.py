"""Create the canonical BarcodeNest Stripe products and recurring prices once."""

from app.billing import StripeClient
from app.config import get_settings


def main() -> None:
    client = StripeClient(get_settings())
    for plan, name, cents in (("STARTER", "BarcodeNest Starter", 999), ("GROWTH", "BarcodeNest Growth", 1999)):
        product_id = client.create_product(name, plan)
        price_id = client.create_monthly_price(product_id, cents, plan)
        print(f"STRIPE_{plan}_PRICE_ID={price_id}")


if __name__ == "__main__":
    main()
