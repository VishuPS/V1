"""Non-persistent live integration check for fallback providers."""
import argparse
import time

from app.barcodes import parse_barcode
from app.config import get_settings
from app.fallbacks import OpenFactsFallback, UPCItemDBFallback, UrllibTransport


def main() -> None:
    parser = argparse.ArgumentParser(description="Check fallback providers without writing a database")
    parser.add_argument("gtins", nargs="+")
    args = parser.parse_args()
    settings = get_settings()
    transport = UrllibTransport()
    providers = [OpenFactsFallback(settings, transport), UPCItemDBFallback(settings, transport)]
    for position, raw in enumerate(args.gtins):
        gtin = parse_barcode(raw).gtin14
        print(f"GTIN {gtin}")
        for provider in providers:
            started = time.perf_counter()
            try:
                result = provider.lookup(gtin)
            except Exception as exc:
                print(f"  {provider.name}: unavailable ({type(exc).__name__})")
                continue
            elapsed = (time.perf_counter() - started) * 1000
            if result.candidate:
                item = result.candidate.mapped
                print(f"  {provider.name}: found title={item.name!r} brand={item.brand!r} categories={item.categories!r} persist_allowed={result.candidate.persist_allowed} latency_ms={elapsed:.1f}")
                break
            print(f"  {provider.name}: {result.status} detail={result.detail or '-'} latency_ms={elapsed:.1f}")
        if position < len(args.gtins) - 1 and settings.upcitemdb_enabled:
            time.sleep(settings.upcitemdb_min_interval_seconds)


if __name__ == "__main__":
    main()
