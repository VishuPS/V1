from __future__ import annotations

from app.barcodes import BarcodeError, parse_barcode


def normalize_isbn(value: str) -> tuple[str, str]:
    """Return (ISBN-13, canonical GTIN-14) without changing barcode API semantics."""
    compact = "".join(character for character in value.upper() if character not in " -")
    if len(compact) == 10:
        if not compact[:9].isdigit() or (not compact[-1].isdigit() and compact[-1] != "X"):
            raise ValueError("ISBN-10 is invalid")
        total = sum((10 - index) * (10 if digit == "X" else int(digit)) for index, digit in enumerate(compact))
        if total % 11:
            raise ValueError("ISBN-10 check digit is invalid")
        body = "978" + compact[:9]
        from app.barcodes import calculate_check_digit
        isbn13 = body + str(calculate_check_digit(body))
    elif len(compact) == 13:
        try:
            parsed = parse_barcode(compact)
        except BarcodeError as exc:
            raise ValueError(str(exc)) from exc
        if not compact.startswith(("978", "979")):
            raise ValueError("ISBN-13 must use the 978 or 979 prefix")
        isbn13 = parsed.value
    else:
        raise ValueError("ISBN must contain 10 or 13 characters")
    return isbn13, isbn13.zfill(14)
