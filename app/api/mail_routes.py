from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from app.admin_auth import AdminContext
from app.config import Settings, get_settings
from app.user_auth import ensure_allowed_origin
from app import mail_service as mail

router = APIRouter(prefix="/v1/admin/mail", tags=["admin email"])
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("/status")
def status(admin: AdminContext, settings: SettingsDep, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {"configured": bool(settings.support_mail_password), "address": settings.support_mail_address}


@router.get("/folders")
def folders(admin: AdminContext, settings: SettingsDep, response: Response):
    response.headers["Cache-Control"] = "no-store"
    with mail.mailbox(settings) as client:
        return {"items": mail._folders(client)}


@router.post("/connection")
def connection(request: Request, admin: AdminContext, settings: SettingsDep, response: Response):
    ensure_allowed_origin(request, settings)
    response.headers["Cache-Control"] = "no-store"
    return mail.check_connection(settings)


@router.get("/messages")
def messages(admin: AdminContext, settings: SettingsDep, response: Response,
             folder: str = Query(default="INBOX", max_length=400),
             q: str = Query(default="", max_length=200), page: int = Query(default=1, ge=1, le=10000)):
    response.headers["Cache-Control"] = "no-store"
    return mail.list_messages(settings, folder, q, page)


@router.get("/messages/{uid}")
def message(uid: int, admin: AdminContext, settings: SettingsDep, response: Response,
            folder: str = Query(default="INBOX", max_length=400), uid_validity: str = Query(pattern=r"^\d+$")):
    response.headers["Cache-Control"] = "no-store"
    if uid < 1:
        raise mail.mail_error(400, "invalid_message", "Invalid message identifier.")
    return mail.read_message(settings, folder, uid, uid_validity)


@router.get("/messages/{uid}/attachments/{index}")
def attachment(uid: int, index: int, admin: AdminContext, settings: SettingsDep,
               folder: str = Query(default="INBOX", max_length=400), uid_validity: str = Query(pattern=r"^\d+$")):
    if uid < 1 or index < 0:
        raise mail.mail_error(400, "invalid_attachment", "Invalid attachment identifier.")
    data, disposition = mail.download_attachment(settings, folder, uid, uid_validity, index)
    return Response(data, media_type="application/octet-stream", headers={
        "Content-Disposition": disposition, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
    })


@router.post("/messages/{uid}")
def update_message(uid: int, change: mail.MailMutation, request: Request,
                   admin: AdminContext, settings: SettingsDep, response: Response):
    ensure_allowed_origin(request, settings)
    response.headers["Cache-Control"] = "no-store"
    if uid < 1:
        raise mail.mail_error(400, "invalid_message", "Invalid message identifier.")
    return mail.mutate_message(settings, uid, change)


@router.post("/send")
def send(payload: mail.OutgoingMail, request: Request, admin: AdminContext,
         settings: SettingsDep, response: Response):
    ensure_allowed_origin(request, settings)
    response.headers["Cache-Control"] = "no-store"
    return mail.send_message(settings, payload, admin.user.id)
