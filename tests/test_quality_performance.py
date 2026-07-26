from sqlalchemy.orm import Session, sessionmaker

from app.tools.data_quality import calculate_quality
from app.tools.performance import percentile, summarize, valid_unknown


def test_data_quality_metrics(session_factory: sessionmaker[Session]) -> None:
    stats = calculate_quality(session_factory=session_factory)
    assert stats.total_products == 2
    assert stats.completeness()["name"] == 100
    assert stats.completeness()["brand"] == 50
    assert stats.completeness()["image"] == 0
    assert stats.barcode_types["EAN-13"] == 1
    assert stats.barcode_types["UPC-A"] == 1


def test_latency_summary_and_unknown_gtin() -> None:
    summary = summarize([1.0, 2.0, 3.0, 100.0])
    assert summary.mean_ms == 26.5
    assert summary.median_ms == 2.5
    assert percentile([1.0, 2.0, 3.0, 100.0], 0.99) == 3.0
    assert len(valid_unknown(42)) == 13
