import hashlib
from datetime import timedelta
from uuid import UUID
from typing import Literal
from fastapi import APIRouter,Request,Response,HTTPException,Query
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import select,func,delete
from app.api.vault_routes import Db,Config,Owner
from app.funnel import record_event,CLIENT_EVENTS,TOOLS
from app.models import FunnelEvent,utcnow
from app.user_auth import ensure_allowed_origin
from app.auth import rate_limiter

router=APIRouter(tags=['first-party analytics'])


class ClientEvent(BaseModel):
    model_config=ConfigDict(extra='forbid')
    visit_id:UUID
    event:str=Field(max_length=32)
    tool:str=Field(default='platform',max_length=32)


def add(payload,session,request,settings,user=None):
    ensure_allowed_origin(request,settings)
    if payload.event not in CLIENT_EVENTS or payload.tool not in TOOLS:raise HTTPException(422,detail='Unknown event or tool')
    address=request.client.host if request.client else 'unknown'
    rate_limiter.check('analytics:'+hashlib.sha256(address.encode()).hexdigest(),60)
    visit_hash=hashlib.sha256(str(payload.visit_id).encode()).hexdigest()
    if user:
        record_event(session,'session_linked',user_id=user.id,visit_hash=visit_hash,dedupe_key='visit:'+visit_hash+':'+user.id)
    record_event(session,payload.event,user_id=user.id if user else None,visit_hash=visit_hash,tool=payload.tool,dedupe_key='visitor:'+visit_hash if payload.event=='visitor' else None)
    session.commit()
    return Response(status_code=204,headers={'Cache-Control':'no-store'})


@router.post('/v1/events',status_code=204)
def public_event(payload:ClientEvent,session:Db,request:Request,settings:Config):return add(payload,session,request,settings)


@router.post('/v1/account/events',status_code=204)
def account_event(payload:ClientEvent,session:Db,request:Request,settings:Config,owner:Owner):return add(payload,session,request,settings,owner.user)


@router.get('/v1/admin/funnel')
def funnel_summary(session:Db,owner:Owner,days:int=Query(default=30,ge=1,le=90)):
    if not owner.user.is_admin:raise HTTPException(403,detail='Administrator access required')
    cutoff=utcnow()-timedelta(days=days)
    events=session.execute(select(FunnelEvent.event,func.count(),func.count(func.distinct(FunnelEvent.visit_hash)),func.count(func.distinct(FunnelEvent.user_id))).where(FunnelEvent.created_at>=cutoff).group_by(FunnelEvent.event)).all()
    tools=session.execute(select(FunnelEvent.tool,FunnelEvent.event,func.count()).where(FunnelEvent.created_at>=cutoff).group_by(FunnelEvent.tool,FunnelEvent.event)).all()
    linked=select(FunnelEvent.user_id).where(FunnelEvent.created_at>=cutoff,FunnelEvent.event=='session_linked',FunnelEvent.user_id!=None)
    new=select(FunnelEvent.user_id).where(FunnelEvent.created_at>=cutoff,FunnelEvent.event=='user_registered',FunnelEvent.user_id.in_(linked))
    paid=session.scalar(select(func.count(func.distinct(FunnelEvent.user_id))).where(FunnelEvent.created_at>=cutoff,FunnelEvent.event=='subscription_activated',FunnelEvent.user_id.in_(new)))
    return {'days':days,'events':{e:{'count':n,'visits':v,'accounts':a} for e,n,v,a in events},'tools':[{'tool':t,'event':e,'count':n} for t,e,n in tools],'new_registered_accounts_with_visit':session.scalar(select(func.count()).select_from(new.distinct().subquery())),'new_registered_accounts_activated':paid,'first_recorded_at':session.scalar(select(func.min(FunnelEvent.created_at))),'note':'Client events are approximate and respect browser opt-outs. Registration and activation are server events. No historical events were backfilled. Visits are random tab identifiers, not people; accounts can span devices.'}
