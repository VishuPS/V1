from datetime import datetime,timedelta,timezone
from sqlalchemy import select
from app.models import CallPack,Subscription,SubscriptionPlan,ApiClient
from app.config import get_settings
from app.billing import process_stripe_event
from app.call_packs import sync,consume
from tests.test_user_auth import create_account,login
import pytest
from fastapi import HTTPException

def setup(session_factory):
    uid,key=create_account(session_factory)
    with session_factory() as db:
        db.add(SubscriptionPlan(code='ONE_TIME',name='One-time pack',monthly_lookups=500,requests_per_minute=30,price_cents=499,currency='USD'))
        db.commit()
    return uid,key

def payment(uid,session_id='cs_pack',paid=True):
    return {'id':'evt_'+session_id,'type':'checkout.session.completed','data':{'object':{'id':session_id,'mode':'payment','payment_status':'paid' if paid else 'unpaid','amount_total':499,'currency':'usd','customer':'cus_pack','metadata':{'plan':'ONE_TIME','user_id':uid}}}}

def test_multiple_purchases_have_independent_expiry_and_no_duplicate_grants(session_factory):
    uid,_=setup(session_factory)
    with session_factory() as db:
        assert process_stripe_event(db,get_settings(),payment(uid))
        assert not process_stripe_event(db,get_settings(),payment(uid))
        # A different event for the same session cannot grant again either.
        event=payment(uid);event['id']='evt_different'
        process_stripe_event(db,get_settings(),event)
        first=db.scalar(select(CallPack));expiry=first.expires_at
        process_stripe_event(db,get_settings(),payment(uid,'cs_second'))
        packs=list(db.scalars(select(CallPack).order_by(CallPack.activated_at)))
        assert len(packs)==2 and packs[0].expires_at==expiry
        assert all(p.expires_at-p.activated_at==timedelta(days=30) for p in packs)
        record=db.scalar(select(Subscription).where(Subscription.user_id==uid))
        used,limit=consume(db,record,550,datetime.now(timezone.utc))
        assert (used,limit)==(550,1000)
        assert packs[0].used==500 and packs[1].used==50
        assert record.free_credits_used==0
        with pytest.raises(HTTPException): consume(db,record,451,datetime.now(timezone.utc))

def test_expiry_restores_free_without_resetting_lifetime_usage(session_factory):
    uid,_=setup(session_factory)
    with session_factory() as db:
        process_stripe_event(db,get_settings(),payment(uid))
        record=db.scalar(select(Subscription).where(Subscription.user_id==uid))
        record.free_credits_used=230
        pack=db.scalar(select(CallPack));pack.expires_at=datetime.now(timezone.utc)-timedelta(seconds=1)
        db.flush();sync(db,record)
        assert record.plan_code=='FREE' and record.free_credits_used==230
        assert db.scalar(select(ApiClient.plan).where(ApiClient.owner_user_id==uid))=='FREE'

def test_paid_pack_api_metering_and_account_summaries(unauthenticated_client,session_factory):
    uid,key=setup(session_factory)
    with session_factory() as db: process_stripe_event(db,get_settings(),payment(uid))
    headers={'X-API-Key':key}
    assert unauthenticated_client.get('/v1/products/3017620422003',headers=headers).status_code==200
    assert unauthenticated_client.get('/v1/products/4006381333931',headers=headers).status_code==404
    with session_factory() as db:
        assert db.scalar(select(CallPack.used))==2
        assert db.scalar(select(Subscription.free_credits_used).where(Subscription.user_id==uid))==0
    tokens=login(unauthenticated_client,'owner@example.com').json()
    auth={'Authorization':'Bearer '+tokens['access_token']}
    usage=unauthenticated_client.get('/v1/account/usage',headers=auth).json()
    assert usage['allowance_type']=='one_time' and usage['remaining']==498
    sub=unauthenticated_client.get('/v1/account/subscription',headers=auth).json()
    assert sub['plan']=='ONE_TIME' and len(sub['call_packs'])==1

def test_unpaid_pack_checkout_never_activates(session_factory):
    uid,_=setup(session_factory)
    with session_factory() as db:
        process_stripe_event(db,get_settings(),payment(uid,paid=False))
        assert db.scalar(select(CallPack.id)) is None
        assert db.scalar(select(Subscription.plan_code).where(Subscription.user_id==uid))=='FREE'

def test_checkout_is_one_time_and_not_a_subscription(monkeypatch):
    from app.billing import StripeClient
    settings=get_settings().model_copy(update={'stripe_secret_key':'sk_test_mock','stripe_one_time_price_id':'price_pack'})
    captured=[]
    monkeypatch.setattr(StripeClient,'_post',lambda self,path,data: (captured.extend(data) or {'url':'https://checkout.stripe.com/example'}))
    StripeClient(settings).call_pack_checkout('user','user@example.com',None)
    assert ('mode','payment') in captured
    assert ('line_items[0][price]','price_pack') in captured
    assert not any('subscription_data' in key for key,value in captured)

def test_old_pack_expiry_does_not_extend_on_repurchase(session_factory):
    uid,_=setup(session_factory)
    with session_factory() as db:
        process_stripe_event(db,get_settings(),payment(uid))
        first=db.scalar(select(CallPack));first.expires_at=datetime.now(timezone.utc)+timedelta(days=1);db.commit()
        process_stripe_event(db,get_settings(),payment(uid,'cs_new'))
        record=db.scalar(select(Subscription).where(Subscription.user_id==uid))
        sync(db,record,datetime.now(timezone.utc)+timedelta(days=2))
        assert record.monthly_call_limit==500 and record.monthly_calls_used==0

def test_pack_metering_supports_multiple_purchases_in_one_calendar_month(session_factory):
    from app.auth import authenticate_api_key,consume_usage
    uid,key=setup(session_factory)
    with session_factory() as db:
        process_stripe_event(db,get_settings(),payment(uid))
        process_stripe_event(db,get_settings(),payment(uid,'cs_more'))
        context=authenticate_api_key(key,db,get_settings())
        result=consume_usage(db,context,750)
        assert result.quota_remaining==250
        assert sum(db.scalars(select(CallPack.used)))==750

def test_migration_adds_one_time_catalog_without_changing_existing_plans():
    import importlib.util
    from pathlib import Path
    from sqlalchemy import create_engine,inspect,text
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    engine=create_engine('sqlite://')
    from app.db import Base
    Base.metadata.create_all(engine,tables=[t for name,t in Base.metadata.tables.items() if name!='call_packs'])
    path=Path(__file__).parents[1]/'migrations/versions/20261104_0020_call_packs.py'
    spec=importlib.util.spec_from_file_location('pack_migration',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
        assert 'call_packs' in inspect(connection).get_table_names()
        assert connection.execute(text("SELECT price_cents FROM subscription_plans WHERE code='ONE_TIME'")).scalar()==499
