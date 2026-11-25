"""Explicit operator task; never run a catalogue COUNT on visitor requests."""
import argparse
from datetime import datetime,timezone
from sqlalchemy import select,func,text
from app.db import SessionLocal
from app.models import Product,PlatformStatistic


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout-seconds",type=int,default=120)
    args=parser.parse_args()
    if not 1<=args.timeout_seconds<=300:
        parser.error("Choose a timeout between 1 and 300 seconds")
    with SessionLocal() as session:
        if session.get_bind().dialect.name=="postgresql":
            session.execute(text(f"SET LOCAL statement_timeout = '{args.timeout_seconds*1000}ms'"))
        value=session.scalar(select(func.count()).select_from(Product))
        snapshot=session.get(PlatformStatistic,"public_product_count")
        if snapshot is None:
            snapshot=PlatformStatistic(key="public_product_count");session.add(snapshot)
        snapshot.value=value;snapshot.verified_at=datetime.now(timezone.utc);snapshot.method="exact_count"
        session.commit()
        print(f"Verified public product records: {value:,}; checked {snapshot.verified_at.isoformat()}")


if __name__=="__main__":
    main()
