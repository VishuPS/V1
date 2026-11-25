"""Transparent quality checks. Reports never mutate customer products."""
from collections import Counter
from urllib.parse import urlsplit
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,Field
from sqlalchemy import select,func
from app.api.vault_routes import Db,Config
from app.api.workspace_dependencies import ProcessingOwner
from app.entitlements import require_feature,reserve_workspace_units
from app.models import VaultProduct,ProductIdentifier
from app.barcodes import parse_barcode

router=APIRouter(prefix="/v1/account/catalogue-auditor",tags=["catalogue quality"])
RULES={
    "duplicate_barcode":"A primary or additional identifier belongs to more than one product after trim/case normalisation.",
    "duplicate_sku":"A non-empty SKU is shared after trim/case normalisation.",
    "invalid_gtin":"A retail GTIN has an unsupported length, non-digit data or an invalid check digit.",
    "missing_name":"The product name is blank after trimming.",
    "missing_image":"No product image URL is saved.",
    "missing_price":"No selling price is saved. A price of zero is valid.",
    "inconsistent_category":"Category casing or whitespace differs from another product's equivalent category.",
    "invalid_format":"Image URL is not HTTP(S), currency is unsupported, price is negative, or text has forbidden control characters.",
}


class AuditInput(BaseModel):
    collection:str=Field(default="",max_length=100)


@router.post("/run")
def audit_catalogue(payload:AuditInput,session:Db,owner:ProcessingOwner,settings:Config):
    require_feature(session,owner.user,settings,"catalogue_auditing")
    filters=[VaultProduct.user_id==owner.user.id]
    if payload.collection:filters.append(VaultProduct.collection==payload.collection)
    count=session.scalar(select(func.count()).select_from(VaultProduct).where(*filters))
    maximum=min(10000,settings.workspace_audit_limit)
    if count>maximum:
        raise HTTPException(422,detail=f"This report supports up to {maximum} private products; no partial score was calculated.")
    products=[];size=0
    for row in session.execute(select(VaultProduct.id,VaultProduct.name,VaultProduct.barcode,VaultProduct.barcode_type,VaultProduct.sku,VaultProduct.image_url,VaultProduct.price_minor,VaultProduct.currency,VaultProduct.category).where(*filters).order_by(VaultProduct.id)).yield_per(200):
        size+=sum(len(v.encode('utf-8')) for v in row if isinstance(v,str))
        if size>12_000_000:raise HTTPException(422,detail="Audit data exceeds 12 MB. Choose a smaller collection; no partial score was calculated.")
        products.append(row)
    aliases=session.execute(select(ProductIdentifier.product_id,ProductIdentifier.value,ProductIdentifier.kind).where(ProductIdentifier.user_id==owner.user.id,ProductIdentifier.product_id.in_(select(VaultProduct.id).where(*filters)))).yield_per(500)
    identities={p.id:[(p.barcode,p.barcode_type)] for p in products}
    for pid,value,kind in aliases:
        size+=len(value.encode('utf-8'))
        if size>12_000_000:raise HTTPException(422,detail="Audit data exceeds 12 MB. Choose a smaller collection; no partial score was calculated.")
        if pid in identities:
            identities[pid].append((value,kind))
    def normal(value):
        try:return parse_barcode(value.strip()).gtin14
        except ValueError:return value.strip().casefold()
    identifiers={}
    for pid,values in identities.items():
        for value,_ in values:identifiers.setdefault(normal(value),set()).add(pid)
    skus=Counter(p.sku.strip().casefold() for p in products if p.sku.strip())
    categories={}
    category_key=lambda value:" ".join(value.split()).casefold()
    for p in products:
        if p.category.strip():
            categories.setdefault(category_key(p.category),set()).add(p.category)
    issues=[];counts={rule:0 for rule in RULES}
    for p in products:
        rules=[]
        if any(len(identifiers[normal(v)])>1 for v,_ in identities[p.id]):rules.append("duplicate_barcode")
        if p.sku.strip() and skus[p.sku.strip().casefold()]>1:rules.append("duplicate_sku")
        invalid=False
        for value,kind in identities[p.id]:
            retail=kind in {"ean13","ean8","upca","gtin14"} or (kind=="auto" and value.isdigit() and len(value) in {8,12,13,14})
            if retail:
                try:
                    parse_barcode(value)
                    if kind!="auto" and len(value)!={"ean8":8,"upca":12,"ean13":13,"gtin14":14}[kind]:raise ValueError("Length mismatch")
                except ValueError: invalid=True
        if invalid:rules.append("invalid_gtin")
        if not p.name.strip():rules.append("missing_name")
        if not p.image_url.strip():rules.append("missing_image")
        if p.price_minor is None:rules.append("missing_price")
        if p.category.strip() and len(categories[category_key(p.category)])>1:rules.append("inconsistent_category")
        try:
            image=urlsplit(p.image_url)
            invalid_image=bool(p.image_url) and (image.scheme not in {"https","http"} or not image.netloc or bool(image.username or image.password))
        except ValueError:invalid_image=True
        if invalid_image or p.currency not in {"USD","EUR","GBP"} or (p.price_minor is not None and p.price_minor<0) or any(ord(c)<32 for value in [p.name,p.barcode,p.sku,p.category] for c in value):rules.append("invalid_format")
        for rule in rules:counts[rule]+=1
        if rules:issues.append({"product_id":p.id,"name":p.name,"barcode":p.barcode,"rules":rules,"correction_url":"/catalogue/?product="+p.id})
    if count:reserve_workspace_units(session,owner.user,settings,"audit_rows",count)
    session.commit()
    from app.models import utcnow
    failed=sum(counts.values());possible=count*len(RULES)
    return {"generated_at":utcnow(),"collection":payload.collection or None,"products_checked":count,"score":round(100*(1-failed/possible),1) if possible else None,"formula":"100 × (1 − failed rule checks / (products × 8)); each rule has equal weight, at most one failure per product per rule in the selected scope. No products means no score.","rules":RULES,"counts":counts,"issues":issues,"snapshot":True,"mutations":0}
