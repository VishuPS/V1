import argparse
from pathlib import Path

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.ingestion.open_food_facts import (
    OpenFoodFactsSource,
    download_dataset,
    import_dataset,
    update_images_only,
)


DEFAULT_SAMPLE = Path(__file__).resolve().parents[2] / "data" / "sample_openfoodfacts.json"


def build_parser() -> argparse.ArgumentParser:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Stream Open Food Facts records into the local product database"
    )
    parser.add_argument(
        "--source-file",
        type=Path,
        default=DEFAULT_SAMPLE,
        help="Local .jsonl, .jsonl.gz, .jsonl.bz2, .jsonl.xz, or small .json file",
    )
    parser.add_argument("--limit", type=int, help="Maximum source records to process")
    parser.add_argument(
        "--batch-size", type=int, default=settings.ingestion_batch_size
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Explicitly download the official JSONL gzip export first",
    )
    parser.add_argument(
        "--dataset-url",
        default=settings.open_food_facts_dataset_url,
        help="Dataset URL used only with --download",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Replace an existing destination and discard any partial download",
    )
    parser.add_argument(
        "--download-only",
        action="store_true",
        help="Download/validate the source file and exit without importing",
    )
    parser.add_argument(
        "--update-images-only",
        action="store_true",
        help="Only update image_url for matching products already in the database",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.download:
        print(f"Downloading {args.dataset_url} to {args.source_file} ...")
        download_dataset(
            args.dataset_url,
            args.source_file,
            force=args.force_download,
        )
    if args.download_only:
        if not args.source_file.exists():
            raise SystemExit("--download-only requires an existing file or --download")
        print(f"Download-only complete: {args.source_file}")
        return
    if not args.source_file.exists():
        raise SystemExit(
            f"Dataset not found: {args.source_file}. Supply --source-file or --download."
        )
    init_db()
    if args.update_images_only:
        stats = update_images_only(
            OpenFoodFactsSource(args.source_file),
            SessionLocal,
            limit=args.limit,
            batch_size=args.batch_size,
        )
    else:
        stats = import_dataset(
            OpenFoodFactsSource(args.source_file),
            SessionLocal,
            limit=args.limit,
            batch_size=args.batch_size,
        )
    print(stats.report())


if __name__ == "__main__":
    main()
