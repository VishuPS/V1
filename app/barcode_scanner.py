"""Bounded in-memory barcode image decoding. Images are never persisted."""
import base64
import binascii
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field
import zxingcpp

from app.barcodes import BarcodeError, parse_barcode

MAX_BYTES = 4 * 1024 * 1024
MAX_PIXELS = 12_000_000


class ScanRequest(BaseModel):
    image_base64: str = Field(min_length=1, max_length=5_592_408)


def read_scan_image(payload: ScanRequest) -> Image.Image:
    try:
        data = base64.b64decode(payload.image_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Provide a base64-encoded PNG, JPEG or WebP image") from exc
    if len(data) > MAX_BYTES:
        raise ValueError("Image must be 4 MiB or smaller")
    try:
        with Image.open(BytesIO(data)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError("Use a PNG, JPEG or WebP image")
            if source.width * source.height > MAX_PIXELS:
                raise ValueError("Image must contain no more than 12 megapixels")
            source.load()
            oriented = ImageOps.exif_transpose(source)
            # Flatten transparent backgrounds onto white rather than black.
            rgba = oriented.convert("RGBA")
            image = Image.new("RGB", rgba.size, "white")
            image.paste(rgba, mask=rgba.getchannel("A"))
            image.thumbnail((2400, 2400))
            return image
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("The image could not be opened. Try another PNG, JPEG or WebP file") from exc


def decode_scan_image(image: Image.Image) -> dict:
    formats = (zxingcpp.BarcodeFormat.EAN13, zxingcpp.BarcodeFormat.EAN8,
               zxingcpp.BarcodeFormat.UPCA, zxingcpp.BarcodeFormat.Code128,
               zxingcpp.BarcodeFormat.QRCode, zxingcpp.BarcodeFormat.DataMatrix)
    results = []
    seen = set()
    for item in zxingcpp.read_barcodes(image, formats=formats):
        if not item.valid or not item.text or (str(item.format), item.text) in seen:
            continue
        seen.add((str(item.format), item.text))
        gtin = None
        if item.format in {zxingcpp.BarcodeFormat.EAN13, zxingcpp.BarcodeFormat.EAN8, zxingcpp.BarcodeFormat.UPCA}:
            try:
                gtin = parse_barcode(item.text).gtin14
            except BarcodeError:
                pass
        results.append({"value": item.text, "format": str(item.format), "gtin14": gtin})
        if len(results) == 20:
            break
    return {"barcodes": results, "count": len(results)}
