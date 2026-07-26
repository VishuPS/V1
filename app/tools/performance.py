import argparse
import statistics
import time
from dataclasses import dataclass

from sqlalchemy import func, select

from app.barcodes import calculate_check_digit
from app.db import SessionLocal
from app.models import Product
from app.services import lookup_product


@dataclass(frozen=True, slots=True)
class LatencySummary:
    operations: int
    mean_ms: float
    median_ms: float
    p95_ms: float
    p99_ms: float


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * percentile_value)))
    return ordered[index]


def summarize(samples_ms: list[float]) -> LatencySummary:
    return LatencySummary(
        operations=len(samples_ms),
        mean_ms=statistics.fmean(samples_ms) if samples_ms else 0.0,
        median_ms=statistics.median(samples_ms) if samples_ms else 0.0,
        p95_ms=percentile(samples_ms, 0.95),
        p99_ms=percentile(samples_ms, 0.99),
    )


def valid_unknown(index: int) -> str:
    body = f"98{index:010d}"[-12:]
    return body + str(calculate_check_digit(body))


def timed_calls(callable_, inputs: list[object]) -> LatencySummary:
    samples: list[float] = []
    for value in inputs:
        started = time.perf_counter_ns()
        callable_(value)
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
    return summarize(samples)


def run_benchmark(iterations: int, batch_size: int) -> dict[str, LatencySummary]:
    if iterations < 1 or batch_size < 1:
        raise ValueError("iterations and batch_size must be at least 1")
    with SessionLocal() as session:
        known = list(session.scalars(select(Product.barcode).limit(max(1, iterations))))
        if not known:
            raise RuntimeError("Database has no products; import data before benchmarking")
        known_inputs = [known[index % len(known)] for index in range(iterations)]
        unknown_inputs = [valid_unknown(index) for index in range(iterations)]
        repeated_inputs = [known[0]] * iterations
        batch_inputs = [
            (known_inputs[index : index + batch_size] + unknown_inputs[index : index + batch_size])
            for index in range(0, iterations, batch_size)
        ]
        return {
            "known": timed_calls(lambda code: lookup_product(session, str(code)), known_inputs),
            "unknown": timed_calls(
                lambda code: lookup_product(session, str(code)), unknown_inputs
            ),
            "repeated": timed_calls(
                lambda code: lookup_product(session, str(code)), repeated_inputs
            ),
            "batch": timed_calls(
                lambda codes: [lookup_product(session, str(code)) for code in codes],
                batch_inputs,
            ),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark local product lookups")
    parser.add_argument("--iterations", type=int, default=1_000)
    parser.add_argument("--batch-size", type=int, default=50)
    args = parser.parse_args()
    with SessionLocal() as session:
        product_count = session.scalar(select(func.count()).select_from(Product)) or 0
    print(f"Database products: {product_count:,}")
    results = run_benchmark(args.iterations, args.batch_size)
    print("Latency in milliseconds (batch measures the complete batch request):")
    print("scenario        n       mean     median        p95        p99")
    for name, result in results.items():
        print(
            f"{name:<12} {result.operations:>5} "
            f"{result.mean_ms:>10.3f} {result.median_ms:>10.3f} "
            f"{result.p95_ms:>10.3f} {result.p99_ms:>10.3f}"
        )


if __name__ == "__main__":
    main()
