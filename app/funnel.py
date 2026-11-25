"""First-party event vocabulary. Private values are never accepted as attributes."""
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from app.models import FunnelEvent,new_uuid,utcnow

EVENTS={'visitor','tool_opened','barcode_generated','barcode_looked_up','product_saved','label_exported','bulk_started','bulk_completed','inventory_created','upgrade_prompt_displayed','checkout_initiated','subscription_activated','user_registered','session_linked'}
CLIENT_EVENTS=EVENTS-{'subscription_activated','user_registered','checkout_initiated','session_linked'}
TOOLS={'platform','lookup','generator','scanner','validator','converter','vault','catalogue','skus','inventory','assets','label_studio','bulk','auditor','exports','api','billing'}


def record_event(session,event,*,user_id=None,visit_hash=None,tool='platform',dedupe_key=None):
    if event not in EVENTS or tool not in TOOLS:raise ValueError('Unknown analytics event or tool')
    insert=pg_insert if session.get_bind().dialect.name=='postgresql' else sqlite_insert
    statement=insert(FunnelEvent).values(id=new_uuid(),user_id=user_id,visit_hash=visit_hash,event=event,tool=tool,dedupe_key=dedupe_key,created_at=utcnow())
    if dedupe_key:statement=statement.on_conflict_do_nothing(index_elements=[FunnelEvent.dedupe_key])
    session.execute(statement)


def record_event_safely(session,event,**kwargs):
    """Analytics failures cannot change registration, payment or tool behaviour."""
    try:
        with session.begin_nested():record_event(session,event,**kwargs)
    except Exception:
        import logging
        logging.getLogger(__name__).warning('Aggregate event was not recorded',exc_info=False)


def prune_expired_events(session,cutoff):
    """Bounded retention pass: at most 10,000 telemetry rows, no customer records."""
    from sqlalchemy import select,delete
    removed=0
    for _ in range(10):
        ids=list(session.scalars(select(FunnelEvent.id).where(FunnelEvent.created_at<cutoff).order_by(FunnelEvent.created_at).limit(1000)))
        if not ids:break
        session.execute(delete(FunnelEvent).where(FunnelEvent.id.in_(ids)));session.commit();removed+=len(ids)
    return removed
