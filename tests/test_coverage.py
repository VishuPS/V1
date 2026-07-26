from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.tools.coverage import (
    calculate_coverage,
    load_benchmark,
    read_barcodes,
    read_benchmark,
    write_json_summary,
    write_report,
)


def test_read_text_or_csv_barcodes(tmp_path: Path) -> None:
    path = tmp_path / "input.csv"
    path.write_text(
        "barcode,description\n3017620422003,Nutella\n4006381333931,Unknown\n",
        encoding="utf-8",
    )
    assert read_barcodes(path) == ["3017620422003", "4006381333931"]


def test_coverage_calculation_and_csv_report(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    with session_factory() as session:
        stats, rows = calculate_coverage(
            session, ["3017620422003", "4006381333931", "123"]
        )
    assert stats.total == 3
    assert stats.valid == 2
    assert stats.found == 1
    assert stats.not_found == 1
    assert stats.percent(stats.found, stats.valid) == 50
    assert stats.name_complete == 1
    assert stats.brand_complete == 1
    assert stats.image_complete == 0
    output = tmp_path / "coverage.csv"
    write_report(output, rows)
    assert "product_name" in output.read_text(encoding="utf-8")


def test_benchmark_metadata_breakdowns_and_json(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    path = tmp_path / "benchmark.csv"
    path.write_text(
        "barcode,expected_product_name,expected_brand,country,category,source_of_barcode\n"
        "3017620422003,Nutella,Ferrero,Portugal,confectionery,shop observation\n"
        "4006381333931,Unknown,,UK,beverages,shop observation\n"
        "123,,,Portugal,dairy,shop observation\n",
        encoding="utf-8",
    )
    benchmark = read_benchmark(path)
    with session_factory() as session:
        stats, _ = calculate_coverage(session, benchmark)
    assert benchmark[0].country == "Portugal"
    assert stats.invalid == 1
    assert stats.breakdowns["country"]["Portugal"].tested == 1
    assert stats.breakdowns["country"]["Portugal"].hit_rate == 100
    assert stats.breakdowns["country"]["UK"].hit_rate == 0
    assert stats.breakdowns["category"]["confectionery"].found == 1
    assert stats.breakdowns["barcode_type"]["EAN-13"].tested == 2
    output = tmp_path / "summary.json"
    write_json_summary(output, stats)
    payload = output.read_text(encoding="utf-8")
    assert '"hit_rate": 50.0' in payload
    assert '"database_products": 2' in payload
    assert stats.readiness_signal() == "commercial coverage cannot yet be classified"
    with session_factory() as session:
        independent, _ = calculate_coverage(
            session, benchmark, independent_benchmark=True
        )
    assert independent.readiness_signal().startswith("coverage needs improvement")


def test_duplicate_equivalent_barcodes_and_malformed_rows_are_reported(
    tmp_path: Path,
) -> None:
    path = tmp_path / "duplicates.csv"
    path.write_text(
        "barcode,country,notes\n"
        "012345678905,Portugal,first\n"
        "00012345678905,Portugal,equivalent duplicate\n"
        ",UK,missing barcode\n"
        "3017620422003,France,valid,unexpected extra cell\n",
        encoding="utf-8",
    )
    loaded = load_benchmark(path)
    assert loaded.stats.input_rows == 4
    assert loaded.stats.accepted_rows == 1
    assert loaded.stats.duplicate_rows == 1
    assert loaded.stats.malformed_rows == 2
    assert loaded.rows[0].barcode == "012345678905"
