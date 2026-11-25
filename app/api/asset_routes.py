from datetime import date
from typing import Literal
from fastapi import APIRouter,HTTPException,Query,Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel,Field,field_validator
from sqlalchemy import select,func,or_
from app.api.vault_routes import Db,Owner,Config,owned,VaultInput
from app.api.workspace_dependencies import ProcessingOwner
from app.api.tool_routes import AccountAuthenticated
from app.models import AssetRecord,AssetEvent,User,utcnow
from app.entitlements import require_feature
from app.workspace_receipts import fingerprint,replay,remember
from app.workspace_io import table_response
from app.label_studio import LabelDefinition,render_png,render_svg,render_pdf
from app.service_usage import consume_service_usage

router=APIRouter(prefix="/v1/account/assets",tags=["private assets"])
RequestId=Field(min_length=16,max_length=64,pattern=r"^[A-Za-z0-9_-]+$")


class AssetInput(BaseModel):
    request_id:str=RequestId
    revision:int|None=Field(default=None,ge=1)
    asset_code:str=Field(min_length=1,max_length=80,pattern=r"^[A-Z0-9][A-Z0-9_.-]*$")
    name:str=Field(min_length=1,max_length=200)
    description:str=Field(default="",max_length=4000)
    location:str=Field(default="",max_length=200)
    assigned_person:str=Field(default="",max_length=200)
    product_id:str|None=Field(default=None,max_length=36)
    acquired_on:date|None=None
    purchase_minor:int|None=Field(default=None,ge=0,le=999999999999)
    currency:Literal["USD","EUR","GBP"]="USD"
    condition:Literal["new","good","fair","poor","damaged"]="good"

    @field_validator("name","description","location","assigned_person")
    @classmethod
    def safe_text(cls,value):return VaultInput.safe_unicode(value)


class AssetAction(BaseModel):
    request_id:str=RequestId
    revision:int=Field(ge=1)
    action:Literal["checkout","checkin","move","maintenance","retire","restore"]
    note:str=Field(min_length=1,max_length=1000)
    person:str=Field(default="",max_length=200)
    location:str=Field(default="",max_length=200)
    event_date:date|None=None

    @field_validator("note","person","location")
    @classmethod
    def safe_text(cls,value):return VaultInput.safe_unicode(value).strip()


class AssetLabel(BaseModel):
    definition:LabelDefinition
    format:Literal["pdf","png","svg"]="pdf"


def serialize(item):
    keys=["id","product_id","asset_code","name","description","location","assigned_person","acquired_on","purchase_minor","currency","condition","status","checked_out_to","revision","created_at","updated_at"]
    return jsonable_encoder({key:getattr(item,key) for key in keys})


def asset_owned(session,owner,asset_id):
    if not asset_id or len(asset_id)>36 or "\x00" in asset_id:raise HTTPException(404,detail="Asset not found")
    item=session.scalar(select(AssetRecord).where(AssetRecord.id==asset_id,AssetRecord.user_id==owner.user.id))
    if not item:raise HTTPException(404,detail="Asset not found")
    return item


def save(payload,session,owner,settings,asset_id=None):
    entitlement=require_feature(session,owner.user,settings,"assets")
    session.scalar(select(User).where(User.id==owner.user.id).with_for_update())
    digest=fingerprint({"asset_id":asset_id,**payload.model_dump(mode="json",exclude={"request_id"})})
    previous=replay(session,owner.user.id,payload.request_id,"asset_save",digest)
    if previous is not None:return previous
    if payload.product_id:owned(session,owner,payload.product_id)
    if not payload.name.strip():raise HTTPException(422,detail="Enter an asset name")
    item=asset_owned(session,owner,asset_id) if asset_id else None
    before=serialize(item) if item else {}
    duplicate=session.scalar(select(AssetRecord.id).where(AssetRecord.user_id==owner.user.id,AssetRecord.asset_code==payload.asset_code,AssetRecord.id!=(asset_id or "")))
    if duplicate:raise HTTPException(409,detail="That asset identification number is already in use")
    if item:
        if item.revision!=payload.revision:raise HTTPException(409,detail="Asset changed. Refresh before saving.")
        item.revision+=1
    else:
        if payload.revision is not None:raise HTTPException(409,detail="New assets have no revision")
        count=session.scalar(select(func.count()).select_from(AssetRecord).where(AssetRecord.user_id==owner.user.id))
        if count>=entitlement['asset_capacity']:raise HTTPException(409,detail="Asset register capacity reached. Existing assets remain available.")
        item=AssetRecord(user_id=owner.user.id);session.add(item)
    for key,value in payload.model_dump(exclude={"request_id","revision"}).items():setattr(item,key,value)
    item.updated_at=utcnow();session.flush();result=serialize(item)
    session.add(AssetEvent(user_id=owner.user.id,asset_id=item.id,kind="updated" if before else "created",note="Asset details saved",before=before,after=result))
    remember(session,owner.user.id,payload.request_id,"asset_save",digest,result);session.commit()
    return result


@router.post("")
def create_asset(payload:AssetInput,session:Db,owner:Owner,settings:Config):return save(payload,session,owner,settings)


@router.put("/{asset_id}")
def update_asset(asset_id:str,payload:AssetInput,session:Db,owner:Owner,settings:Config):return save(payload,session,owner,settings,asset_id)


@router.post("/{asset_id}/actions")
def asset_action(asset_id:str,payload:AssetAction,session:Db,owner:Owner,settings:Config):
    require_feature(session,owner.user,settings,"assets")
    session.scalar(select(User).where(User.id==owner.user.id).with_for_update())
    item=asset_owned(session,owner,asset_id)
    digest=fingerprint({"asset_id":asset_id,**payload.model_dump(mode="json",exclude={"request_id"})})
    previous=replay(session,owner.user.id,payload.request_id,"asset_action",digest)
    if previous is not None:return previous
    if item.revision!=payload.revision:raise HTTPException(409,detail="Asset changed. Refresh before recording this action.")
    before=serialize(item)
    if payload.action=="checkout":
        if item.status!="available" or not payload.person:raise HTTPException(409,detail="Check out an available asset to a named person")
        item.status="checked_out";item.checked_out_to=payload.person
    elif payload.action=="checkin":
        if item.status!="checked_out":raise HTTPException(409,detail="Only checked-out assets can be checked in")
        item.status="available";item.checked_out_to=""
    elif payload.action=="retire":
        if item.status!="available":raise HTTPException(409,detail="Check the asset in before retiring it")
        item.status="retired"
    elif payload.action=="restore":
        if item.status!="retired":raise HTTPException(409,detail="Only retired assets can be restored")
        item.status="available"
    elif payload.action=="move":
        if not payload.location:raise HTTPException(422,detail="Enter the destination location")
        item.location=payload.location
    if not payload.note:raise HTTPException(422,detail="Enter a note for the history")
    item.revision+=1;item.updated_at=utcnow();session.flush();result=serialize(item)
    session.add(AssetEvent(user_id=owner.user.id,asset_id=item.id,kind=payload.action,note=payload.note,event_date=payload.event_date,before=before,after=result))
    remember(session,owner.user.id,payload.request_id,"asset_action",digest,result);session.commit()
    return result


@router.get("")
def list_assets(session:Db,owner:Owner,q:str=Query(default="",max_length=200),status:Literal["","available","checked_out","retired"]="",location:str=Query(default="",max_length=200),offset:int=Query(default=0,ge=0),limit:int=Query(default=50,ge=1,le=100)):
    filters=[AssetRecord.user_id==owner.user.id]
    if q:filters.append(or_(AssetRecord.name.icontains(q,autoescape=True),AssetRecord.asset_code.icontains(q,autoescape=True),AssetRecord.assigned_person.icontains(q,autoescape=True),AssetRecord.checked_out_to.icontains(q,autoescape=True)))
    if status:filters.append(AssetRecord.status==status)
    if location:filters.append(AssetRecord.location==location)
    total=session.scalar(select(func.count()).select_from(AssetRecord).where(*filters))
    items=session.scalars(select(AssetRecord).where(*filters).order_by(AssetRecord.updated_at.desc(),AssetRecord.id).offset(offset).limit(limit)).all()
    return {"items":[serialize(a) for a in items],"total":total}


@router.get("/export")
def export_assets(session:Db,owner:ProcessingOwner,settings:Config,format:Literal["csv","xlsx","json"]="csv"):
    require_feature(session,owner.user,settings,"advanced_exports")
    rows=session.scalars(select(AssetRecord).where(AssetRecord.user_id==owner.user.id).order_by(AssetRecord.asset_code).limit(10001)).all()
    if len(rows)>10000:raise HTTPException(422,detail="Export supports up to 10,000 assets")
    fields=["asset_code","name","description","location","assigned_person","acquired_on","purchase_minor","currency","condition","status","checked_out_to","product_id"]
    return table_response([serialize(a) for a in rows],fields,format,"asset-register")


@router.get("/{asset_id}")
def get_asset(asset_id:str,session:Db,owner:Owner):return serialize(asset_owned(session,owner,asset_id))


@router.get("/{asset_id}/history")
def asset_history(asset_id:str,session:Db,owner:Owner,offset:int=Query(default=0,ge=0),limit:int=Query(default=50,ge=1,le=100)):
    asset_owned(session,owner,asset_id)
    filters=[AssetEvent.user_id==owner.user.id,AssetEvent.asset_id==asset_id]
    total=session.scalar(select(func.count()).select_from(AssetEvent).where(*filters))
    items=session.scalars(select(AssetEvent).where(*filters).order_by(AssetEvent.created_at.desc(),AssetEvent.id).offset(offset).limit(limit)).all()
    return {"items":[{"id":e.id,"kind":e.kind,"note":e.note,"event_date":e.event_date,"created_at":e.created_at,"before":e.before,"after":e.after} for e in items],"total":total}


@router.post("/{asset_id}/label")
def asset_label(asset_id:str,payload:AssetLabel,session:Db,owner:ProcessingOwner,settings:Config,auth:AccountAuthenticated):
    require_feature(session,owner.user,settings,"assets");require_feature(session,owner.user,settings,"label_templates")
    item=asset_owned(session,owner,asset_id)
    product={"barcode":item.asset_code,"barcode_type":"code128","sku":item.asset_code,"name":item.name,"price_minor":item.purchase_minor,"currency":item.currency}
    try:data=render_pdf(payload.definition,[product]) if payload.format=="pdf" else render_png(payload.definition,product) if payload.format=="png" else render_svg(payload.definition,product)
    except ValueError as exc:raise HTTPException(422,detail=str(exc)) from exc
    headers=consume_service_usage(session,auth,settings,"labels",1)
    headers.update({"Cache-Control":"no-store","Content-Disposition":f'attachment; filename="asset-label.{payload.format}"'})
    return Response(data,media_type={"pdf":"application/pdf","png":"image/png","svg":"image/svg+xml"}[payload.format],headers=headers)
