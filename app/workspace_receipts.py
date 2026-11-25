import hashlib
import json
from fastapi import HTTPException
from sqlalchemy import select,func
from app.models import WorkspaceReceipt


def fingerprint(payload):
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()).hexdigest()


def replay(session,user_id,request_id,operation,digest):
    if not request_id:return None
    item=session.scalar(select(WorkspaceReceipt).where(WorkspaceReceipt.user_id==user_id,WorkspaceReceipt.request_id==request_id))
    if item:
        if item.operation!=operation or item.digest!=digest:raise HTTPException(409,detail="This request identifier was already used with different inputs")
        return item.result
    if session.scalar(select(func.count()).select_from(WorkspaceReceipt).where(WorkspaceReceipt.user_id==user_id))>=100000:
        raise HTTPException(409,detail="The operation receipt capacity is reached. Contact support; existing receipts are retained for safe retries.")
    return None


def remember(session,user_id,request_id,operation,digest,result):
    if request_id:session.add(WorkspaceReceipt(user_id=user_id,request_id=request_id,operation=operation,digest=digest,result=result))
