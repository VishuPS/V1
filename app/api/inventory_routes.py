"""Private inventory. Balances and append-only movements commit together."""
from typing import Literal
from fastapi import APIRouter,HTTPException,Query
from pydantic import BaseModel,Field,field_validator
from sqlalchemy import select,func,update,or_,case,cast,Numeric
from app.api.vault_routes import Db,Owner,Config,owned,VaultInput
from app.api.workspace_dependencies import ProcessingOwner
from app.models import InventoryItem,StockMovement,VaultProduct,ProductIdentifier,User,utcnow
from app.entitlements import require_feature
from app.workspace_io import table_response

router=APIRouter(prefix="/v1/account/inventory",tags=["private inventory"])


class InventoryInput(BaseModel):
    product_id:str=Field(max_length=36)
    low_stock_threshold:int=Field(default=0,ge=0,le=1000000000)
    location:str=Field(default="",max_length=200)
    purchase_minor:int|None=Field(default=None,ge=0,le=999999999999)
    currency:Literal["USD","EUR","GBP"]="USD"
    revision:int|None=Field(default=None,ge=1)
    archived:bool=False

    @field_validator("location")
    @classmethod
    def safe_location(cls,value):
        return VaultInput.safe_unicode(value)


class MovementInput(BaseModel):
    request_id:str=Field(min_length=16,max_length=64,pattern=r"^[A-Za-z0-9_-]+$")
    delta:int=Field(ge=-1000000000,le=1000000000)
    reason:str=Field(min_length=1,max_length=500)

    @field_validator("reason")
    @classmethod
    def safe_reason(cls,value):
        return VaultInput.safe_unicode(value).strip() or "Stock adjustment"


def serialize(item,product):
    return {"id":item.id,"product_id":item.product_id,"name":product.name,"barcode":product.barcode,"sku":product.sku,"category":product.category,"supplier":product.supplier,"quantity":item.quantity,"low_stock_threshold":item.low_stock_threshold,"low_stock":item.quantity<=item.low_stock_threshold,"location":item.location,"purchase_minor":item.purchase_minor,"selling_minor":product.price_minor,"selling_currency":product.currency,"currency":item.currency,"revision":item.revision,"archived":item.archived,"updated_at":item.updated_at}


def movement_json(item):
    return {"id":item.id,"inventory_id":item.inventory_id,"request_id":item.request_id,"delta":item.delta,"balance":item.balance,"reason":item.reason,"created_at":item.created_at}


def inventory_owned(session,owner,item_id):
    if not item_id or len(item_id)>36 or "\x00" in item_id:raise HTTPException(404,detail="Inventory record not found")
    item=session.scalar(select(InventoryItem).where(InventoryItem.id==item_id,InventoryItem.user_id==owner.user.id))
    if not item:
        raise HTTPException(404,detail="Inventory record not found")
    return item


@router.post("")
def save_inventory(payload:InventoryInput,session:Db,owner:Owner,settings:Config):
    require_feature(session,owner.user,settings,"inventory")
    session.scalar(select(User).where(User.id==owner.user.id).with_for_update())
    product=owned(session,owner,payload.product_id)
    item=session.scalar(select(InventoryItem).where(InventoryItem.user_id==owner.user.id,InventoryItem.product_id==product.id).with_for_update())
    if item:
        if payload.revision!=item.revision:
            raise HTTPException(409,detail="Inventory changed. Refresh before saving.")
        if payload.archived and item.quantity:
            raise HTTPException(409,detail="Remove remaining stock before archiving.")
        item.revision+=1
    else:
        if payload.revision is not None or payload.archived:
            raise HTTPException(409,detail="Create a new active record without a revision.")
        item=InventoryItem(user_id=owner.user.id,product_id=product.id,quantity=0)
        session.add(item)
        from app.funnel import record_event_safely
        record_event_safely(session,"inventory_created",user_id=owner.user.id,tool="inventory")
    for key in ["low_stock_threshold","location","purchase_minor","archived","currency"]:
        setattr(item,key,getattr(payload,key))
    item.updated_at=utcnow();session.commit();session.refresh(item)
    return serialize(item,product)


@router.post("/{item_id}/movements")
def move_stock(item_id:str,payload:MovementInput,session:Db,owner:Owner,settings:Config):
    require_feature(session,owner.user,settings,"inventory")
    if not payload.delta:
        raise HTTPException(422,detail="Stock movement cannot be zero")
    # Account lock serializes idempotency across different inventory records too.
    session.scalar(select(User).where(User.id==owner.user.id).with_for_update())
    item=inventory_owned(session,owner,item_id)
    previous=session.scalar(select(StockMovement).where(StockMovement.user_id==owner.user.id,StockMovement.request_id==payload.request_id))
    if previous:
        if previous.inventory_id!=item.id or previous.delta!=payload.delta or previous.reason!=payload.reason:
            raise HTTPException(409,detail="Request identifier already used for another movement")
        return {**movement_json(previous),"replayed":True}
    if item.archived:
        raise HTTPException(409,detail="Restore the archived inventory record before adding stock")
    balance=session.execute(update(InventoryItem).where(InventoryItem.id==item.id,InventoryItem.user_id==owner.user.id,InventoryItem.quantity+payload.delta>=0,InventoryItem.quantity+payload.delta<=1000000000).values(quantity=InventoryItem.quantity+payload.delta,revision=InventoryItem.revision+1,updated_at=utcnow()).returning(InventoryItem.quantity)).scalar_one_or_none()
    if balance is None:
        raise HTTPException(409,detail="Insufficient stock or maximum quantity exceeded")
    movement=StockMovement(user_id=owner.user.id,inventory_id=item.id,request_id=payload.request_id,delta=payload.delta,balance=balance,reason=payload.reason)
    session.add(movement);session.commit();session.refresh(movement)
    return {**movement_json(movement),"replayed":False}


def inventory_query(owner,q="",low_stock=False,archived=False,location=""):
    filters=[InventoryItem.user_id==owner.user.id,InventoryItem.archived==archived,VaultProduct.user_id==owner.user.id]
    if q:
        aliases=select(ProductIdentifier.product_id).where(ProductIdentifier.user_id==owner.user.id,ProductIdentifier.value==q)
        filters.append(or_(VaultProduct.name.icontains(q,autoescape=True),VaultProduct.barcode==q,VaultProduct.sku==q,VaultProduct.id.in_(aliases)))
    if low_stock:
        filters.append(InventoryItem.quantity<=InventoryItem.low_stock_threshold)
    if location:
        filters.append(InventoryItem.location==location)
    return select(InventoryItem,VaultProduct).join(VaultProduct,InventoryItem.product_id==VaultProduct.id).where(*filters)


@router.get("")
def list_inventory(session:Db,owner:Owner,q:str=Query(default="",max_length=200),low_stock:bool=False,archived:bool=False,location:str=Query(default="",max_length=200),offset:int=Query(default=0,ge=0),limit:int=Query(default=50,ge=1,le=100)):
    query=inventory_query(owner,q,low_stock,archived,location)
    total=session.scalar(select(func.count()).select_from(query.subquery()))
    return {"items":[serialize(i,p) for i,p in session.execute(query.order_by(InventoryItem.updated_at.desc(),InventoryItem.id).offset(offset).limit(limit))],"total":total}


@router.get("/dashboard")
def inventory_dashboard(session:Db,owner:Owner):
    filters=[InventoryItem.user_id==owner.user.id,InventoryItem.archived==False]
    totals=session.execute(select(func.count(),func.coalesce(func.sum(InventoryItem.quantity),0),func.coalesce(func.sum(case((InventoryItem.quantity<=InventoryItem.low_stock_threshold,1),else_=0)),0),func.coalesce(func.sum(case((InventoryItem.quantity==0,1),else_=0)),0)).where(*filters)).one()
    # Currency-separated totals; no fabricated currency conversions.
    values=session.execute(select(InventoryItem.currency,func.coalesce(func.sum(cast(InventoryItem.quantity,Numeric(38,0))*InventoryItem.purchase_minor),0),func.coalesce(func.sum(case((InventoryItem.purchase_minor==None,1),else_=0)),0)).where(*filters).group_by(InventoryItem.currency)).all()
    recent=session.scalars(select(StockMovement).where(StockMovement.user_id==owner.user.id).order_by(StockMovement.created_at.desc(),StockMovement.id).limit(20)).all()
    return {"total_products":totals[0],"stock_units":totals[1],"low_stock_products":totals[2],"out_of_stock_products":totals[3],"valuation":[{"currency":c,"purchase_value_minor":str(int(v)),"missing_purchase_prices":m} for c,v,m in values],"recent_movements":[movement_json(m) for m in recent]}


@router.get("/{item_id}/movements")
def movement_history(item_id:str,session:Db,owner:Owner,offset:int=Query(default=0,ge=0),limit:int=Query(default=50,ge=1,le=100)):
    inventory_owned(session,owner,item_id)
    query=select(StockMovement).where(StockMovement.user_id==owner.user.id,StockMovement.inventory_id==item_id)
    total=session.scalar(select(func.count()).select_from(query.subquery()))
    return {"items":[movement_json(m) for m in session.scalars(query.order_by(StockMovement.created_at.desc(),StockMovement.id).offset(offset).limit(limit))],"total":total}


@router.get("/export")
def export_inventory(session:Db,owner:ProcessingOwner,settings:Config,format:Literal["csv","xlsx","json"]="csv"):
    require_feature(session,owner.user,settings,"advanced_exports")
    rows=session.execute(inventory_query(owner).order_by(InventoryItem.id).limit(10001)).all()
    if len(rows)>10000:
        raise HTTPException(422,detail="Export supports up to 10,000 active inventory records")
    data=[serialize(i,p) for i,p in rows]
    fields=["id","product_id","name","barcode","sku","category","supplier","quantity","low_stock_threshold","location","purchase_minor","currency","selling_minor","selling_currency"]
    return table_response(data,fields,format,"inventory")
