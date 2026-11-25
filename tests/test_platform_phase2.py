import base64
import csv
from io import BytesIO,StringIO
from datetime import datetime,timezone
from zipfile import ZipFile,ZIP_DEFLATED
from PIL import Image
import pytest
import zxingcpp
from openpyxl import Workbook,load_workbook
from sqlalchemy import select
from app.config import get_settings
from app.models import Subscription,SubscriptionPlan,ServiceUsage,VaultProduct,SkuAllocation
from app.workspace_io import TableUpload,read_table,table_response
from app.label_studio import LabelDefinition,render_png,render_svg,render_pdf
from tests.test_user_auth import create_account,login


def account(client,session_factory,email="phase2@example.com",plan="CREATOR"):
    uid,_=create_account(session_factory,email)
    if plan!="FREE":
        with session_factory() as session:
            if not session.get(SubscriptionPlan,plan):
                session.add(SubscriptionPlan(code=plan,name=plan,monthly_lookups=0 if plan=="CREATOR" else 2000,requests_per_minute=120));session.flush()
            sub=session.scalar(select(Subscription).where(Subscription.user_id==uid));sub.plan_code=plan;session.commit()
    token=login(client,email).json()["access_token"]
    return uid,{"Authorization":"Bearer "+token}


def upload(text):
    return {"format":"csv","data_base64":base64.b64encode(text.encode()).decode()}


def product(client,headers,barcode="SKU-001",**extra):
    response=client.post("/v1/account/vault",headers=headers,json={"barcode":barcode,"name":"Storage box",**extra})
    assert response.status_code==200,response.text
    return response.json()["item"]


def definition(kind="barcode"):
    return {"width_mm":100,"height_mm":50,"layout":"a4","elements":[{"kind":"name","x":5,"y":3,"w":90,"h":8,"font_size":12,"font":"bold"},{"kind":kind,"x":5,"y":15,"w":90,"h":32}]}


def test_sku_sequences_unique_and_associated(unauthenticated_client,session_factory):
    client=unauthenticated_client;uid,headers=account(client,session_factory)
    item=product(client,headers)
    body={"prefix":"ELEC","category_code":"PHN","pattern":"{prefix}-{category}-{number:06}","quantity":1,"product_ids":[item["id"]]}
    first=client.post("/v1/account/skus/generate",headers=headers,json=body)
    assert first.status_code==200,first.text
    assert first.json()["items"][0]["code"]=="ELEC-PHN-000001"
    second=client.post("/v1/account/skus/generate",headers=headers,json={**body,"quantity":2,"product_ids":[]})
    assert [item["code"] for item in second.json()["items"]]==["ELEC-PHN-000002","ELEC-PHN-000003"]
    saved=client.get("/v1/account/vault/"+item["id"],headers=headers).json()
    assert saved["sku"]=="ELEC-PHN-000001"
    assert client.post("/v1/account/skus/generate",headers=headers,json=body).status_code==409
    assert client.post("/v1/account/skus/generate",headers=headers,json={"pattern":"{number.__class__}"}).status_code==422
    with session_factory() as session:
        assert session.scalar(select(ServiceUsage.units).where(ServiceUsage.account_scope=="user:"+uid,ServiceUsage.category=="sku_generation"))==3
    _,other=account(client,session_factory,"other-phase2@example.com")
    assert client.put("/v1/account/skus/"+first.json()["items"][0]["id"]+"/product",headers=other,json={"product_id":item["id"]}).status_code==404


def test_sku_import_reports_duplicates_and_exports_literal_excel(unauthenticated_client,session_factory):
    client=unauthenticated_client;_,headers=account(client,session_factory)
    response=client.post("/v1/account/skus/import",headers=headers,json=upload("code,category\nBOX-001,Boxes\nBOX-001,Boxes\nbad sku,Boxes\n"))
    assert response.status_code==200,response.text
    assert response.json()["accepted"]==1
    assert len([row for row in response.json()["results"] if row["status"]=="error"])==2
    exported=client.get("/v1/account/skus/export?format=xlsx",headers=headers)
    assert exported.status_code==200
    book=load_workbook(BytesIO(exported.content),read_only=True)
    assert list(book.active.values)[1][0]=="BOX-001";book.close()
    _,free=account(client,session_factory,"free-phase2@example.com",plan="FREE")
    assert client.post("/v1/account/skus/generate",headers=free,json={}).status_code==403
    assert client.get("/v1/account/skus/export",headers=free).status_code==403


def test_catalogue_variants_aliases_bulk_edits_and_isolation(unauthenticated_client,session_factory):
    client=unauthenticated_client;_,headers=account(client,session_factory)
    parent=product(client,headers,"PARENT",supplier="Supplier A",tags=["Boxes"])
    child=product(client,headers,"CHILD",variant_of=parent["id"],custom_fields={"Colour":"Blue"})
    alias=client.post(f'/v1/account/catalogue/{child["id"]}/identifiers',headers=headers,json={"value":"96385074","kind":"ean8","revision":child["revision"]})
    assert alias.status_code==200,alias.text
    child["revision"]=alias.json()["revision"]
    duplicate=client.post("/v1/account/vault",headers=headers,json={"barcode":"96385074","name":"Must not overwrite"})
    assert not duplicate.json()["created"] and duplicate.json()["item"]["id"]==child["id"]
    assert client.get("/v1/account/vault?q=96385074",headers=headers).json()["total"]==1
    patch={"product_ids":[parent["id"],child["id"]],"revisions":{parent["id"]:parent["revision"],child["id"]:child["revision"]},"updates":{"collection":"Warehouse"}}
    assert client.post("/v1/account/catalogue/bulk-edit",headers=headers,json=patch).status_code==200
    assert client.post("/v1/account/catalogue/bulk-edit",headers=headers,json=patch).status_code==409
    current=client.get('/v1/account/vault/'+parent["id"],headers=headers).json()
    cycle=client.put('/v1/account/vault/'+parent["id"],headers=headers,json={"barcode":"PARENT","name":"Parent","variant_of":child["id"],"revision":current["revision"]})
    assert cycle.status_code==422
    exported=client.get('/v1/account/catalogue/export?format=json',headers=headers)
    assert exported.status_code==200 and len(exported.json())==2
    _,other=account(client,session_factory,"catalogue-other@example.com")
    assert client.get(f'/v1/account/catalogue/{child["id"]}/identifiers',headers=other).status_code==404
    assert client.post('/v1/account/catalogue/selection',headers=other,json={"product_ids":[child["id"]]}).status_code==404


def test_catalogue_import_never_overwrites_existing_records(unauthenticated_client,session_factory):
    client=unauthenticated_client;_,headers=account(client,session_factory)
    item=product(client,headers,"EXISTING")
    response=client.post('/v1/account/catalogue/import',headers=headers,json=upload('barcode,name,sku\nEXISTING,Remote overwrite,\nNEW,New record,NEW-SKU\nOTHER,Another,NEW-SKU\n'))
    assert response.status_code==200,response.text
    assert response.json()["accepted"]==1
    assert response.json()["results"][0]["status"]=="skipped"
    assert client.get('/v1/account/vault/'+item["id"],headers=headers).json()["name"]=="Storage box"


@pytest.mark.parametrize("kind,value",[("ean13","3017620422003"),("ean8","96385074"),("upca","012345678905"),("code128","SKU-001"),("code39","ASSET-001"),("qr","https://example.com")])
def test_label_studio_exports_remain_machine_readable(kind,value):
    design=LabelDefinition.model_validate(definition("qr" if kind=="qr" else "barcode"))
    product={"name":"Test product","barcode":value,"barcode_type":kind}
    png=render_png(design,product)
    result=zxingcpp.read_barcode(Image.open(BytesIO(png)))
    assert result
    assert result.text.zfill(14)==value.zfill(14) if kind in {"ean13","ean8","upca"} else result.text==value
    assert abs(Image.open(BytesIO(png)).info["dpi"][0]-300)<.1
    svg=render_svg(design,product);assert b'<svg' in svg and b'white' in svg
    assert render_pdf(design,[product]*2).startswith(b'%PDF')


def test_template_gates_geometry_and_customer_isolation(unauthenticated_client,session_factory):
    client=unauthenticated_client;uid,headers=account(client,session_factory)
    item=product(client,headers)
    response=client.post('/v1/account/label-studio/templates',headers=headers,json={"name":"My labels","definition":definition()})
    assert response.status_code==200,response.text
    template=response.json()
    exported=client.post('/v1/account/label-studio/render',headers=headers,json={"definition":definition(),"product_ids":[item["id"]],"format":"png"})
    assert exported.status_code==200 and exported.headers["x-service-category"]=="labels"
    overlap=definition();overlap["elements"][0]["y"]=20
    assert client.post('/v1/account/label-studio/templates',headers=headers,json={"name":"Bad","definition":overlap}).status_code==422
    assert client.post('/v1/account/label-studio/render',headers=headers,json={"definition":definition(),"product_ids":[item["id"],item["id"]],"format":"pdf"}).status_code==422
    _,other=account(client,session_factory,"studio-other@example.com",plan="FREE")
    assert client.get('/v1/account/label-studio/templates/'+template["id"],headers=other).status_code==404
    assert client.post('/v1/account/label-studio/templates',headers=other,json={"name":"No plan","definition":definition()}).status_code==403
    with session_factory() as session:
        sub=session.scalar(select(Subscription).where(Subscription.user_id==uid));sub.status="unpaid";session.commit()
    assert client.get('/v1/account/label-studio/templates/'+template["id"],headers=headers).status_code==200
    assert client.post('/v1/account/label-studio/render',headers=headers,json={"definition":definition(),"product_ids":[item["id"]]}).status_code==403


def test_xlsx_formulas_and_external_relationships_are_rejected():
    book=Workbook();sheet=book.active;sheet.append(["barcode","name"]);sheet.append(["00123","=1+1"])
    output=BytesIO();book.save(output)
    with pytest.raises(ValueError,match="formulas"):
        read_table(TableUpload(format="xlsx",data_base64=base64.b64encode(output.getvalue()).decode()))
    output=BytesIO()
    with ZipFile(output,"w",compression=ZIP_DEFLATED) as archive:
        archive.writestr("xl/externalLinks/externalLink1.xml","bad")
    with pytest.raises(ValueError,match="external"):
        read_table(TableUpload(format="xlsx",data_base64=base64.b64encode(output.getvalue()).decode()))
    exported=table_response([{"barcode":"00123","name":"=1+1"}],["barcode","name"],"xlsx","test")
    book=load_workbook(BytesIO(exported.body),read_only=True,data_only=False)
    row=list(book.active.rows)[1];assert row[0].value=="00123" and row[1].data_type=="s";book.close()


def test_small_thermal_internal_barcode_keeps_whole_modules():
    design=LabelDefinition(width_mm=50,height_mm=25,layout="thermal",elements=[{"kind":"barcode","x":1,"y":1,"w":48,"h":23}])
    data=render_png(design,{"barcode":"SKU-001","barcode_type":"code128"})
    assert zxingcpp.read_barcode(Image.open(BytesIO(data))).text=="SKU-001"
