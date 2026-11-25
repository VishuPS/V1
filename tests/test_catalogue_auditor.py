from sqlalchemy import select
from tests.test_platform_phase2 import account,product
from app.models import VaultProduct,ServiceUsage


def test_auditor_rules_score_and_no_mutations(unauthenticated_client,session_factory):
    client=unauthenticated_client;uid,h=account(client,session_factory,plan='STARTER')
    a=product(client,h,barcode='A',category='Tools',sku='A')
    b=product(client,h,barcode='B',category='tools',sku='B')
    with session_factory() as s:
        p=s.get(VaultProduct,b['id']);p.barcode='1234567890123';p.barcode_type='ean13';p.sku=' a ';p.name=' ';p.image_url='javascript:bad';p.price_minor=-1;s.commit()
    result=client.post('/v1/account/catalogue-auditor/run',headers=h,json={})
    assert result.status_code==200,result.text
    d=result.json();assert d['products_checked']==2 and d['mutations']==0
    assert d['counts']=={'duplicate_barcode':0,'duplicate_sku':2,'invalid_gtin':1,'missing_name':1,'missing_image':1,'missing_price':1,'inconsistent_category':2,'invalid_format':1}
    assert d['score']==43.8
    assert all(i['correction_url'].startswith('/catalogue/?product=') for i in d['issues'])
    with session_factory() as s:
        assert s.get(VaultProduct,b['id']).name==' '
        assert s.scalar(select(ServiceUsage.units).where(ServiceUsage.account_scope=='user:'+uid,ServiceUsage.category=='audit_rows'))==2
    _,other=account(client,session_factory,'auditor-other@example.com',plan='STARTER')
    empty=client.post('/v1/account/catalogue-auditor/run',headers=other,json={}).json();assert empty['score'] is None and empty['issues']==[]
    _,free=account(client,session_factory,'auditor-free@example.com',plan='FREE')
    assert client.post('/v1/account/catalogue-auditor/run',headers=free,json={}).status_code==403
