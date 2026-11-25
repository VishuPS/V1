from typing import Annotated
from fastapi import Depends
from app.api.vault_routes import Owner
from app.user_auth import UserContext
from app.processing import processing_slot


def bounded_processing(owner:Owner):
    with processing_slot():
        yield owner


ProcessingOwner=Annotated[UserContext,Depends(bounded_processing)]
