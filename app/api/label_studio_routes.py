from typing import Literal
from fastapi import APIRouter,HTTPException,Response
from pydantic import BaseModel,Field,field_validator
from sqlalchemy import select,func
from sqlalchemy.exc import IntegrityError
from app.api.vault_routes import Db,Owner,Config,owned_many,serialize
from app.entitlements import require_feature
from app.label_studio import LabelDefinition,render_png,render_svg,render_pdf
from app.models import LabelTemplate,User,utcnow
from app.processing import processing_slot
from app.api.tool_routes import account_tool_context
from app.auth import AuthContext
from app.service_usage import consume_service_usage
from fastapi import Depends
from typing import Annotated
from app.api.workspace_dependencies import ProcessingOwner

router=APIRouter(prefix="/v1/account/label-studio",tags=["label studio"])
AccountTool=Annotated[AuthContext,Depends(account_tool_context)]


class TemplateInput(BaseModel):
    name:str=Field(min_length=1,max_length=100)
    definition:LabelDefinition

    @field_validator("name")
    @classmethod
    def valid_name(cls,value):
        from app.api.vault_routes import VaultInput
        return VaultInput.safe_unicode(value)


class TemplateUpdate(TemplateInput):
    revision:int=Field(ge=1)


class StudioRender(BaseModel):
    definition:LabelDefinition
    product_ids:list[str]=Field(min_length=1,max_length=100)
    format:Literal["svg","png","pdf"]="pdf"


def template_json(item):
    return {"id":item.id,"name":item.name,"definition":item.definition,"revision":item.revision,"updated_at":item.updated_at}


@router.get("/templates")
def templates(session:Db,owner:Owner,settings:Config):
    items=session.execute(select(LabelTemplate.id,LabelTemplate.name,LabelTemplate.revision,LabelTemplate.updated_at).where(LabelTemplate.user_id==owner.user.id).order_by(LabelTemplate.updated_at.desc()).limit(100)).mappings().all()
    return {"items":[dict(item) for item in items]}


@router.post("/templates")
def create_template(payload:TemplateInput,session:Db,owner:ProcessingOwner,settings:Config):
    entitlements=require_feature(session,owner.user,settings,"label_templates")
    session.scalar(select(User).where(User.id==owner.user.id).with_for_update())
    used=session.scalar(select(func.count()).select_from(LabelTemplate).where(LabelTemplate.user_id==owner.user.id))
    if used>=settings.label_template_limits.get(entitlements["plan"],0):
        raise HTTPException(409,detail="Your template capacity is full. Remove a template or upgrade; existing designs remain readable.")
    item=LabelTemplate(user_id=owner.user.id,name=payload.name.strip(),definition=payload.definition.model_dump())
    if not item.name:
        raise HTTPException(422,detail="A template name is required")
    session.add(item)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback();raise HTTPException(409,detail="Choose a unique template name") from exc
    return template_json(item)


def owned_template(session,owner,template_id,lock=True):
    query=select(LabelTemplate).where(LabelTemplate.user_id==owner.user.id,LabelTemplate.id==template_id)
    item=session.scalar(query.with_for_update() if lock else query)
    if not item:
        raise HTTPException(404,detail="Template not found")
    return item


@router.get("/templates/{template_id}")
def get_template(template_id:str,session:Db,owner:Owner):
    return template_json(owned_template(session,owner,template_id,lock=False))


@router.put("/templates/{template_id}")
def update_template(template_id:str,payload:TemplateUpdate,session:Db,owner:ProcessingOwner,settings:Config):
    require_feature(session,owner.user,settings,"label_templates")
    item=owned_template(session,owner,template_id)
    if item.revision!=payload.revision:
        raise HTTPException(409,detail="This template changed. Reopen it before editing again.")
    if not payload.name.strip():
        raise HTTPException(422,detail="A template name is required")
    item.name=payload.name.strip();item.definition=payload.definition.model_dump();item.revision+=1;item.updated_at=utcnow()
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback();raise HTTPException(409,detail="Choose a unique template name") from exc
    return template_json(item)


@router.delete("/templates/{template_id}",status_code=204)
def remove_template(template_id:str,session:Db,owner:Owner):
    # Customers may remove retained templates after a downgrade.
    session.delete(owned_template(session,owner,template_id));session.commit();return Response(status_code=204)


@router.post("/render")
def render_design(payload:StudioRender,session:Db,owner:ProcessingOwner,settings:Config,auth:AccountTool):
    require_feature(session,owner.user,settings,"label_templates")
    if len(set(payload.product_ids))!=len(payload.product_ids) or len(payload.product_ids)>settings.workspace_batch_limit:
        raise HTTPException(422,detail="Choose a bounded set of distinct products")
    if payload.format!="pdf" and len(payload.product_ids)!=1:
        raise HTTPException(422,detail="SVG and PNG exports contain one label. Choose PDF for a product selection.")
    products=[serialize(item) for item in owned_many(session,owner,payload.product_ids)]
    try:
        content=render_pdf(payload.definition,products) if payload.format=="pdf" else render_png(payload.definition,products[0]) if payload.format=="png" else render_svg(payload.definition,products[0])
    except ValueError as exc:
        raise HTTPException(422,detail={"code":"invalid_label_design","message":str(exc)}) from exc
    headers=consume_service_usage(session,auth,settings,"labels",len(products))
    headers.update({"Content-Disposition":f'attachment; filename="barcodenest-labels.{payload.format}"',"Cache-Control":"no-store"})
    return Response(content,media_type={"pdf":"application/pdf","png":"image/png","svg":"image/svg+xml"}[payload.format],headers=headers)
