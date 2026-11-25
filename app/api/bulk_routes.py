"""Bounded file handling and idempotent quotas for browser bulk workers."""
from typing import Literal
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,Field,field_validator
from sqlalchemy import select
from app.api.vault_routes import Db,Owner,Config
from app.api.workspace_dependencies import ProcessingOwner
from app.api.tool_routes import AccountAuthenticated
from app.models import User
from app.entitlements import require_feature,reserve_workspace_units
from app.service_usage import consume_service_usage
from app.workspace_io import TableUpload,read_table,table_response
from app.workspace_receipts import fingerprint,replay,remember

router=APIRouter(prefix="/v1/account/bulk",tags=["bulk workspace"])


class BulkGrant(BaseModel):
    request_id:str=Field(min_length=16,max_length=64,pattern=r"^[A-Za-z0-9_-]+$")
    operation:Literal["validate","generate","lookup","enrich"]
    values:list[str]=Field(min_length=1,max_length=100)
    symbology:Literal["ean13","ean8","upca","code128","code39","qr"]="ean13"

    @field_validator("values")
    @classmethod
    def bounded_values(cls,values):
        if any(len(v)>80 or "\x00" in v for v in values):raise ValueError("Values must be at most 80 characters and contain no null bytes")
        return values


class TableExport(BaseModel):
    rows:list[dict[str,str|int|float|bool|None]]=Field(max_length=1000)
    fields:list[str]=Field(min_length=1,max_length=50)
    format:Literal["csv","xlsx","json"]="csv"


@router.post("/read")
def bulk_read(payload:TableUpload,session:Db,owner:ProcessingOwner,settings:Config):
    require_feature(session,owner.user,settings,"bulk_generation")
    try:rows=read_table(payload,max_bytes=settings.workspace_upload_bytes,max_rows=1000)
    except ValueError as exc:raise HTTPException(422,detail=str(exc)) from exc
    return {"rows":rows,"count":len(rows),"maximum":1000}


@router.post("/reserve")
def reserve_bulk(payload:BulkGrant,session:Db,owner:Owner,settings:Config,auth:AccountAuthenticated):
    require_feature(session,owner.user,settings,"bulk_generation")
    if len(payload.values)>min(25,settings.workspace_batch_limit):raise HTTPException(422,detail="Process at most 25 rows per chunk")
    session.scalar(select(User).where(User.id==owner.user.id).with_for_update())
    digest=fingerprint(payload.model_dump(exclude={"request_id"}))
    previous=replay(session,owner.user.id,payload.request_id,"bulk",digest)
    if previous is not None:return previous
    reserve_workspace_units(session,owner.user,settings,"bulk_rows",len(payload.values))
    if payload.operation=="generate":consume_service_usage(session,auth,settings,"generation",len(payload.values),commit=False)
    if payload.operation=="enrich":raise HTTPException(422,detail="Use the idempotent enrichment operation")
    result={"request_id":payload.request_id,"operation":payload.operation,"units":len(payload.values),"granted":True}
    remember(session,owner.user.id,payload.request_id,"bulk",digest,result);session.commit()
    return result


@router.post("/enrich")
def enrich_bulk(payload:BulkGrant,session:Db,owner:ProcessingOwner,settings:Config,auth:AccountAuthenticated):
    require_feature(session,owner.user,settings,"bulk_generation")
    if payload.operation!="enrich" or len(payload.values)!=1:raise HTTPException(422,detail="Enrich one row per bounded request")
    from app.barcodes import parse_barcode
    try:parse_barcode(payload.values[0])
    except ValueError as exc:raise HTTPException(422,detail=str(exc)) from exc
    session.scalar(select(User).where(User.id==owner.user.id).with_for_update())
    digest=fingerprint(payload.model_dump(exclude={"request_id"}))
    previous=replay(session,owner.user.id,payload.request_id,"enrich",digest)
    if previous is not None:return previous
    reserve_workspace_units(session,owner.user,settings,"bulk_rows",1)
    consume_service_usage(session,auth,settings,"enrichment",1,commit=False)
    from app.services import resolve_product
    result=resolve_product(session,payload.values[0],settings=settings).result.model_dump(mode="json")
    remember(session,owner.user.id,payload.request_id,"enrich",digest,result);session.commit()
    return result


@router.post("/export")
def export_bulk(payload:TableExport,session:Db,owner:ProcessingOwner,settings:Config):
    require_feature(session,owner.user,settings,"advanced_exports")
    if len(set(payload.fields))!=len(payload.fields) or any(len(f)>100 for f in payload.fields):raise HTTPException(422,detail="Use unique field names up to 100 characters")
    if any(len(row)>50 or any(len(str(v))>10000 for v in row.values()) for row in payload.rows):raise HTTPException(422,detail="Export cells or columns exceed processing limits")
    try:return table_response(payload.rows,payload.fields,payload.format,"bulk-results")
    except ValueError as exc:raise HTTPException(422,detail=str(exc)) from exc
