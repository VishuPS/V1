import json
from typing import Literal
from fastapi import APIRouter,HTTPException,Query
from pydantic import BaseModel,Field,ValidationError
from sqlalchemy import select,func
from app.api.vault_routes import Db,Owner,Config,VaultInput,identity,owned,owned_many,serialize,validate_product_links,summary
from app.entitlements import require_feature,reserve_workspace_units
from app.models import User,VaultProduct,ProductIdentifier,SkuAllocation,utcnow
from app.workspace_io import TableUpload,read_table,table_response
from app.api.workspace_dependencies import ProcessingOwner

router=APIRouter(prefix="/v1/account/catalogue",tags=["private product catalogue"])


class IdentifierInput(BaseModel):
    value:str=Field(min_length=1,max_length=500)
    kind:Literal["auto","ean13","upca","ean8","gtin14","code128","code39","qr","datamatrix"]="auto"


class IdentifierAdd(IdentifierInput):
    revision:int=Field(ge=1)


class BulkEdit(BaseModel):
    product_ids:list[str]=Field(min_length=1,max_length=1000)
    revisions:dict[str,int]
    updates:dict=Field(min_length=1,max_length=8)


class ProductSelection(BaseModel):
    product_ids:list[str]=Field(min_length=1,max_length=100)


@router.post("/selection")
def selected_products(payload:ProductSelection,session:Db,owner:Owner):
    return {"items":[serialize(item) for item in owned_many(session,owner,payload.product_ids)]}


@router.get("/{product_id}/identifiers")
def identifiers(product_id:str,session:Db,owner:Owner):
    owned(session,owner,product_id)
    items=session.scalars(select(ProductIdentifier).where(ProductIdentifier.user_id==owner.user.id,ProductIdentifier.product_id==product_id).order_by(ProductIdentifier.created_at)).all()
    return {"items":[{"id":item.id,"value":item.value,"kind":item.kind} for item in items]}


@router.post("/{product_id}/identifiers")
def add_identifier(product_id:str,payload:IdentifierAdd,session:Db,owner:Owner,settings:Config):
    require_feature(session,owner.user,settings,"vault")
    session.scalar(select(User).where(User.id==owner.user.id).with_for_update())
    product=owned(session,owner,product_id,lock=True)
    if product.revision!=payload.revision:
        raise HTTPException(409,detail="The product changed. Reopen it before adding an identifier.")
    kind,key=identity(VaultInput(barcode=payload.value,barcode_type=payload.kind,name=product.name))
    primary=session.scalar(select(VaultProduct.id).where(VaultProduct.user_id==owner.user.id,VaultProduct.identity_key==key))
    existing=session.scalar(select(ProductIdentifier).where(ProductIdentifier.user_id==owner.user.id,ProductIdentifier.identity_key==key))
    if primary or existing:
        raise HTTPException(409,detail="This identifier is already saved in your catalogue")
    count=session.scalar(select(func.count()).select_from(ProductIdentifier).where(ProductIdentifier.product_id==product_id,ProductIdentifier.user_id==owner.user.id))
    if count>=20:
        raise HTTPException(409,detail="Store at most 20 additional identifiers per product")
    item=ProductIdentifier(user_id=owner.user.id,product_id=product_id,identity_key=key,value=payload.value,kind=kind)
    session.add(item);product.revision+=1;product.updated_at=utcnow();session.commit()
    return {"id":item.id,"value":item.value,"kind":item.kind,"revision":product.revision}


@router.delete("/{product_id}/identifiers/{identifier_id}",status_code=204)
def remove_identifier(product_id:str,identifier_id:str,session:Db,owner:Owner,settings:Config,revision:int=Query(ge=1)):
    from fastapi import Response
    require_feature(session,owner.user,settings,"vault")
    session.scalar(select(User).where(User.id==owner.user.id).with_for_update())
    product=owned(session,owner,product_id,lock=True)
    if product.revision!=revision:
        raise HTTPException(409,detail="The product changed. Reopen it before removing an identifier.")
    item=session.scalar(select(ProductIdentifier).where(ProductIdentifier.id==identifier_id,ProductIdentifier.product_id==product_id,ProductIdentifier.user_id==owner.user.id))
    if not item:
        raise HTTPException(404,detail="Identifier not found")
    session.delete(item);product.revision+=1;product.updated_at=utcnow();session.commit()
    return Response(status_code=204)


@router.post("/bulk-edit")
def bulk_edit(payload:BulkEdit,session:Db,owner:Owner,settings:Config):
    require_feature(session,owner.user,settings,"bulk_editing")
    if len(payload.product_ids)>settings.workspace_batch_limit or len(set(payload.product_ids))!=len(payload.product_ids):
        raise HTTPException(422,detail="Use a bounded selection of distinct products")
    allowed={"category","collection","supplier","tags","price_minor","currency","notes"}
    if not set(payload.updates)<=allowed:
        raise HTTPException(422,detail="Only category, collection, supplier, tags, price, currency and notes support bulk edits")
    session.scalar(select(User).where(User.id==owner.user.id).with_for_update())
    records=owned_many(session,owner,sorted(payload.product_ids),lock=True)
    for item in records:
        if payload.revisions.get(item.id)!=item.revision:
            raise HTTPException(409,detail="A selected product changed. Reload before editing; no records were overwritten.")
        values={key:getattr(item,key) for key in VaultInput.model_fields}
        try:
            checked=VaultInput.model_validate({**values,**payload.updates})
        except ValidationError as exc:
            raise HTTPException(422,detail="The bulk edit contains invalid field values") from exc
        for key in payload.updates:
            setattr(item,key,getattr(checked,key))
        item.revision+=1;item.updated_at=utcnow()
    reserve_workspace_units(session,owner.user,settings,"bulk_rows",len(records))
    session.commit()
    return {"updated":len(records)}


@router.get("/export")
def export_catalogue(session:Db,owner:ProcessingOwner,settings:Config,format:Literal["csv","xlsx","json"]="csv",collection:str=Query(default="",max_length=100)):
    require_feature(session,owner.user,settings,"advanced_exports")
    from app.workspace_export import product_rows
    rows=product_rows(session,owner.user.id,serialize,collection=collection,include_aliases=True)
    return table_response(rows,["id",*VaultInput.model_fields,"identifiers"],format,"barcodenest-catalogue")


@router.post("/import")
def import_catalogue(payload:TableUpload,session:Db,owner:ProcessingOwner,settings:Config):
    require_feature(session,owner.user,settings,"bulk_editing")
    try:
        rows=read_table(payload,max_bytes=settings.workspace_upload_bytes,max_rows=settings.workspace_batch_limit)
    except ValueError as exc:
        raise HTTPException(422,detail={"code":"invalid_file","message":str(exc)}) from exc
    if rows and not {"barcode","name"}<=set(rows[0]):
        raise HTTPException(422,detail="The file needs barcode and name columns")
    session.scalar(select(User).where(User.id==owner.user.id).with_for_update())
    balance=summary(session,owner,settings);slots=max(0,balance["capacity"]-balance["used"])
    existing=set(session.scalars(select(VaultProduct.identity_key).where(VaultProduct.user_id==owner.user.id)))
    existing.update(session.scalars(select(ProductIdentifier.identity_key).where(ProductIdentifier.user_id==owner.user.id)))
    pending_skus=set();results=[];accepted=0
    for index,row in enumerate(rows,2):
        try:
            values={key:row[key] for key in VaultInput.model_fields if key in row}
            for key in ["tags","custom_fields"]:
                if key in values:
                    values[key]=json.loads(values[key]) if values[key] else ([] if key=="tags" else {})
            for key in ["price_minor","variant_of"]:
                if values.get(key)=="":
                    values[key]=None
            checked=VaultInput.model_validate(values);kind,key=identity(checked)
            if key in existing:
                results.append({"row":index,"status":"skipped","error":"Identifier already exists; existing product was kept"});continue
            if accepted>=slots:
                raise ValueError("Vault storage capacity reached")
            if checked.sku.strip().upper() in pending_skus and checked.sku.strip():
                raise ValueError("Duplicate SKU within this file")
            validate_product_links(session,owner,checked)
            alias_rows=json.loads(row.get("identifiers") or "[]")
            if not isinstance(alias_rows,list) or len(alias_rows)>20:
                raise ValueError("Use at most 20 additional identifiers")
            aliases=[];keys={key}
            for raw in alias_rows:
                alias=IdentifierInput.model_validate(raw);alias_kind,alias_key=identity(VaultInput(barcode=alias.value,barcode_type=alias.kind,name=checked.name))
                if alias_key in existing or alias_key in keys:
                    raise ValueError("Duplicate additional identifier")
                aliases.append((alias,alias_kind,alias_key));keys.add(alias_key)
            item=VaultProduct(user_id=owner.user.id,identity_key=key,**{**checked.model_dump(),"barcode_type":kind})
            session.add(item);session.flush()
            if item.sku:
                reservation=session.scalar(select(SkuAllocation).where(SkuAllocation.user_id==owner.user.id,SkuAllocation.code==item.sku.strip().upper(),SkuAllocation.product_id.is_(None)))
                if reservation:
                    reservation.product_id=item.id
            for alias,alias_kind,alias_key in aliases:
                session.add(ProductIdentifier(user_id=owner.user.id,product_id=item.id,identity_key=alias_key,value=alias.value,kind=alias_kind))
            existing.update(keys);pending_skus.add(checked.sku.strip().upper());accepted+=1
            results.append({"row":index,"status":"imported","product_id":item.id})
        except (ValueError,ValidationError,HTTPException) as exc:
            detail=exc.detail if isinstance(exc,HTTPException) else str(exc)
            if isinstance(detail,dict):
                detail=detail.get("message","Invalid record")
            results.append({"row":index,"status":"error","error":str(detail)[:500]})
    reserve_workspace_units(session,owner.user,settings,"bulk_rows",len(rows))
    session.commit()
    return {"accepted":accepted,"results":results,"processed":len(rows),"note":"Existing records were never overwritten"}
