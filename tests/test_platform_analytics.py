from uuid import uuid4
from datetime import timedelta
from sqlalchemy import select,func
from tests.test_platform_phase2 import account
from app.models import FunnelEvent,User,utcnow
from app.funnel import record_event,prune_expired_events


def test_events_reject_private_payloads_and_link_only_authenticated_owner(unauthenticated_client,session_factory):
    c=unauthenticated_client
    response=c.post('/v1/auth/register',json={'email':'funnel-owner@example.invalid','name':'Owner','password':'correct horse battery staple'})
    assert response.status_code==201,response.text
    with session_factory() as s:uid=s.scalar(select(User.id).where(User.email=='funnel-owner@example.invalid'))
    h={}
    visit=str(uuid4());body={'visit_id':visit,'event':'tool_opened','tool':'catalogue'}
    assert c.post('/v1/events',json={**body,'product_name':'Private'}).status_code==422
    assert c.post('/v1/events',json={**body,'event':'subscription_activated'}).status_code==422
    assert c.post('/v1/events',json=body).status_code==204
    assert c.post('/v1/account/events',headers=h,json=body).status_code==204
    assert c.get('/v1/admin/funnel',headers=h).status_code==403
    with session_factory() as s:
        s.get(User,uid).is_admin=True;s.commit()
        rows=s.scalars(select(FunnelEvent).where(FunnelEvent.event=='tool_opened')).all()
        assert {r.user_id for r in rows}=={None,uid}
        assert all(r.visit_hash!=visit and len(r.visit_hash)==64 for r in rows)
        record_event(s,'subscription_activated',user_id=uid,tool='billing',dedupe_key='test-sub-activation')
        record_event(s,'subscription_activated',user_id=uid,tool='billing',dedupe_key='test-sub-activation');s.commit()
        assert s.scalar(select(func.count()).select_from(FunnelEvent).where(FunnelEvent.event=='subscription_activated'))==1
    report=c.get('/v1/admin/funnel',headers=h);assert report.status_code==200,report.text
    d=report.json();assert d['events']['tool_opened']['count']==2 and d['events']['user_registered']['accounts']==1
    assert d['new_registered_accounts_activated']==1
    assert visit not in report.text and 'Private' not in report.text


def test_event_retention_does_not_delete_accounts(unauthenticated_client,session_factory):
    uid,_=account(unauthenticated_client,session_factory)
    with session_factory() as s:
        s.add(FunnelEvent(event='visitor',tool='platform',created_at=utcnow()-timedelta(days=91)))
        s.commit();removed=prune_expired_events(s,utcnow()-timedelta(days=90))
        assert removed==1 and s.get(User,uid)


def test_verified_subscription_activation_is_idempotent(session_factory):
    from tests.test_subscription_billing import account as billing_account,stripe_subscription
    from app.billing import process_stripe_event
    from app.config import get_settings
    uid,_=billing_account(session_factory)
    settings=get_settings().model_copy(update={'stripe_starter_price_id':'price_starter'})
    obj=stripe_subscription(uid,'STARTER')
    with session_factory() as s:
        assert process_stripe_event(s,settings,{'id':'evt_funnel1','type':'customer.subscription.created','data':{'object':obj}})
        assert process_stripe_event(s,settings,{'id':'evt_funnel2','type':'customer.subscription.updated','data':{'object':obj}})
        assert s.scalar(select(func.count()).select_from(FunnelEvent).where(FunnelEvent.user_id==uid,FunnelEvent.event=='subscription_activated'))==1
