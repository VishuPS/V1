from uuid import uuid4
from sqlalchemy import select
from tests.test_platform_phase2 import account,definition
from app.models import Subscription


def test_assets_lifecycle_history_labels_and_safe_retry(unauthenticated_client,session_factory):
    c=unauthenticated_client;uid,h=account(c,session_factory,plan='STARTER')
    body={"request_id":str(uuid4()),"asset_code":"LAP-001","name":"Warehouse laptop","location":"Office","assigned_person":"Operations","purchase_minor":120000,"currency":"EUR","acquired_on":"2026-01-01"}
    r=c.post('/v1/account/assets',headers=h,json=body);assert r.status_code==200,r.text
    item=r.json();assert item['status']=='available'
    assert c.post('/v1/account/assets',headers=h,json=body).json()==item
    path='/v1/account/assets/'+item['id']
    checkout={"request_id":str(uuid4()),"revision":item['revision'],"action":"checkout","person":"Team member","note":"Project visit"}
    response=c.post(path+'/actions',headers=h,json=checkout);assert response.status_code==200,response.text
    item=response.json();assert item['checked_out_to']=='Team member'
    assert c.post(path+'/actions',headers=h,json=checkout).json()==item
    assert c.post(path+'/actions',headers=h,json={**checkout,"request_id":str(uuid4())}).status_code==409
    retire={"request_id":str(uuid4()),"revision":item['revision'],"action":"retire","note":"Retire"}
    assert c.post(path+'/actions',headers=h,json=retire).status_code==409
    for action,extra in [('checkin',{}),('move',{'location':'Store room'}),('maintenance',{'event_date':'2026-11-20'}),('retire',{}),('restore',{})]:
        r=c.post(path+'/actions',headers=h,json={"request_id":str(uuid4()),"revision":item['revision'],"action":action,"note":action,**extra});assert r.status_code==200,r.text;item=r.json()
    assert item['location']=='Store room' and item['status']=='available'
    history=c.get(path+'/history',headers=h).json();assert history['total']==7
    assert {'checkout','checkin','move','maintenance','retire','restore','created'}=={e['kind'] for e in history['items']}
    label=c.post(path+'/label',headers=h,json={'definition':definition(),'format':'png'});assert label.status_code==200,label.text
    assert label.content.startswith(b'\x89PNG')
    assert c.get('/v1/account/assets/export?format=xlsx',headers=h).status_code==200
    assert c.post('/v1/account/assets',headers=h,json={**body,'request_id':str(uuid4())}).status_code==409
    with session_factory() as s:
        sub=s.scalar(select(Subscription).where(Subscription.user_id==uid));sub.status='unpaid';s.commit()
    assert c.get(path,headers=h).status_code==200
    assert c.post(path+'/actions',headers=h,json={**checkout,'request_id':str(uuid4()),'revision':item['revision']}).status_code==403


def test_asset_owner_and_plan_isolation(unauthenticated_client,session_factory):
    c=unauthenticated_client;_,h=account(c,session_factory,plan='STARTER')
    body={"request_id":str(uuid4()),"asset_code":"ASSET-001","name":"Scanner"}
    item=c.post('/v1/account/assets',headers=h,json=body).json();path='/v1/account/assets/'+item['id']
    _,other=account(c,session_factory,'asset-other@example.com',plan='STARTER')
    assert c.get(path,headers=other).status_code==404
    assert c.get(path+'/history',headers=other).status_code==404
    assert c.post(path+'/actions',headers=other,json={"request_id":str(uuid4()),"revision":1,"action":"checkout","person":"Other","note":"No"}).status_code==404
    assert c.get('/v1/account/assets',headers=other).json()['total']==0
    _,free=account(c,session_factory,'asset-free@example.com',plan='FREE')
    assert c.post('/v1/account/assets',headers=free,json=body).status_code==403
