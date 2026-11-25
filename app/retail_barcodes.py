"""Retail EAN/UPC geometry with GS1 guard extensions and readable digits.

Uses python-barcode for the encoded module pattern. Physical print quality
requires verification on the final printed label.
"""
from dataclasses import dataclass
from io import BytesIO
from math import ceil
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring

import barcode
from barcode.writer import PATH as BARCODE_PACKAGE_PATH
from PIL import Image, ImageDraw, ImageFont

MODULE_WIDTH_MM = 0.33
BAR_HEIGHT_MM = 22.85
QUIET_ZONE_MM = 6.5
GUARD_EXTENSION_MODULES = 5
DPI = 300
FONT_SIZE_PT = 9


@dataclass
class Bar:
    start: int
    width: int
    extended: bool


def retail_bars(value: str, symbology: str) -> list[Bar]:
    pattern = barcode.get(symbology, value).build()[0]
    expected=67 if symbology=="ean8" else 95
    if len(pattern) != expected or set(pattern) != {"0", "1"}:
        raise ValueError("Unexpected retail barcode pattern")
    bars = []
    for index, bit in enumerate(pattern):
        if bit == "0":
            continue
        extended = (index<3 or 31<=index<36 or index>=64) if symbology=="ean8" else (index < 3 or 45 <= index < 50 or index >= 92)
        if symbology == "upca":
            extended = extended or 3 <= index < 10 or 85 <= index < 92
        if bars and bars[-1].start + bars[-1].width == index and bars[-1].extended == extended:
            bars[-1].width += 1
        else:
            bars.append(Bar(index, 1, extended))
    return bars


def digit_positions(value: str, symbology: str) -> list[tuple[str, float]]:
    if symbology=="ean8":
        return [(digit,6.5+index*7) for index,digit in enumerate(value[:4])]+[(digit,39.5+index*7) for index,digit in enumerate(value[4:])]
    if symbology == "ean13":
        positions = [(value[0], -4)]
        positions.extend((digit, 6.5 + index * 7) for index, digit in enumerate(value[1:7]))
        positions.extend((digit, 53.5 + index * 7) for index, digit in enumerate(value[7:]))
        return positions
    positions = [(value[0], -5)]
    positions.extend((value[index], 6.5 + index * 7) for index in range(1, 6))
    positions.extend((value[index], 53.5 + (index - 6) * 7) for index in range(6, 11))
    positions.append((value[-1], 100))
    return positions


def retail_svg(value: str, symbology: str) -> bytes:
    width = (67 if symbology=="ean8" else 95) * MODULE_WIDTH_MM + 2 * QUIET_ZONE_MM
    baseline = 1 + BAR_HEIGHT_MM + 3
    height = baseline + 1
    svg = Element("svg", {
        "xmlns": "http://www.w3.org/2000/svg", "width": f"{width:.3f}mm",
        "height": f"{height:.3f}mm", "viewBox": f"0 0 {width:.3f} {height:.3f}",
    })
    SubElement(svg, "rect", {"width": f"{width:.3f}", "height": f"{height:.3f}", "fill": "white"})
    for bar in retail_bars(value, symbology):
        bar_height = BAR_HEIGHT_MM + (GUARD_EXTENSION_MODULES * MODULE_WIDTH_MM if bar.extended else 0)
        SubElement(svg, "rect", {
            "x": f"{QUIET_ZONE_MM + bar.start * MODULE_WIDTH_MM:.3f}", "y": "1",
            "width": f"{bar.width * MODULE_WIDTH_MM:.3f}", "height": f"{bar_height:.3f}", "fill": "black",
        })
    for digit, module_position in digit_positions(value, symbology):
        text = SubElement(svg, "text", {
            "x": f"{QUIET_ZONE_MM + module_position * MODULE_WIDTH_MM:.3f}",
            "y": f"{baseline:.3f}", "font-size": f"{FONT_SIZE_PT * 25.4 / 72:.3f}",
            "font-family": "DejaVu Sans Mono, monospace", "text-anchor": "middle", "fill": "black",
        })
        text.text = digit
    return tostring(svg, encoding="utf-8", xml_declaration=True)


def retail_png(value: str, symbology: str) -> bytes:
    # Whole-pixel modules avoid alternating narrow-bar widths in raster output.
    pixels_per_mm = DPI / 25.4
    module = round(MODULE_WIDTH_MM * pixels_per_mm)
    quiet = ceil(QUIET_ZONE_MM * pixels_per_mm)
    top = ceil(pixels_per_mm)
    bar_height = ceil(BAR_HEIGHT_MM * pixels_per_mm)
    baseline = top + bar_height + ceil(3 * pixels_per_mm)
    image = Image.new("RGB", ((67 if symbology=="ean8" else 95) * module + 2 * quiet, baseline + top), "white")
    draw = ImageDraw.Draw(image)
    for bar in retail_bars(value, symbology):
        height = bar_height + (GUARD_EXTENSION_MODULES * module if bar.extended else 0)
        left = quiet + bar.start * module
        draw.rectangle((left, top, left + bar.width * module - 1, top + height - 1), fill="black")
    font_path = Path(BARCODE_PACKAGE_PATH) / "fonts" / "DejaVuSansMono.ttf"
    font = ImageFont.truetype(str(font_path), round(FONT_SIZE_PT * DPI / 72))
    for digit, module_position in digit_positions(value, symbology):
        draw.text((quiet + module_position * module, baseline), digit, font=font, fill="black", anchor="ms")
    output = BytesIO()
    image.save(output, format="PNG", dpi=(DPI, DPI))
    return output.getvalue()
