from __future__ import annotations

import argparse
from pathlib import Path

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.ingestion.multi_source import import_mapped_records
from app.ingestion.open_facts_adapters import OPEN_FACTS_SOURCES, OpenFactsAdapter
from app.ingestion.open_food_facts import download_dataset
from app.ingestion.usda import (
    USDAFoodDataCentralAdapter,
    dataset_fingerprint,
    discover_latest_branded_csv_url,
    download_usda_archive,
)
from app.tools.product_sources import source_coverage_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download and synchronize BarcodeNest product sources")
    parser.add_argument("--source", choices=["usda", "beauty", "pet", "products", "all"], required=True)
    parser.add_argument("--source-file", type=Path, help="Use a local fixture/archive instead of downloading")
    parser.add_argument("--cache-dir", type=Path, default=Path("data/source-cache"))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--batch-size", type=int, default=get_settings().ingestion_batch_size)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--skip-nutrition", action="store_true", help="Skip USDA support-table nutrition join")
    return parser


def _run_usda(args) -> None:
    url = discover_latest_branded_csv_url() if args.source_file is None else None
    archive = args.source_file or download_usda_archive(url, args.cache_dir, force=args.force_download)
    # Small/dry-run checks stay fast; full imports build a disk-backed nutrition join.
    include_nutrition = not args.skip_nutrition and not args.dry_run and args.limit is None
    adapter = USDAFoodDataCentralAdapter(archive, include_nutrition=include_nutrition)
    stats = import_mapped_records(
        adapter.records(), SessionLocal, source="USDA_FDC", dataset_url=url,
        dataset_fingerprint=dataset_fingerprint(archive), batch_size=args.batch_size,
        limit=args.limit, dry_run=args.dry_run, resume=args.resume,
    )
    stats.invalid_barcodes += adapter.invalid_barcodes
    stats.skipped += adapter.skipped
    stats.errors += getattr(adapter, "errors", 0)
    stats.source_records_read = stats.processed + stats.invalid_barcodes + stats.skipped + stats.errors
    print(stats.report())


def _run_open_facts(args, key: str) -> None:
    definition = OPEN_FACTS_SOURCES[key]
    path = args.source_file or args.cache_dir / f"{key}-products.jsonl.gz"
    if args.source_file is None:
        download_dataset(definition.dataset_url, path, force=args.force_download)
    adapter = OpenFactsAdapter(path, definition)
    stats = import_mapped_records(
        adapter.records(), SessionLocal, source=definition.source,
        dataset_url=definition.dataset_url, dataset_fingerprint=dataset_fingerprint(path),
        batch_size=args.batch_size, limit=args.limit, dry_run=args.dry_run, resume=args.resume,
    )
    stats.invalid_barcodes += adapter.invalid_barcodes
    stats.skipped += adapter.skipped
    stats.errors += adapter.errors
    stats.source_records_read = stats.processed + stats.invalid_barcodes + stats.skipped + stats.errors
    print(stats.report())


def main() -> None:
    args = build_parser().parse_args()
    if not args.dry_run:
        init_db()
    sources = ["usda", "beauty", "pet", "products"] if args.source == "all" else [args.source]
    if args.source_file and len(sources) != 1:
        raise SystemExit("--source-file can only be used with one explicit source")
    for source in sources:
        _run_usda(args) if source == "usda" else _run_open_facts(args, source)
    if args.stats and not args.dry_run:
        with SessionLocal() as session:
            print(source_coverage_report(session))
    elif args.stats:
        print("Coverage stats skipped: dry run does not connect to the database.")


if __name__ == "__main__":
    main()
