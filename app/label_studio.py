"""Bounded label layout and rendering with preserved barcode quiet zones."""
import base64
from io import BytesIO
from math import floor
from pathlib import Path
from threading import Lock
from typing import Literal
from xml.etree.ElementTree import Element,SubElement,tostring
from defusedxml import ElementTree
from PIL import Image,ImageDraw,ImageFont
from pydantic import BaseModel,Field,model_validator
import reportlab
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.units import mm
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from app.barcode_tools import RenderRequest,render_symbol
from barcode.writer import PATH as BARCODE_PACKAGE_PATH

FONTS={"regular":"Vera.ttf","bold":"VeraBd.ttf","italic":"VeraIt.ttf"}
FONTS["mono"]=str(Path(BARCODE_PACKAGE_PATH)/"fonts"/"DejaVuSansMono.ttf")
FONT_ROOT=Path(reportlab.__file__).parent/"fonts"
FONT_LOCK=Lock()
_fonts_ready=False
DPI=300
PX=DPI/25.4


def prepare_fonts():
    global _fonts_ready
    with FONT_LOCK:
        if not _fonts_ready:
            for name,file in FONTS.items():
                pdfmetrics.registerFont(TTFont("Nest-"+name,str(FONT_ROOT/file)))
            _fonts_ready=True


class LabelElement(BaseModel):
    kind:Literal["barcode","qr","name","sku","price","text","logo"]
    x:float=Field(default=5,ge=0,le=210)
    y:float=Field(default=5,ge=0,le=300)
    w:float=Field(default=80,gt=0,le=210)
    h:float=Field(default=10,gt=0,le=300)
    text:str=Field(default="",max_length=200)
    font:Literal["regular","bold","italic","mono"]="regular"
    font_size:float=Field(default=10,ge=6,le=36)
    align:Literal["left","center","right"]="left"
    source:Literal["barcode","sku"]="barcode"
    symbology:Literal["auto","ean13","ean8","upca","code128","code39","qr"]="auto"


class LabelDefinition(BaseModel):
    width_mm:float=Field(default=100,ge=20,le=210)
    height_mm:float=Field(default=50,ge=10,le=300)
    layout:Literal["a4","thermal"]="a4"
    elements:list[LabelElement]=Field(min_length=1,max_length=30)
    logo_base64:str=Field(default="",max_length=350000)

    @model_validator(mode="after")
    def geometry(self):
        if self.width_mm*self.height_mm>40000:
            raise ValueError("The label area is too large")
        for element in self.elements:
            if element.x+element.w>self.width_mm+.001 or element.y+element.h>self.height_mm+.001:
                raise ValueError("Every element must fit inside the label dimensions")
        symbols=[item for item in self.elements if item.kind in {"barcode","qr"}]
        for symbol in symbols:
            for other in self.elements:
                if other is symbol:
                    continue
                overlaps=symbol.x<other.x+other.w and symbol.x+symbol.w>other.x and symbol.y<other.y+other.h and symbol.y+symbol.h>other.y
                if overlaps:
                    raise ValueError("Barcode frames, including their quiet zones, cannot overlap other elements")
        if self.layout=="a4" and (self.width_mm>206 or self.height_mm>293):
            raise ValueError("This label does not fit on A4 with 2 mm page margins")
        if self.logo_base64:
            try:
                data=base64.b64decode(self.logo_base64,validate=True)
                if len(data)>250000:
                    raise ValueError("Logo file is too large")
                with Image.open(BytesIO(data)) as source:
                    if source.format not in {"PNG","JPEG","WEBP"} or source.width*source.height>1000000:
                        raise ValueError("Use a PNG, JPEG or WebP logo of at most one megapixel")
                    source.thumbnail((512,512));image=source.convert("RGBA");image.info.clear()
                    output=BytesIO();image.save(output,format="PNG",optimize=True)
                    if len(output.getvalue())>250000:
                        raise ValueError("Use a smaller logo")
                    self.logo_base64=base64.b64encode(output.getvalue()).decode("ascii")
            except Exception as exc:
                raise ValueError("The logo could not be opened safely; use a small PNG, JPEG or WebP") from exc
        return self


def element_text(element,product):
    if element.kind=="text":
        value=element.text
    elif element.kind=="price":
        price=product.get("price_minor")
        value="" if price is None else f"{product.get('currency','USD')} {price/100:.2f}"
    else:
        value=str(product.get(element.kind) or "")
    if any(ord(c)<32 for c in value):
        raise ValueError("Label text must be on one line")
    try:
        value.encode("cp1252")
    except UnicodeEncodeError as exc:
        raise ValueError("This label font supports printable Latin text; shorten or replace unsupported text") from exc
    return value


def fitted_text(element,product):
    prepare_fonts();value=element_text(element,product)
    maximum=element.w*mm
    if pdfmetrics.stringWidth(value,"Nest-"+element.font,element.font_size)<=maximum:
        return value
    while value and pdfmetrics.stringWidth(value+"...","Nest-"+element.font,element.font_size)>maximum:
        value=value[:-1]
    return value+"..." if value else ""


def symbol_request(element,product,format):
    value=str(product.get(element.source) or "")
    if not value:
        raise ValueError(f"This product has no {element.source} value for its barcode element")
    kind="qr" if element.kind=="qr" else element.symbology
    if kind=="auto":
        kind="code128" if element.source=="sku" else product.get("barcode_type","code128")
    if kind not in {"ean13","ean8","upca","code128","code39","qr"}:
        raise ValueError("Choose a supported label symbol for this product")
    return RenderRequest(value=value,symbology=kind,format=format)


def barcode_image(element,product):
    image=Image.open(BytesIO(render_symbol(symbol_request(element,product,"png"),internal_label_height=element.h))).convert("RGB")
    factor=floor(min(element.w*PX/image.width,element.h*PX/image.height)+1e-8)
    if factor<1:
        raise ValueError(f"The barcode frame is too small. Allow at least {image.width/PX:.1f} × {image.height/PX:.1f} mm for this value")
    if factor>1:
        image=image.resize((image.width*factor,image.height*factor),Image.Resampling.NEAREST)
    return image


def logo_image(definition):
    if not definition.logo_base64:
        raise ValueError("Upload a logo or remove the logo element")
    return Image.open(BytesIO(base64.b64decode(definition.logo_base64))).convert("RGBA")


def render_png(definition:LabelDefinition,product:dict)->bytes:
    image=Image.new("RGB",(round(definition.width_mm*PX),round(definition.height_mm*PX)),"white")
    draw=ImageDraw.Draw(image)
    for element in definition.elements:
        x,y=round(element.x*PX),round(element.y*PX)
        w,h=round(element.w*PX),round(element.h*PX)
        if element.kind in {"barcode","qr"}:
            symbol=barcode_image(element,product)
            image.paste(symbol,(x+(w-symbol.width)//2,y+(h-symbol.height)//2))
        elif element.kind=="logo":
            logo=logo_image(definition);logo.thumbnail((w,h));image.paste(logo,(x+(w-logo.width)//2,y+(h-logo.height)//2),logo)
        else:
            if element.h*mm<element.font_size*1.2:
                raise ValueError("Increase the text element height for its selected font size")
            font=ImageFont.truetype(str(FONT_ROOT/FONTS[element.font]),round(element.font_size*DPI/72))
            value=fitted_text(element,product);length=draw.textlength(value,font=font)
            left=x if element.align=="left" else x+(w-length)/2 if element.align=="center" else x+w-length
            draw.text((left,y),value,font=font,fill="black")
    output=BytesIO();image.save(output,format="PNG",dpi=(DPI,DPI));return output.getvalue()


def render_svg(definition:LabelDefinition,product:dict)->bytes:
    root=Element("svg",{"xmlns":"http://www.w3.org/2000/svg","width":f"{definition.width_mm}mm","height":f"{definition.height_mm}mm","viewBox":f"0 0 {definition.width_mm} {definition.height_mm}"})
    fonts={element.font for element in definition.elements if element.kind not in {"barcode","qr","logo"}}
    if fonts:
        style=SubElement(root,"style")
        style.text="\n".join("@font-face{font-family:'Nest-"+font+"';font-weight:"+('bold' if font=='bold' else 'normal')+";font-style:"+('italic' if font=='italic' else 'normal')+";src:url(data:font/ttf;base64,"+base64.b64encode((FONT_ROOT/FONTS[font]).read_bytes()).decode("ascii")+") format('truetype');}" for font in fonts)
    SubElement(root,"rect",{"width":"100%","height":"100%","fill":"white"})
    for element in definition.elements:
        if element.kind in {"barcode","qr"}:
            symbol=ElementTree.fromstring(render_symbol(symbol_request(element,product,"svg"),internal_label_height=element.h))
            width=float(symbol.attrib["width"].removesuffix("mm"));height=float(symbol.attrib["height"].removesuffix("mm"))
            factor=floor(min(element.w/width,element.h/height)+1e-8)
            if factor<1:
                raise ValueError(f"The barcode frame needs at least {width:.1f} × {height:.1f} mm")
            if "viewBox" not in symbol.attrib:
                symbol.set("viewBox",f"0 0 {width} {height}")
            symbol.set("x",str(element.x+(element.w-width*factor)/2));symbol.set("y",str(element.y+(element.h-height*factor)/2))
            symbol.set("width",str(width*factor));symbol.set("height",str(height*factor));root.append(symbol)
        elif element.kind=="logo":
            logo_image(definition).close()
            SubElement(root,"image",{"x":str(element.x),"y":str(element.y),"width":str(element.w),"height":str(element.h),"href":"data:image/png;base64,"+definition.logo_base64,"preserveAspectRatio":"xMidYMid meet"})
        else:
            if element.h*mm<element.font_size*1.2:
                raise ValueError("Increase the text element height for its selected font size")
            point=element.x if element.align=="left" else element.x+element.w/2 if element.align=="center" else element.x+element.w
            text=SubElement(root,"text",{"x":str(point),"y":str(element.y+element.font_size*25.4/72),"font-family":"Nest-"+element.font+", sans-serif","font-size":str(element.font_size*25.4/72),"text-anchor":{"left":"start","center":"middle","right":"end"}[element.align],"fill":"black"})
            if element.font=="bold":
                text.set("font-weight","bold")
            if element.font=="italic":
                text.set("font-style","italic")
            text.text=fitted_text(element,product)
    return tostring(root,encoding="utf-8",xml_declaration=True)


def render_pdf(definition:LabelDefinition,products:list[dict])->bytes:
    prepare_fonts();output=BytesIO()
    if not 1<=len(products)<=100:
        raise ValueError("Print 1–100 products per PDF")
    width,height=definition.width_mm*mm,definition.height_mm*mm
    page_size=A4 if definition.layout=="a4" else (width,height)
    columns=max(1,int((page_size[0]-4*mm)//width)) if definition.layout=="a4" else 1
    rows=max(1,int((page_size[1]-4*mm)//height)) if definition.layout=="a4" else 1
    canvas=Canvas(output,pagesize=page_size)
    for index,product in enumerate(products):
        position=index%(columns*rows)
        if index and position==0:
            canvas.showPage()
        left=2*mm+(position%columns)*width if definition.layout=="a4" else 0
        top=page_size[1]-2*mm-(position//columns)*height if definition.layout=="a4" else height
        for element in definition.elements:
            x=left+element.x*mm;y=top-element.y*mm;w=element.w*mm;h=element.h*mm
            if element.kind in {"barcode","qr"}:
                symbol=barcode_image(element,product)
                sw,sh=symbol.width/DPI*72,symbol.height/DPI*72
                canvas.drawImage(ImageReader(symbol),x+(w-sw)/2,y-(h+sh)/2,width=sw,height=sh,mask="auto")
            elif element.kind=="logo":
                logo=logo_image(definition);factor=min(w/logo.width,h/logo.height)
                lw,lh=logo.width*factor,logo.height*factor
                canvas.drawImage(ImageReader(logo),x+(w-lw)/2,y-(h+lh)/2,width=lw,height=lh,mask="auto")
            else:
                if h<element.font_size*1.2:
                    raise ValueError("Increase the text element height for its selected font size")
                canvas.setFont("Nest-"+element.font,element.font_size)
                value=fitted_text(element,product);baseline=y-element.font_size
                if element.align=="left":
                    canvas.drawString(x,baseline,value)
                elif element.align=="center":
                    canvas.drawCentredString(x+w/2,baseline,value)
                else:
                    canvas.drawRightString(x+w,baseline,value)
    canvas.save();return output.getvalue()
