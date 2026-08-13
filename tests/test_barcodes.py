import pytest

from app.barcodes import (
    BarcodeError,
    calculate_check_digit,
    detect_barcode_type,
    equivalent_barcodes,
    normalize_barcode,
    normalize_gtin,
    parse_barcode,
)


@pytest.mark.parametrize(
    ("value", "expected_type"),
    [
        ("96385074", "EAN-8"),
        ("012345678905", "UPC-A"),
        ("3017620422003", "EAN-13"),
        ("10012345678902", "GTIN-14"),
    ],
)
def test_supported_valid_barcodes(value: str, expected_type: str) -> None:
    barcode = parse_barcode(value)
    assert barcode.value == value
    assert barcode.barcode_type == expected_type
    assert detect_barcode_type(value) == expected_type


@pytest.mark.parametrize(
    "value",
    ["", "123", "3017620422004", "30176204220A3", "  ", "１２３４５６７８"],
)
def test_invalid_barcodes(value: str) -> None:
    with pytest.raises(BarcodeError):
        parse_barcode(value)


def test_normalization_strips_surrounding_whitespace_only() -> None:
    assert normalize_barcode(" 3017620422003 ") == "3017620422003"


def test_upc_and_ean_equivalence() -> None:
    assert equivalent_barcodes("012345678905") == (
        "012345678905",
        "00012345678905",
        "0012345678905",
    )
    assert "012345678905" in equivalent_barcodes("0012345678905")
    assert "012345678905" in equivalent_barcodes("00012345678905")
    assert normalize_gtin("012345678905") == "00012345678905"
    assert normalize_gtin("0012345678905") == "00012345678905"


def test_calculate_check_digit() -> None:
    assert calculate_check_digit("301762042200") == 3
