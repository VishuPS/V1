"""Read rich products in small batches instead of loading every alias into RAM."""
from sqlalchemy import select
from app.models import VaultProduct,ProductIdentifier


def product_rows(session,user_id,serialize,*,collection="",include_aliases=False):
    last=""
    while True:
        query=select(VaultProduct).where(VaultProduct.user_id==user_id,VaultProduct.id>last)
        if collection:query=query.where(VaultProduct.collection==collection)
        batch=session.scalars(query.order_by(VaultProduct.id).limit(100)).all()
        if not batch:break
        aliases={}
        if include_aliases:
            for pid,value,kind in session.execute(select(ProductIdentifier.product_id,ProductIdentifier.value,ProductIdentifier.kind).where(ProductIdentifier.user_id==user_id,ProductIdentifier.product_id.in_([p.id for p in batch]))):
                aliases.setdefault(pid,[]).append({"value":value,"kind":kind})
        for product in batch:
            row=serialize(product)
            if include_aliases:row['identifiers']=aliases.get(product.id,[])
            yield row
        last=batch[-1].id
