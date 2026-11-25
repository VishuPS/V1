import hashlib
import re
from string import Formatter
from typing import Literal
from fastapi import APIRouter,HTTPException,Query
from pydantic import BaseModel,Field,field_validator
from sqlalchemy import select,func,or_
from app.api.vault_routes import Db,Owner,Config,owned,owned_many
from app.entitlements import require_feature,reserve_workspace_units
from app.models import User,VaultProduct,SkuSequence,SkuAllocation,utcnow
from app.workspace_io import TableUpload,read_table,table_response
from app.api.workspace_dependencies import ProcessingOwner
from app.workspace_receipts import fingerprint,replay,remember

router=APIRouter(prefix="/v1/account/skus",tags=["SKU manager"])


class SkuRequest(BaseModel):
    request_id:str|None=Field(default=None,min_length=16,max_length=64,pattern=r"^[A-Za-z0-9_-]+$")
    prefix:str=Field(default="SKU",max_length=20,pattern=r"^[A-Z0-9_-]*$")
    category_code:str=Field(default="",max_length=20,pattern=r"^[A-Z0-9_-]*$")
    pattern:str=Field(default="{prefix}-{number:06}",max_length=100)
    start:int=Field(default=1,ge=1,le=999999999)
    quantity:int=Field(default=1,ge=1,le=1000)
    product_ids:list[str]=Field(default_factory=list,max_length=1000)

    @field_validator("pattern")
    @classmethod
    def valid_pattern(cls,value):
        numbers=0
        try:
            for literal,field,spec,conversion in Formatter().parse(value):
                if not re.fullmatch(r"[A-Z0-9_.-]*",literal) or conversion:
                    raise ValueError("Use uppercase letters, numbers, dots, dashes and underscores")
                if field is not None:
                    if field not in {"prefix","category","number"}:
                        raise ValueError("Pattern tokens are {prefix}, {category} and {number:06}")
                    if field=="number":
                        numbers+=1
                        if spec and not re.fullmatch(r"0[1-9]|010",spec):
                            raise ValueError("Number padding must be from 1 to 10 digits")
                    elif spec:
                        raise ValueError("Only the number token accepts padding")
            if numbers!=1:
                raise ValueError("Include the number token exactly once")
        except (KeyError,IndexError) as exc:
            raise ValueError("Invalid numbering pattern") from exc
        return value


class SkuAssociation(BaseModel):
    product_id:str


def sku_json(item):
    return {"id":item.id,"code":item.code,"product_id":item.product_id,"category":item.category,"created_at":item.created_at}


@router.get("")
def list_skus(session:Db,owner:Owner,settings:Config,q:str=Query(default="",max_length=100),category:str=Query(default="",max_length=100),sort:Literal["newest","code"]="newest",offset:int=Query(default=0,ge=0),limit:int=Query(default=50,ge=1,le=100)):
    filters=[SkuAllocation.user_id==owner.user.id]
    if q:
        filters.append(SkuAllocation.code.icontains(q,autoescape=True))
    if category:
        filters.append(SkuAllocation.category==category)
    query=select(SkuAllocation).where(*filters)
    query=query.order_by(SkuAllocation.code if sort=="code" else SkuAllocation.created_at.desc(),SkuAllocation.id)
    items=session.scalars(query.offset(offset).limit(limit)).all()
    total=session.scalar(select(func.count()).select_from(SkuAllocation).where(*filters))
    return {"items":[sku_json(item) for item in items],"total":total,"offset":offset}


@router.post("/generate")
def generate_skus(payload:SkuRequest,session:Db,owner:Owner,settings:Config):
    require_feature(session,owner.user,settings,"sku_management")
    if payload.quantity>settings.workspace_batch_limit or len(payload.product_ids)>settings.workspace_batch_limit:
        raise HTTPException(422,detail={"code":"processing_limit","message":f"Process at most {settings.workspace_batch_limit} SKUs per request"})
    if payload.product_ids and (len(set(payload.product_ids))!=len(payload.product_ids) or len(payload.product_ids)!=payload.quantity):
        raise HTTPException(422,detail={"code":"invalid_products","message":"Choose one distinct product per generated SKU"})
    session.scalar(select(User).where(User.id==owner.user.id).with_for_update())
    digest=fingerprint(payload.model_dump(exclude={"request_id"}))
    previous=replay(session,owner.user.id,payload.request_id,"sku",digest)
    if previous is not None:return previous
    ledger_limit=settings.sku_record_limits.get(require_feature(session,owner.user,settings,"sku_management")["plan"],0)
    count=session.scalar(select(func.count()).select_from(SkuAllocation).where(SkuAllocation.user_id==owner.user.id))
    if count+payload.quantity>ledger_limit:
        raise HTTPException(409,detail="The SKU register capacity is reached. Existing allocations remain available.")
    products=owned_many(session,owner,payload.product_ids,lock=True)
    if any(product.sku for product in products):
        raise HTTPException(409,detail={"code":"sku_already_assigned","message":"A selected product already has an SKU. Clear or review it before generating another."})
    key=hashlib.sha256((payload.prefix+"\0"+payload.category_code+"\0"+payload.pattern).encode()).hexdigest()
    sequence=session.scalar(select(SkuSequence).where(SkuSequence.user_id==owner.user.id,SkuSequence.sequence_key==key).with_for_update())
    if sequence is None:
        sequence=SkuSequence(user_id=owner.user.id,sequence_key=key,prefix=payload.prefix,category_code=payload.category_code,pattern=payload.pattern,next_value=payload.start)
        session.add(sequence)
    used=set(session.scalars(select(SkuAllocation.code).where(SkuAllocation.user_id==owner.user.id)))
    used.update(value.strip().upper() for value in session.scalars(select(VaultProduct.sku).where(VaultProduct.user_id==owner.user.id,VaultProduct.sku!="")))
    generated=[];attempts=0
    while len(generated)<payload.quantity:
        attempts+=1
        if attempts>payload.quantity+10000 or sequence.next_value>9999999999:
            raise HTTPException(409,detail={"code":"sku_sequence_exhausted","message":"Choose another prefix or numbering pattern"})
        code=payload.pattern.format(prefix=payload.prefix,category=payload.category_code,number=sequence.next_value)
        sequence.next_value+=1
        if not re.fullmatch(r"[A-Z0-9_.-]{1,80}",code):
            raise HTTPException(422,detail={"code":"sku_too_long","message":"Generated SKUs must contain 1–80 characters for Code 128 output"})
        if code in used:
            continue
        product=products[len(generated)] if products else None
        item=SkuAllocation(user_id=owner.user.id,code=code,product_id=product.id if product else None,category=product.category if product else payload.category_code)
        session.add(item);generated.append(item);used.add(code)
        if product:
            product.sku=code;product.revision+=1;product.updated_at=utcnow()
    reserve_workspace_units(session,owner.user,settings,"sku_generation",len(generated))
    session.flush()
    from fastapi.encoders import jsonable_encoder
    result=jsonable_encoder({"items":[sku_json(item) for item in generated],"count":len(generated),"next_value":sequence.next_value})
    remember(session,owner.user.id,payload.request_id,"sku",digest,result)
    session.commit()
    return result


@router.put("/{sku_id}/product")
def associate_sku(sku_id:str,payload:SkuAssociation,session:Db,owner:Owner,settings:Config):
    require_feature(session,owner.user,settings,"sku_management")
    session.scalar(select(User).where(User.id==owner.user.id).with_for_update())
    item=session.scalar(select(SkuAllocation).where(SkuAllocation.id==sku_id,SkuAllocation.user_id==owner.user.id).with_for_update())
    if item is None:
        raise HTTPException(404,detail="SKU not found")
    product=owned(session,owner,payload.product_id,lock=True)
    if item.product_id and item.product_id!=product.id or product.sku and product.sku!=item.code:
        raise HTTPException(409,detail="The SKU or product is already associated. Existing assignments were kept.")
    duplicate=session.scalar(select(VaultProduct.id).where(VaultProduct.user_id==owner.user.id,VaultProduct.id!=product.id,func.upper(VaultProduct.sku)==item.code))
    if duplicate:
        raise HTTPException(409,detail="Another product already uses this SKU")
    item.product_id=product.id;item.category=product.category;product.sku=item.code;product.revision+=1;product.updated_at=utcnow();session.commit()
    return sku_json(item)


@router.get("/export")
def export_skus(session:Db,owner:ProcessingOwner,settings:Config,format:Literal["csv","xlsx","json"]="csv"):
    require_feature(session,owner.user,settings,"advanced_exports")
    items=session.scalars(select(SkuAllocation).where(SkuAllocation.user_id==owner.user.id).order_by(SkuAllocation.code).limit(10001)).all()
    if len(items)>10000:
        raise HTTPException(422,detail="Export at most 10,000 SKUs at once")
    return table_response([sku_json(item) for item in items],["code","product_id","category","created_at"],format,"barcodenest-skus")


@router.post("/import")
def import_skus(payload:TableUpload,session:Db,owner:ProcessingOwner,settings:Config):
    entitlements=require_feature(session,owner.user,settings,"sku_management")
    try:
        rows=read_table(payload,max_bytes=settings.workspace_upload_bytes,max_rows=settings.workspace_batch_limit)
    except ValueError as exc:
        raise HTTPException(422,detail={"code":"invalid_file","message":str(exc)}) from exc
    if rows and not ({"code","sku"}&set(rows[0])):
        raise HTTPException(422,detail="The file needs a code or sku column")
    session.scalar(select(User).where(User.id==owner.user.id).with_for_update())
    incoming=[row.get("code",row.get("sku","")).strip().upper() for row in rows]
    used=set(session.scalars(select(SkuAllocation.code).where(SkuAllocation.user_id==owner.user.id,SkuAllocation.code.in_(incoming))))
    available=settings.sku_record_limits.get(entitlements["plan"],0)-(session.scalar(select(func.count()).select_from(SkuAllocation).where(SkuAllocation.user_id==owner.user.id)) or 0)
    existing={value.strip().upper():pid for value,pid in session.execute(select(VaultProduct.sku,VaultProduct.id).where(VaultProduct.user_id==owner.user.id,VaultProduct.sku!=""))}
    results=[];accepted=0
    for index,row in enumerate(rows,2):
        code=row.get("code",row.get("sku","")).strip().upper();product_id=row.get("product_id","").strip()
        error=None;product=None
        if not re.fullmatch(r"[A-Z0-9_.-]{1,80}",code):
            error="Use a 1–80 character internal SKU with letters, digits, dots, dashes or underscores"
        elif code in used:
            error="SKU already allocated"
        elif code in existing and existing[code]!=product_id:
            error="Another product uses this SKU"
        if product_id and error is None:
            product=session.scalar(select(VaultProduct).where(VaultProduct.id==product_id,VaultProduct.user_id==owner.user.id))
            if not product or product.sku and product.sku.strip().upper()!=code:
                error="Product unavailable or already assigned another SKU"
        if error is None and accepted>=available:
            error="SKU register capacity reached"
        if error:
            results.append({"row":index,"status":"error","error":error});continue
        session.add(SkuAllocation(user_id=owner.user.id,code=code,product_id=product_id or None,category=(product.category if product else row.get("category","")[:100])))
        if product:
            product.sku=code;product.revision+=1;product.updated_at=utcnow()
        used.add(code);accepted+=1;results.append({"row":index,"status":"imported","code":code})
    reserve_workspace_units(session,owner.user,settings,"bulk_rows",len(rows))
    session.commit()
    return {"accepted":accepted,"results":results}
