"""Create the canonical BarcodeNest Stripe products and recurring prices once."""

from app.billing import StripeClient
from app.config import get_settings


def main() -> None:
    client = StripeClient(get_settings())
    for plan, name, monthly_cents, annual_cents in (("STARTER", "BarcodeNest Starter", 999, 9590), ("GROWTH", "BarcodeNest Growth", 1999, 19190)):
        product_id = client.create_product(name, plan)
        monthly_id = client.create_recurring_price(product_id, monthly_cents, plan, "month")
        annual_id = client.create_recurring_price(product_id, annual_cents, plan, "year")
        print(f"STRIPE_{plan}_PRICE_ID={monthly_id}")
        print(f"STRIPE_{plan}_ANNUAL_PRICE_ID={annual_id}")


if __name__ == "__main__":
    main()
