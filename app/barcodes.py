from dataclasses import dataclass


SUPPORTED_LENGTHS = {8: "EAN-8", 12: "UPC-A", 13: "EAN-13", 14: "GTIN-14"}


class BarcodeError(ValueError):
    """Raised when a barcode cannot be accepted as a supported GTIN."""


@dataclass(frozen=True, slots=True)
class Barcode:
    value: str
    barcode_type: str

    @property
    def gtin14(self) -> str:
        return self.value.zfill(14)

    @property
    def equivalents(self) -> tuple[str, ...]:
        values = [self.value, self.gtin14]
        if len(self.value) == 14 and self.value.startswith("0"):
            values.append(self.value[1:])
            if self.value.startswith("00"):
                values.append(self.value[2:])
        elif len(self.value) == 12:
            values.append(f"0{self.value}")
        elif len(self.value) == 13 and self.value.startswith("0"):
            values.append(self.value[1:])
        return tuple(dict.fromkeys(values))


def calculate_check_digit(body: str) -> int:
    if not body or not body.isascii() or not body.isdigit():
        raise BarcodeError("Barcode body must contain ASCII digits only")
    weighted_sum = sum(
        int(digit) * (3 if offset % 2 == 0 else 1)
        for offset, digit in enumerate(reversed(body))
    )
    return (10 - weighted_sum % 10) % 10


def parse_barcode(raw: str) -> Barcode:
    value = raw.strip() if isinstance(raw, str) else ""
    if not value:
        raise BarcodeError("Barcode is required")
    if not value.isascii() or not value.isdigit():
        raise BarcodeError("Barcode must contain ASCII digits only")
    barcode_type = SUPPORTED_LENGTHS.get(len(value))
    if barcode_type is None:
        raise BarcodeError("Barcode must be 8, 12, 13, or 14 digits long")
    if calculate_check_digit(value[:-1]) != int(value[-1]):
        raise BarcodeError("Barcode check digit is invalid")
    return Barcode(value=value, barcode_type=barcode_type)


def normalize_barcode(raw: str) -> str:
    return parse_barcode(raw).value


def normalize_gtin(raw: str) -> str:
    """Validate any supported UPC/EAN/GTIN representation and return GTIN-14."""
    return parse_barcode(raw).gtin14


def detect_barcode_type(raw: str) -> str:
    return parse_barcode(raw).barcode_type


def equivalent_barcodes(raw: str) -> tuple[str, ...]:
    return parse_barcode(raw).equivalents
