from uuid import uuid4
from sqlalchemy import select
from tests.test_platform_phase2 import account,upload
from app.models import ServiceUsage,SkuAllocation


def test_bulk_safe_files_quota_receipts_and_exports(unauthenticated_client,session_factory):
    c=unauthenticated_client;uid,h=account(c,session_factory)
    assert c.post('/v1/account/bulk/read',headers=h,json=upload('barcode,name\n00123,Box\n')).json()['rows'][0]['barcode']=='00123'
    body={"request_id":str(uuid4()),"operation":"generate","values":["BOX-1","BOX-2"],"symbology":"code128"}
    first=c.post('/v1/account/bulk/reserve',headers=h,json=body);assert first.status_code==200,first.text
    assert c.post('/v1/account/bulk/reserve',headers=h,json=body).json()==first.json()
    assert c.post('/v1/account/bulk/reserve',headers=h,json={**body,"values":["BOX-3"]}).status_code==409
    with session_factory() as s:
        used=dict(s.execute(select(ServiceUsage.category,ServiceUsage.units).where(ServiceUsage.account_scope=='user:'+uid)).all())
        assert used['generation']==2 and used['bulk_rows']==2
    output=c.post('/v1/account/bulk/export',headers=h,json={"rows":[{"barcode":"00123","name":"=1+1"}],"fields":["barcode","name"],"format":"csv"})
    assert output.status_code==200 and "'=1+1" in output.text
    output=c.post('/v1/account/bulk/export',headers=h,json={"rows":[{"=SUM(1,1)":"literal"}],"fields":["=SUM(1,1)"],"format":"xlsx"})
    from openpyxl import load_workbook
    from io import BytesIO
    book=load_workbook(BytesIO(output.content),read_only=True,data_only=False)
    assert book.active['A1'].data_type=='s' and book.active['A1'].value=='=SUM(1,1)';book.close()
    output=c.post('/v1/account/bulk/export',headers=h,json={"rows":[{"=1+1":"literal"}],"fields":["=1+1"],"format":"csv"})
    assert "'=1+1" in output.text.splitlines()[0]
    _,free=account(c,session_factory,'bulk-free@example.com',plan='FREE')
    assert c.post('/v1/account/bulk/read',headers=free,json=upload('barcode\n00123\n')).status_code==403


def test_sku_retry_receipt_prevents_duplicate_allocation(unauthenticated_client,session_factory):
    c=unauthenticated_client;uid,h=account(c,session_factory)
    body={"request_id":str(uuid4()),"prefix":"RETRY","quantity":3}
    first=c.post('/v1/account/skus/generate',headers=h,json=body);assert first.status_code==200,first.text
    assert c.post('/v1/account/skus/generate',headers=h,json=body).json()==first.json()
    assert c.post('/v1/account/skus/generate',headers=h,json={**body,"quantity":4}).status_code==409
    with session_factory() as s:
        assert len(s.scalars(select(SkuAllocation).where(SkuAllocation.user_id==uid)).all())==3
    assert c.post('/v1/account/skus/generate',headers=h,json={'pattern':'{}-{number:06}'}).status_code==422


def test_export_byte_budget_stops_consuming_oversized_generator():
    import pytest
    from fastapi import HTTPException
    from app.workspace_io import table_response
    consumed=0
    def rows():
        nonlocal consumed
        for _ in range(10000):
            consumed+=1
            yield {'notes':'x'*4000,'custom_fields':{str(i):'v'*500 for i in range(20)}}
    with pytest.raises(HTTPException) as error:table_response(rows(),['notes','custom_fields'],'json','bounded')
    assert error.value.status_code==422 and consumed<1000
