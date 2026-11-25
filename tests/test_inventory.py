from uuid import uuid4
from sqlalchemy import select,func
from app.models import Subscription,StockMovement
from tests.test_platform_phase2 import account,product


def test_inventory_ledger_retries_archiving_and_valuation(unauthenticated_client,session_factory):
    client=unauthenticated_client;uid,headers=account(client,session_factory,plan="STARTER")
    p=product(client,headers,price_minor=500,currency="EUR")
    body={"product_id":p["id"],"low_stock_threshold":2,"purchase_minor":123,"currency":"EUR","location":"Shelf A"}
    response=client.post('/v1/account/inventory',headers=headers,json=body)
    assert response.status_code==200,response.text
    item=response.json();path='/v1/account/inventory/'+item['id']+'/movements'
    movement={"request_id":str(uuid4()),"delta":5,"reason":"Purchase delivery"}
    first=client.post(path,headers=headers,json=movement);assert first.status_code==200,first.text
    assert first.json()['balance']==5
    replay=client.post(path,headers=headers,json=movement);assert replay.json()['replayed'] is True
    assert client.post(path,headers=headers,json={**movement,"delta":4}).status_code==409
    assert client.post(path,headers=headers,json={"request_id":str(uuid4()),"delta":-6,"reason":"Too many"}).status_code==409
    assert client.post(path,headers=headers,json={"request_id":str(uuid4()),"delta":0,"reason":"Zero"}).status_code==422
    d=client.get('/v1/account/inventory/dashboard',headers=headers).json()
    assert d['stock_units']==5 and d['valuation']==[{"currency":"EUR","purchase_value_minor":"615","missing_purchase_prices":0}]
    assert client.delete('/v1/account/vault/'+p['id'],headers=headers).status_code==409
    assert client.post('/v1/account/inventory',headers=headers,json={**body,"revision":1,"archived":True}).status_code==409
    rows=client.get('/v1/account/inventory?q=SKU-001',headers=headers).json()['items'];assert rows[0]['quantity']==5
    assert client.post('/v1/account/inventory',headers=headers,json={**body,"revision":1}).status_code==409
    assert client.get('/v1/account/inventory/export?format=xlsx',headers=headers).status_code==200
    removal={"request_id":str(uuid4()),"delta":-5,"reason":"Sale"}
    assert client.post(path,headers=headers,json=removal).json()['balance']==0
    current=client.get('/v1/account/inventory',headers=headers).json()['items'][0]
    archived=client.post('/v1/account/inventory',headers=headers,json={**body,"revision":current['revision'],"archived":True});assert archived.status_code==200
    assert client.get('/v1/account/inventory',headers=headers).json()['total']==0
    assert client.get('/v1/account/inventory?archived=true',headers=headers).json()['total']==1
    assert client.post(path,headers=headers,json={"request_id":str(uuid4()),"delta":1,"reason":"Archived"}).status_code==409
    history=client.get(path,headers=headers).json();assert history['total']==2
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(StockMovement).where(StockMovement.user_id==uid))==2
        sub=session.scalar(select(Subscription).where(Subscription.user_id==uid));sub.status='unpaid';session.commit()
    assert client.get(path,headers=headers).status_code==200
    assert client.post(path,headers=headers,json=removal).status_code==403


def test_inventory_owner_and_plan_isolation(unauthenticated_client,session_factory):
    client=unauthenticated_client;_,paid=account(client,session_factory,plan='STARTER');p=product(client,paid)
    item=client.post('/v1/account/inventory',headers=paid,json={"product_id":p['id']}).json()
    _,free=account(client,session_factory,'inventory-free@example.com',plan='FREE')
    assert client.post('/v1/account/inventory',headers=free,json={"product_id":p['id']}).status_code==403
    _,other=account(client,session_factory,'inventory-other@example.com',plan='STARTER')
    assert client.post('/v1/account/inventory',headers=other,json={"product_id":p['id']}).status_code==404
    assert client.get('/v1/account/inventory',headers=other).json()['total']==0
    assert client.get('/v1/account/inventory/'+item['id']+'/movements',headers=other).status_code==404
    assert client.post('/v1/account/inventory/'+item['id']+'/movements',headers=other,json={"request_id":str(uuid4()),"delta":1,"reason":" чужой"}).status_code==404
