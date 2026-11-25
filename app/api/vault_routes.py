import csv
import hashlib
import json
from io import StringIO
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select,update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.barcodes import BarcodeError, parse_barcode
from app.barcode_tools import validate_symbol
from app.config import Settings, get_settings
from app.db import get_db
from app.models import Subscription, User, VaultProduct, ProductIdentifier, SkuAllocation, utcnow
from app.auth import rate_limiter
from app.user_auth import UserContext, get_current_user, ensure_allowed_origin
from app.service_usage import record_activity
from app.entitlements import account_entitlements, require_feature

router = APIRouter(prefix="/v1/account/vault", tags=["private product vault"])
Db = Annotated[Session, Depends(get_db)]
Config = Annotated[Settings, Depends(get_settings)]


def vault_user(request: Request, response: Response, context: Annotated[UserContext, Depends(get_current_user)], settings: Config):
    ensure_allowed_origin(request, settings)
    rate_limiter.check("workspace:"+context.user.id,settings.workspace_requests_per_minute)
    response.headers["Cache-Control"]="no-store"
    return context


Owner = Annotated[UserContext, Depends(vault_user)]


class VaultInput(BaseModel):
    barcode: str = Field(min_length=1,max_length=500)
    barcode_type: Literal["auto","ean13","upca","ean8","gtin14","code128","code39","qr","datamatrix"] = "auto"
    name: str = Field(min_length=1,max_length=200)
    sku: str = Field(default="",max_length=100)
    brand: str = Field(default="",max_length=200)
    category: str = Field(default="",max_length=100)
    collection: str = Field(default="",max_length=100)
    supplier: str = Field(default="",max_length=200)
    tags: list[str] = Field(default_factory=list,max_length=20)
    variant_of: str | None = Field(default=None,max_length=36)
    notes: str = Field(default="",max_length=4000)
    image_url: str = Field(default="",max_length=2048)
    price_minor: int | None = Field(default=None,ge=0,le=999999999999)
    currency: Literal["USD","EUR","GBP"] = "USD"
    custom_fields: dict[str,str] = Field(default_factory=dict,max_length=20)

    @field_validator("barcode","name","sku","brand","category","collection","supplier","notes","image_url")
    @classmethod
    def safe_unicode(cls,value):
        if "\x00" in value:
            raise ValueError("Null bytes are not supported")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("Use valid Unicode text") from exc
        return value

    @field_validator("barcode","name")
    @classmethod
    def nonblank(cls,value,info):
        if not value.strip():
            raise ValueError("Barcode and product name cannot be blank")
        return value if info.field_name=="barcode" else value.strip()

    @field_validator("image_url")
    @classmethod
    def safe_image(cls,value):
        if value:
            url=urlsplit(value)
            if url.scheme != "https" or not url.hostname or url.username or url.password:
                raise ValueError("Image links must use HTTPS without embedded credentials")
        return value

    @field_validator("custom_fields")
    @classmethod
    def bounded_fields(cls,value):
        if any(not key.strip() or len(key)>64 or len(item)>500 for key,item in value.items()):
            raise ValueError("Custom fields need a name of 1–64 characters and a value of at most 500 characters")
        for key,item in value.items():
            if "\x00" in key or "\x00" in item:
                raise ValueError("Null bytes are not supported in custom fields")
            try:
                key.encode("utf-8");item.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError("Use valid Unicode in custom fields") from exc
        return value

    @field_validator("tags")
    @classmethod
    def bounded_tags(cls,value):
        if any(not tag.strip() or len(tag)>40 for tag in value):
            raise ValueError("Tags must contain 1–40 characters")
        for tag in value:
            if "\x00" in tag:
                raise ValueError("Null bytes are not supported in tags")
            try:
                tag.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError("Use valid Unicode tags") from exc
        return list(dict.fromkeys(tag.strip() for tag in value))


class VaultUpdate(VaultInput):
    revision: int = Field(ge=1)


def identity(payload: VaultInput):
    kind=payload.barcode_type
    parsed=None
    if kind in {"auto","ean13","upca","ean8","gtin14"}:
        try:
            parsed=parse_barcode(payload.barcode)
        except BarcodeError as exc:
            if kind != "auto":
                raise HTTPException(422,detail={"code":"invalid_barcode","message":str(exc)}) from exc
    if kind=="auto":
        kind=({8:"ean8",12:"upca",13:"ean13",14:"gtin14"}[len(payload.barcode)] if parsed
              else "code128" if len(payload.barcode)<=80 and all(32<=ord(c)<=126 for c in payload.barcode) else "qr")
    if kind in {"ean13","upca","code128","code39"}:
        try:
            validate_symbol(payload.barcode,kind)
        except ValueError as exc:
            raise HTTPException(422,detail={"code":"invalid_barcode","message":str(exc)}) from exc
    if kind in {"ean8","gtin14"} and len(payload.barcode)!={"ean8":8,"gtin14":14}[kind]:
        raise HTTPException(422,detail={"code":"invalid_barcode","message":"The barcode length does not match the selected format"})
    key="gtin:"+parsed.gtin14 if parsed else kind+":"+payload.barcode
    return kind,hashlib.sha256(key.encode()).hexdigest()


def plan_for(session,owner):
    subscription=session.scalar(select(Subscription).where(Subscription.user_id==owner.user.id).order_by(Subscription.created_at.desc()).limit(1))
    return subscription.plan_code if subscription else "FREE"


def summary(session,owner,settings):
    entitlements=account_entitlements(session,owner.user,settings)
    plan=entitlements["plan"]
    used=session.scalar(select(func.count()).select_from(VaultProduct).where(VaultProduct.user_id==owner.user.id))
    return {"plan":plan,"used":used,"capacity":entitlements["vault_capacity"],"export_included":entitlements["features"]["advanced_exports"]}


def serialize(item):
    return {key:getattr(item,key) for key in ["id","barcode","barcode_type","name","sku","brand","category","collection","supplier","tags","variant_of","notes","image_url","price_minor","currency","custom_fields","revision","created_at","updated_at"]}


def validate_product_links(session,owner,payload,item_id=None):
    if payload.sku.strip():
        query=select(VaultProduct.id).where(VaultProduct.user_id==owner.user.id,func.upper(func.trim(VaultProduct.sku))==payload.sku.strip().upper())
        if item_id:
            query=query.where(VaultProduct.id!=item_id)
        if session.scalar(query):
            raise HTTPException(409,detail={"code":"duplicate_sku","message":"Another saved product already has this SKU"})
        allocation=session.scalar(select(SkuAllocation).where(SkuAllocation.user_id==owner.user.id,SkuAllocation.code==payload.sku.strip().upper()))
        if allocation and allocation.product_id!=item_id:
            raise HTTPException(409,detail={"code":"sku_reserved","message":"This SKU is reserved in SKU Manager. Associate it there instead."})
    parent=payload.variant_of
    visited={item_id} if item_id else set()
    for _ in range(8):
        if not parent:
            return
        if parent in visited:
            raise HTTPException(422,detail="Variants cannot form a circular parent relationship")
        visited.add(parent)
        parent=owned(session,owner,parent).variant_of
    raise HTTPException(422,detail="Use at most eight variant levels")


def owned(session,owner,item_id,lock=False):
    if len(item_id)>36 or "\x00" in item_id:
        raise HTTPException(404,detail="Saved product not found")
    query=select(VaultProduct).where(VaultProduct.id==item_id,VaultProduct.user_id==owner.user.id)
    item=session.scalar(query.with_for_update() if lock else query)
    if not item:
        raise HTTPException(404,detail={"code":"vault_not_found","message":"Saved product not found"})
    return item


def owned_many(session,owner,item_ids,lock=False):
    if not item_ids:
        return []
    if len(item_ids)>1000 or len(set(item_ids))!=len(item_ids) or any(len(item_id)>36 or "\x00" in item_id for item_id in item_ids):
        raise HTTPException(422,detail="Use a bounded selection of distinct product IDs")
    query=select(VaultProduct).where(VaultProduct.user_id==owner.user.id,VaultProduct.id.in_(item_ids)).order_by(VaultProduct.id)
    records=session.scalars(query.with_for_update() if lock else query).all()
    if len(records)!=len(item_ids):
        raise HTTPException(404,detail="One or more selected products are unavailable")
    by_id={item.id:item for item in records}
    return [by_id[item_id] for item_id in item_ids]


@router.get("")
def list_products(session:Db,owner:Owner,settings:Config,q:str=Query(default="",max_length=200),collection:str=Query(default="",max_length=100),category:str=Query(default="",max_length=100),supplier:str=Query(default="",max_length=200),with_price:bool|None=None,sort:Literal["updated","name","sku"]="updated",offset:int=Query(default=0,ge=0),limit:int=Query(default=50,ge=1,le=100)):
    filters=[VaultProduct.user_id==owner.user.id]
    if q:
        aliases=select(ProductIdentifier.product_id).where(ProductIdentifier.user_id==owner.user.id,ProductIdentifier.value.icontains(q,autoescape=True))
        filters.append(or_(*[column.icontains(q,autoescape=True) for column in [VaultProduct.name,VaultProduct.barcode,VaultProduct.sku,VaultProduct.brand]],VaultProduct.id.in_(aliases)))
    if collection:
        filters.append(VaultProduct.collection==collection)
    if category:
        filters.append(VaultProduct.category==category)
    if supplier:
        filters.append(VaultProduct.supplier.icontains(supplier,autoescape=True))
    if with_price is not None:
        filters.append(VaultProduct.price_minor.is_not(None) if with_price else VaultProduct.price_minor.is_(None))
    order={"updated":VaultProduct.updated_at.desc(),"name":VaultProduct.name,"sku":VaultProduct.sku}[sort]
    items=session.scalars(select(VaultProduct).where(*filters).order_by(order,VaultProduct.id).offset(offset).limit(limit)).all()
    total=session.scalar(select(func.count()).select_from(VaultProduct).where(*filters))
    collections=session.scalars(select(VaultProduct.collection).where(VaultProduct.user_id==owner.user.id,VaultProduct.collection!="").distinct().order_by(VaultProduct.collection)).all()
    return {"items":[serialize(item) for item in items],"total":total,"offset":offset,"collections":collections,**summary(session,owner,settings)}


@router.get("/export")
def export_products(session:Db,owner:Owner,settings:Config,format:Literal["csv","json"]="csv",collection:str=Query(default="",max_length=100)):
    if not summary(session,owner,settings)["export_included"]:
        raise HTTPException(403,detail={"code":"paid_plan_required","message":"Vault export is included in Creator, Starter and Growth"})
    query=select(VaultProduct).where(VaultProduct.user_id==owner.user.id)
    if collection:
        query=query.where(VaultProduct.collection==collection)
    # Bound memory even if an operator configures a much larger storage plan.
    records=session.scalars(query.order_by(VaultProduct.name,VaultProduct.id).limit(10001)).all()
    if len(records)>10000:
        raise HTTPException(422,detail={"code":"export_too_large","message":"Export a collection with at most 10,000 products"})
    headers={"Content-Disposition":f'attachment; filename="barcodenest-vault.{format}"',"Cache-Control":"no-store"}
    if format=="json":
        content=json.dumps([serialize(r) for r in records],default=lambda v:v.isoformat(),ensure_ascii=False)
        record_activity(session,"user:"+owner.user.id,"vault_exports")
        return Response(content,media_type="application/json",headers=headers)
    fields=["barcode","barcode_type","name","sku","brand","category","collection","price_minor","currency","image_url","notes","custom_fields"]
    output=StringIO();writer=csv.DictWriter(output,fieldnames=fields);writer.writeheader()
    for record in records:
        values=serialize(record);values["custom_fields"]=json.dumps(values["custom_fields"],ensure_ascii=False)
        row={key:values[key] for key in fields}
        for key,value in row.items():
            if isinstance(value,str) and value.lstrip().startswith(("=","+","-","@")):
                row[key]="'"+value
        writer.writerow(row)
    content=output.getvalue().encode("utf-8-sig")
    record_activity(session,"user:"+owner.user.id,"vault_exports")
    return Response(content,media_type="text/csv",headers=headers)


@router.post("")
def save_product(payload:VaultInput,session:Db,owner:Owner,settings:Config):
    require_feature(session,owner.user,settings,"vault")
    kind,key=identity(payload)
    # Serialize account capacity checks across concurrent creates and deletes.
    session.scalar(select(User).where(User.id==owner.user.id).with_for_update())
    existing=session.scalar(select(VaultProduct).where(VaultProduct.user_id==owner.user.id,VaultProduct.identity_key==key))
    if existing:
        return {"item":serialize(existing),"created":False}
    alias=session.scalar(select(ProductIdentifier).where(ProductIdentifier.user_id==owner.user.id,ProductIdentifier.identity_key==key))
    if alias:
        return {"item":serialize(owned(session,owner,alias.product_id)),"created":False}
    validate_product_links(session,owner,payload)
    balance=summary(session,owner,settings)
    if balance["used"]>=balance["capacity"]:
        raise HTTPException(409,detail={"code":"vault_capacity_reached","message":"Your Vault is full. Remove a product or upgrade to save more. Existing records remain available."})
    values=payload.model_dump();values["barcode_type"]=kind
    item=VaultProduct(user_id=owner.user.id,identity_key=key,**values)
    session.add(item);session.flush()
    if item.sku:
        reservation=session.scalar(select(SkuAllocation).where(SkuAllocation.user_id==owner.user.id,SkuAllocation.code==item.sku.strip().upper(),SkuAllocation.product_id.is_(None)))
        if reservation:
            reservation.product_id=item.id
    session.commit();session.refresh(item)
    return {"item":serialize(item),"created":True}


@router.get("/{item_id}")
def get_saved_product(item_id:str,session:Db,owner:Owner):
    return serialize(owned(session,owner,item_id))


@router.put("/{item_id}")
def update_product(item_id:str,payload:VaultUpdate,session:Db,owner:Owner,settings:Config):
    require_feature(session,owner.user,settings,"vault")
    session.scalar(select(User).where(User.id==owner.user.id).with_for_update())
    item=owned(session,owner,item_id,lock=True)
    if payload.revision!=item.revision:
        raise HTTPException(409,detail={"code":"vault_edit_conflict","message":"This product changed since you opened it. Reopen it before editing again."})
    kind,key=identity(payload)
    validate_product_links(session,owner,payload,item_id)
    alias=session.scalar(select(ProductIdentifier).where(ProductIdentifier.user_id==owner.user.id,ProductIdentifier.identity_key==key,ProductIdentifier.product_id!=item_id))
    if alias:
        raise HTTPException(409,detail={"code":"vault_duplicate","message":"Another product already uses this identifier"})
    values=payload.model_dump(exclude={"revision"});values["barcode_type"]=kind
    for metadata_field in ["supplier","tags","variant_of"]:
        if metadata_field not in payload.model_fields_set:
            values.pop(metadata_field,None)
    if payload.sku!=item.sku:
        session.execute(update(SkuAllocation).where(SkuAllocation.user_id==owner.user.id,SkuAllocation.product_id==item.id,SkuAllocation.code!=payload.sku.strip().upper()).values(product_id=None))
    for name,value in values.items():
        setattr(item,name,value)
    item.identity_key=key;item.revision+=1;item.updated_at=utcnow()
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409,detail={"code":"vault_duplicate","message":"That barcode is already saved in your Vault"}) from exc
    session.refresh(item)
    return serialize(item)


@router.delete("/{item_id}",status_code=204)
def delete_product(item_id:str,session:Db,owner:Owner):
    session.scalar(select(User).where(User.id==owner.user.id).with_for_update())
    session.delete(owned(session,owner,item_id));session.commit()
    return Response(status_code=204)
