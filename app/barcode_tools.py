import csv
from zipfile import ZIP_DEFLATED, ZipFile
from io import BytesIO, StringIO
from typing import Literal

import barcode
import qrcode
import qrcode.image.svg
from barcode.writer import ImageWriter, SVGWriter
from pydantic import BaseModel, Field, field_validator
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from app.barcodes import parse_barcode
from app.retail_barcodes import retail_png, retail_svg

Symbology = Literal["ean13", "ean8", "upca", "code128", "code39", "qr"]


class RenderRequest(BaseModel):
    value: str = Field(min_length=1, max_length=500)
    symbology: Symbology = "ean13"
    format: Literal["svg", "png"] = "svg"

    @field_validator("value")
    @classmethod
    def check_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A value is required")
        return value


class BatchRenderRequest(BaseModel):
    items: list[RenderRequest] = Field(min_length=1, max_length=100)


class ValidationRequest(BaseModel):
    value: str = Field(max_length=64)


def validate_symbol(value: str, symbology: Symbology) -> None:
    if symbology in {"ean13", "ean8", "upca"}:
        parsed = parse_barcode(value)
        length = {"ean13":13,"ean8":8,"upca":12}[symbology]
        if len(value) != length or parsed.value != value:
            raise ValueError(f"{symbology} requires exactly {length} digits including the correct check digit")
    elif symbology == "code128":
        if not 1 <= len(value) <= 80 or any(not 32 <= ord(char) <= 126 for char in value):
            raise ValueError("Code 128 accepts 1 to 80 printable ASCII characters")
    elif symbology == "code39":
        if not 1<=len(value)<=40 or any(char not in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-. $/+%" for char in value):
            raise ValueError("Code 39 accepts up to 40 uppercase letters, digits, spaces and -. $/+%; no optional Mod-43 digit is added")


def render_symbol(request: RenderRequest, *, internal_label_height:float|None=None) -> bytes:
    validate_symbol(request.value, request.symbology)
    if request.symbology in {"ean13", "ean8", "upca"}:
        renderer = retail_svg if request.format == "svg" else retail_png
        return renderer(request.value, request.symbology)
    output = BytesIO()
    if request.symbology == "qr":
        factory = qrcode.image.svg.SvgPathFillImage if request.format == "svg" else None
        image = qrcode.make(request.value, image_factory=factory, box_size=8, border=4)
        if request.format == "png":
            image.save(output, format="PNG", dpi=(300, 300))
        else:
            image.save(output)
    else:
        writer = SVGWriter() if request.format == "svg" else ImageWriter()
        symbol = barcode.get(request.symbology, request.value, writer=writer,options={"add_checksum":False} if request.symbology=="code39" else None)
        module_width = 0.33 if request.format == "svg" else 4 * 25.4 / 300
        options = {"module_width": module_width, "module_height": 22,
                   "quiet_zone": 6.5, "font_size": 10, "dpi": 300}
        if internal_label_height is not None and request.symbology in {"code128","code39"}:
            # Internal labels can use shorter bars; narrow bars and >=10X quiet zones stay intact.
            options.update(module_height=max(5,min(22,internal_label_height-7)),quiet_zone=10*module_width,font_size=8,text_distance=2)
        if request.format == "png":
            symbol.render(options).save(output, format="PNG", dpi=(300, 300))
        else:
            symbol.write(output, options)
    return output.getvalue()


def render_archive(request: BatchRenderRequest) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for index, item in enumerate(request.items, start=1):
            archive.writestr(f"barcode-{index:03d}.{item.format}", render_symbol(item))
    return output.getvalue()


class LabelItem(BaseModel):
    value: str = Field(min_length=1, max_length=80)
    symbology: Symbology = "ean13"
    title: str = Field(default="", max_length=70)
    detail: str = Field(default="", max_length=70)
    copies: int = Field(default=1, ge=1, le=100)

    @field_validator("title", "detail")
    @classmethod
    def check_label_text(cls, value: str) -> str:
        try:
            value.encode("cp1252")
        except UnicodeEncodeError as exc:
            raise ValueError("Label text must use Latin characters supported by the PDF font") from exc
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Label text must be on one line without control characters")
        return value


class LabelsRequest(BaseModel):
    items: list[LabelItem] = Field(min_length=1, max_length=100)
    paper: Literal["a4", "letter"] = "a4"


def fit_text(canvas: Canvas, value: str, width: float) -> str:
    if canvas.stringWidth(value, "Helvetica", 9) <= width:
        return value
    while value and canvas.stringWidth(value, "Helvetica", 9) > width:
        value = value[:-1]
    while value and canvas.stringWidth(value + "...", "Helvetica", 9) > width:
        value = value[:-1]
    return value + "..."


def render_labels(request: LabelsRequest) -> tuple[bytes, int]:
    count = sum(item.copies for item in request.items)
    if count > 100:
        raise ValueError("A document can contain at most 100 labels including copies")
    size = A4 if request.paper == "a4" else letter
    width, height = size
    margin, gap = 24, 12
    cell_width = (width - margin * 2 - gap) / 2
    cell_height = (height - margin * 2) / 5
    output = BytesIO()
    canvas = Canvas(output, pagesize=size, pageCompression=1)
    canvas.setTitle("BarcodeNest labels")
    index = 0
    for item in request.items:
        image_bytes = render_symbol(RenderRequest(value=item.value, symbology=item.symbology, format="png"))
        image = ImageReader(BytesIO(image_bytes))
        pixel_width, pixel_height = image.getSize()
        scale = min((cell_width - 16) / pixel_width, (cell_height - 42) / pixel_height, 72 / 300)
        # Avoid shrinking a long Code 128 beyond a useful printable module size.
        if item.symbology == "code128" and scale * 300 / 72 < 0.6:
            raise ValueError("Code 128 value is too long for this label template")
        for _ in range(item.copies):
            if index and index % 10 == 0:
                canvas.showPage()
            column = index % 2
            row = (index % 10) // 2
            x = margin + column * (cell_width + gap)
            y = height - margin - (row + 1) * cell_height
            canvas.setFont("Helvetica", 9)
            canvas.drawString(x + 8, y + cell_height - 14, fit_text(canvas, item.title, cell_width - 16))
            canvas.drawImage(image, x + (cell_width - pixel_width * scale) / 2, y + 20,
                             width=pixel_width * scale, height=pixel_height * scale)
            canvas.drawString(x + 8, y + 7, fit_text(canvas, item.detail, cell_width - 16))
            index += 1
    canvas.save()
    return output.getvalue(), count


class CsvRequest(BaseModel):
    csv_text: str = Field(min_length=1, max_length=500_000)


OUTPUT_FIELDS = ["lookup_status", "lookup_name", "lookup_brand", "lookup_quantity", "lookup_image_url"]


def parse_csv(csv_text: str) -> tuple[list[str], list[dict]]:
    reader = csv.DictReader(StringIO(csv_text.lstrip("\ufeff")), strict=True)
    fields = reader.fieldnames or []
    if "barcode" not in fields or len(fields) != len(set(fields)):
        raise ValueError("CSV requires unique column names and a barcode column")
    if any(not field or len(field) > 128 for field in fields) or len(fields) > 50:
        raise ValueError("Use at most 50 named columns of up to 128 characters")
    if set(fields).intersection(OUTPUT_FIELDS):
        raise ValueError("Input already contains enrichment output columns")
    rows = []
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError("Every row must have the same number of columns as the header")
        rows.append(row)
        if len(rows) > 100:
            raise ValueError("Use at most 100 rows per CSV request")
    if not rows:
        raise ValueError("CSV must contain at least one data row")
    return fields, rows


def csv_cell(value: str) -> str:
    # CSV quoting alone does not stop spreadsheet formula evaluation.
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")) else value


def enriched_csv(fields: list[str], rows: list[dict], results: list) -> str:
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields + OUTPUT_FIELDS)
    writer.writerow({field: csv_cell(field) for field in fields + OUTPUT_FIELDS})
    for row, result in zip(rows, results, strict=True):
        product = result.product
        enriched = {
            **row,
            "lookup_status": "invalid" if not result.valid else "found" if result.found else "not_found",
            "lookup_name": product.name if product else "",
            "lookup_brand": (product.brand or "") if product else "",
            "lookup_quantity": (product.quantity or "") if product else "",
            "lookup_image_url": (product.image_url or "") if product else "",
        }
        writer.writerow({key: csv_cell(value) for key, value in enriched.items()})
    return output.getvalue()
