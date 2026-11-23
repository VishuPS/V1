import base64
from io import BytesIO

from PIL import Image
import pytest

from app.barcode_scanner import ScanRequest, read_scan_image, decode_scan_image
from app.barcode_tools import RenderRequest, render_symbol
from app.config import ServiceLimit, get_settings


def image_payload(image):
    output = BytesIO()
    image.save(output, format="PNG")
    return {"image_base64": base64.b64encode(output.getvalue()).decode()}


@pytest.mark.parametrize("kind,value", [("ean13","3017620422003"),("upca","012345678905"),("code128","SKU-002"),("qr","https://example.com/?a=<script>")])
@pytest.mark.parametrize("rotate", [0, 90])
def test_decode_images_and_preserve_values(kind, value, rotate):
    image = Image.open(BytesIO(render_symbol(RenderRequest(value=value,symbology=kind,format="png")))).rotate(rotate,expand=True)
    payload = ScanRequest(**image_payload(image))
    result = decode_scan_image(read_scan_image(payload))
    assert result["count"] == 1
    scanned = result["barcodes"][0]
    if kind in {"ean13","upca"}:
        assert scanned["value"].zfill(14) == value.zfill(14)
        assert scanned["gtin14"] == value.zfill(14)
    else:
        assert scanned["value"] == value
        assert scanned["gtin14"] is None


def test_invalid_images_rejected():
    for encoded in ["not base64!",base64.b64encode(b"not an image").decode()]:
        with pytest.raises(ValueError):
            read_scan_image(ScanRequest(image_base64=encoded))
    with pytest.raises(ValueError,match="12 megapixels"):
        read_scan_image(ScanRequest(**image_payload(Image.new("RGB",(4000,3100),"white"))))


@pytest.mark.parametrize("kind,value", [("EAN8","96385074"),("DataMatrix","WAREHOUSE-002")])
def test_additional_scan_formats(kind,value):
    import zxingcpp
    symbol=zxingcpp.create_barcode(value,getattr(zxingcpp.BarcodeFormat,kind))
    image=Image.fromarray(symbol.to_image(scale=5))
    results=decode_scan_image(read_scan_image(ScanRequest(**image_payload(image))))
    assert results["count"] == 1
    assert results["barcodes"][0]["value"] == value


def test_multiple_symbols_are_returned_once():
    first=Image.open(BytesIO(render_symbol(RenderRequest(value="FIRST",symbology="qr",format="png")))).convert("RGB")
    second=Image.open(BytesIO(render_symbol(RenderRequest(value="SECOND",symbology="qr",format="png")))).convert("RGB")
    composite=Image.new("RGB",(first.width+second.width+100,max(first.height,second.height)+40),"white")
    composite.paste(first,(0,20));composite.paste(second,(first.width+100,20))
    assert {x["value"] for x in decode_scan_image(composite)["barcodes"]} == {"FIRST","SECOND"}


def test_scan_attempts_have_separate_quota(client):
    from app.main import app
    settings = get_settings().model_copy(update={"service_limits":{"FREE":ServiceLimit(scans=2,generation=100,labels=25,enrichment=250)}})
    app.dependency_overrides[get_settings] = lambda: settings
    assert client.post("/v1/barcodes/scan",json={"image_base64":"bad"}).status_code == 422
    payload = image_payload(Image.new("RGB",(100,100),"white"))
    for _ in range(2):
        response = client.post("/v1/barcodes/scan",json=payload)
        assert response.status_code == 200
        assert response.json() == {"barcodes":[],"count":0}
        assert response.headers["x-service-category"] == "scans"
    assert client.post("/v1/barcodes/scan",json=payload).status_code == 429
    categories = client.get("/v1/services/usage").json()["categories"]
    assert categories["scans"]["used"] == 2
    assert categories["generation"]["used"] == 0
    assert categories["labels"]["used"] == 0


def test_scanner_requires_authentication(unauthenticated_client):
    payload = image_payload(Image.new("RGB",(100,100),"white"))
    assert unauthenticated_client.post("/v1/barcodes/scan",json=payload).status_code == 401
    assert unauthenticated_client.post("/v1/account/barcodes/scan",json=payload).status_code == 401


def test_account_scan_and_lookup_use_existing_account_rules(unauthenticated_client,session_factory):
    from tests.test_user_auth import create_account, login
    create_account(session_factory)
    token = login(unauthenticated_client).json()["access_token"]
    headers = {"Authorization":"Bearer "+token}
    payload = image_payload(Image.new("RGB",(100,100),"white"))
    response=unauthenticated_client.post("/v1/account/barcodes/scan",headers=headers,json=payload)
    assert response.status_code == 200
    assert unauthenticated_client.get("/v1/account/services/usage",headers=headers).json()["categories"]["scans"]["used"] == 1
    lookup=unauthenticated_client.get("/v1/account/products/3017620422003",headers=headers)
    assert lookup.status_code in {200,404}


def test_legacy_plan_configuration_receives_plan_specific_scan_allowances():
    from app.config import Settings
    settings=Settings(service_limits={"STARTER":{"generation":2,"labels":2,"enrichment":2}})
    assert settings.service_limits["STARTER"].scans == 2000
