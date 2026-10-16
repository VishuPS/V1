"""Admin mailbox client. Credentials and message contents are never logged."""
import base64
import binascii
import imaplib
import re
import smtplib
import socket
import ssl
import threading
import time
from contextlib import contextmanager
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formatdate, getaddresses, make_msgid
from html.parser import HTMLParser
from urllib.parse import quote

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

from app.config import Settings

MAX_MESSAGE_BYTES = 15 * 1024 * 1024
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
_connections = threading.BoundedSemaphore(3)
_send_lock = threading.Lock()
_sent: dict[str, tuple[float, dict]] = {}


def mail_error(status: int, code: str, message: str):
    return HTTPException(status_code=status, detail={"code": code, "message": message})


class OutgoingAttachment(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    content: str = Field(max_length=12_000_000)


class OutgoingMail(BaseModel):
    request_id: str = Field(pattern=r"^[a-zA-Z0-9-]{16,80}$")
    to: str = Field(min_length=3, max_length=2000)
    cc: str = Field(default="", max_length=2000)
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=200_000)
    in_reply_to: str = Field(default="", max_length=1000)
    references: str = Field(default="", max_length=2000)
    attachments: list[OutgoingAttachment] = Field(default_factory=list, max_length=10)

    @field_validator("to", "cc", "subject", "in_reply_to", "references")
    @classmethod
    def no_header_injection(cls, value):
        if "\r" in value or "\n" in value or "\x00" in value:
            raise ValueError("Mail headers must be a single line")
        return value


class MailMutation(BaseModel):
    folder: str = Field(default="INBOX", min_length=1, max_length=400)
    uid_validity: str = Field(pattern=r"^\d+$")
    seen: bool | None = None
    destination: str | None = Field(default=None, min_length=1, max_length=400)


def _quote(value: str) -> str:
    if any(c in value for c in ("\r", "\n", "\x00")):
        raise mail_error(400, "invalid_folder", "Invalid mailbox folder.")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _decode_folder(value: str) -> str:
    def decode(match):
        data = match.group(1)
        if not data:
            return "&"
        try:
            return base64.b64decode(data.replace(",", "/") + "=" * (-len(data) % 4)).decode("utf-16-be")
        except (ValueError, UnicodeError):
            return match.group(0)
    return re.sub(r"&([^-]*)-", decode, value)


def _folders(client):
    status, rows = client.list()
    if status != "OK":
        raise mail_error(502, "mail_folders_unavailable", "Mailbox folders could not be loaded.")
    folders = []
    for row in rows or []:
        if not isinstance(row, bytes):
            continue
        match = re.match(rb'\(([^)]*)\)\s+(?:"[^"]*"|NIL)\s+(.+)$', row)
        if not match:
            continue
        flags = match[1].decode("ascii", "replace").split()
        if "\\Noselect" in flags:
            continue
        raw = match[2].decode("ascii", "replace")
        if raw.startswith('"') and raw.endswith('"'):
            raw = re.sub(r"\\(.)", r"\1", raw[1:-1])
        folders.append({"id": raw, "name": _decode_folder(raw), "flags": flags})
    return folders


@contextmanager
def mailbox(settings: Settings):
    if not settings.support_mail_password:
        raise mail_error(503, "mail_not_configured", "Connect the support mailbox by adding SUPPORT_MAIL_PASSWORD in Render Environment.")
    if not _connections.acquire(timeout=2):
        raise mail_error(503, "mail_busy", "The mailbox is busy. Please try again shortly.")
    client = None
    try:
        client = imaplib.IMAP4_SSL("mail.spacemail.com", 993, ssl_context=ssl.create_default_context(), timeout=20)
        client.login(settings.support_mail_address, settings.support_mail_password.get_secret_value())
        yield client
    except imaplib.IMAP4.error as exc:
        raise mail_error(502, "mail_connection_failed", "Spacemail could not complete the request. Check the mailbox credentials and IMAP access.") from exc
    except (OSError, socket.timeout, ssl.SSLError) as exc:
        raise mail_error(502, "mail_connection_failed", "The connection to Spacemail failed. Please try again.") from exc
    finally:
        if client is not None:
            try:
                client.logout()
            except Exception:
                pass
        _connections.release()


def select_folder(client, folder: str, expected: str | None = None, *, readonly=True):
    if folder not in {item["id"] for item in _folders(client)}:
        raise mail_error(404, "mail_folder_not_found", "This mailbox folder no longer exists.")
    status, _ = client.select(_quote(folder), readonly=readonly)
    if status != "OK":
        raise mail_error(404, "mail_folder_not_found", "This mailbox folder could not be opened.")
    _, values = client.response("UIDVALIDITY")
    validity = (values or [b""])[0].decode("ascii", "replace")
    if not validity.isdigit():
        raise mail_error(502, "mail_identity_unavailable", "The mailbox could not verify message identifiers.")
    if expected is not None and validity != expected:
        raise mail_error(409, "mail_folder_changed", "This folder changed. Refresh the message list and try again.")
    return validity


def _fetch(client, uid, fields):
    status, data = client.uid("FETCH", str(uid), fields)
    if status != "OK":
        raise mail_error(502, "mail_fetch_failed", "The message could not be loaded.")
    for row in data or []:
        if isinstance(row, tuple) and isinstance(row[1], bytes):
            return row[0], row[1]
    raise mail_error(404, "mail_message_not_found", "This message is no longer in this folder.")


def _metadata(message, uid, info=b""):
    flags = re.search(rb"FLAGS \(([^)]*)\)", info)
    return {
        "uid": str(uid), "subject": str(message.get("Subject", "(No subject)")),
        "sender": str(message.get("From", "")), "to": str(message.get("To", "")),
        "cc": str(message.get("Cc", "")), "date": str(message.get("Date", "")),
        "seen": b"\\Seen" in (flags[1] if flags else b""),
        "message_id": str(message.get("Message-ID", "")),
        "reply_to": str(message.get("Reply-To", message.get("From", ""))),
        "references": str(message.get("References", "")),
    }


def list_messages(settings, folder, query, page):
    with mailbox(settings) as client:
        validity = select_folder(client, folder)
        criteria = b"UNDELETED"
        charset = None
        if query:
            if any(c in query for c in ("\r", "\n", "\x00")):
                raise mail_error(400, "invalid_search", "Search must be a single line.")
            criteria += b" TEXT " + _quote(query).encode("utf-8")
            charset = "UTF-8"
        status, rows = client.uid("SEARCH", charset, criteria)
        if status != "OK":
            raise mail_error(502, "mail_search_failed", "Spacemail could not search this folder.")
        uids = (rows[0] or b"").split()[::-1]
        items = []
        for uid in uids[(page - 1) * 20:page * 20]:
            try:
                info, headers = _fetch(client, uid.decode(), "(UID FLAGS BODY.PEEK[HEADER.FIELDS (FROM TO CC SUBJECT DATE MESSAGE-ID REPLY-TO REFERENCES)])")
                items.append(_metadata(BytesParser(policy=policy.default).parsebytes(headers), uid.decode(), info))
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
        return {"items": items, "total": len(uids), "page": page, "uid_validity": validity}


class _TextHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.hidden += 1
        if tag in ("br", "p", "div", "li", "tr") and not self.hidden:
            self.parts.append("\n")
    def handle_endtag(self, tag):
        if tag in ("script", "style", "head") and self.hidden:
            self.hidden -= 1
    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _body(message):
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, UnicodeError):
        content = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
    if not isinstance(content, str):
        return ""
    if part.get_content_type() == "text/html":
        parser = _TextHTML()
        parser.feed(content)
        content = "".join(parser.parts)
    return content[:200_000]


def _attachments(message):
    return [part for part in message.walk() if not part.is_multipart() and
            (part.get_filename() or part.get_content_disposition() == "attachment")]


def _load_message(client, uid):
    status, data = client.uid("FETCH", str(uid), "(RFC822.SIZE)")
    raw = b" ".join(row for row in data or [] if isinstance(row, bytes))
    size = re.search(rb"RFC822.SIZE (\d+)", raw)
    if status != "OK" or size is None:
        raise mail_error(404, "mail_message_not_found", "This message is no longer available.")
    if int(size[1]) > MAX_MESSAGE_BYTES:
        raise mail_error(413, "mail_message_too_large", "This message exceeds 15 MB. Open it in Spacemail webmail.")
    info, raw = _fetch(client, uid, "(UID FLAGS BODY.PEEK[])")
    return BytesParser(policy=policy.default).parsebytes(raw), info


def read_message(settings, folder, uid, validity):
    with mailbox(settings) as client:
        select_folder(client, folder, validity)
        message, info = _load_message(client, uid)
        return {**_metadata(message, uid, info), "body": _body(message),
                "attachments": [{"index": i, "name": part.get_filename() or f"attachment-{i + 1}",
                                 "size": len(part.get_payload(decode=True) or b"")}
                                for i, part in enumerate(_attachments(message))]}


def download_attachment(settings, folder, uid, validity, index):
    with mailbox(settings) as client:
        select_folder(client, folder, validity)
        message, _ = _load_message(client, uid)
        parts = _attachments(message)
        if index >= len(parts):
            raise mail_error(404, "mail_attachment_not_found", "This attachment is not available.")
        part = parts[index]
        name = (part.get_filename() or "attachment").replace("\r", "").replace("\n", "").replace("\x00", "")
        return part.get_payload(decode=True) or b"", "attachment; filename*=UTF-8''" + quote(name, safe="")


def mutate_message(settings, uid, change):
    with mailbox(settings) as client:
        select_folder(client, change.folder, change.uid_validity, readonly=False)
        # Existence check prevents an apparently successful STORE on a stale UID.
        _fetch(client, uid, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
        if change.destination:
            if change.destination == change.folder:
                return {"ok": True}
            if change.destination not in {f["id"] for f in _folders(client)}:
                raise mail_error(404, "mail_folder_not_found", "The destination folder does not exist.")
            if b"MOVE" not in client.capabilities:
                raise mail_error(409, "mail_move_unavailable", "This server does not support safe message moves. Use Spacemail webmail.")
            status, _ = client.uid("MOVE", str(uid), _quote(change.destination))
        elif change.seen is not None:
            status, _ = client.uid("STORE", str(uid), "+FLAGS.SILENT" if change.seen else "-FLAGS.SILENT", "(\\Seen)")
        else:
            raise mail_error(400, "mail_action_required", "Choose a message action.")
        if status != "OK":
            raise mail_error(502, "mail_update_failed", "Spacemail could not update the message.")
        return {"ok": True}


def _recipients(value):
    if not value.strip():
        return []
    addresses = getaddresses([value])
    if value and not addresses:
        raise mail_error(422, "mail_invalid_recipient", "Enter a valid email address.")
    for _, address in addresses:
        if not re.fullmatch(r"[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+", address):
            raise mail_error(422, "mail_invalid_recipient", "Enter valid recipient email addresses separated by commas.")
    return [address for _, address in addresses]


def check_connection(settings):
    with mailbox(settings) as client:
        folders = _folders(client)
    try:
        with smtplib.SMTP_SSL("mail.spacemail.com", 465, timeout=20, context=ssl.create_default_context()) as smtp:
            smtp.login(settings.support_mail_address, settings.support_mail_password.get_secret_value())
    except (smtplib.SMTPException, OSError) as exc:
        raise mail_error(502, "mail_smtp_failed", "Incoming mail connected, but outgoing SMTP access failed. Check the mailbox password and SMTP access in Spacemail.") from exc
    return {"incoming": True, "outgoing": True, "folders": len(folders)}


def send_message(settings, outgoing, admin_id):
    if not settings.support_mail_password:
        raise mail_error(503, "mail_not_configured", "The support mailbox is not connected.")
    to = _recipients(outgoing.to)
    cc = _recipients(outgoing.cc)
    if not to or len(to) + len(cc) > 20:
        raise mail_error(422, "mail_invalid_recipient", "Use between one and twenty recipients.")
    message = EmailMessage()
    message["From"] = settings.support_mail_address
    message["To"] = outgoing.to
    if outgoing.cc:
        message["Cc"] = outgoing.cc
    message["Subject"] = outgoing.subject
    message["Date"] = formatdate(localtime=False, usegmt=True)
    message["Message-ID"] = make_msgid(domain=settings.support_mail_address.split("@")[-1])
    if outgoing.in_reply_to:
        message["In-Reply-To"] = outgoing.in_reply_to
    if outgoing.references:
        message["References"] = outgoing.references
    message.set_content(outgoing.body)
    total = 0
    for attachment in outgoing.attachments:
        try:
            data = base64.b64decode(attachment.content, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise mail_error(422, "mail_invalid_attachment", "An attachment could not be decoded.") from exc
        total += len(data)
        if total > MAX_ATTACHMENT_BYTES:
            raise mail_error(413, "mail_attachments_too_large", "Attachments must total 8 MB or less.")
        name = attachment.name.replace("\r", "").replace("\n", "").replace("\x00", "")
        message.add_attachment(data, maintype="application", subtype="octet-stream", filename=name)
    # Prevent double-click/retry duplicates during this process lifetime. A failed
    # SMTP transaction is never automatically retried because delivery is uncertain.
    key = f"{admin_id}:{outgoing.request_id}"
    with _send_lock:
        now = time.monotonic()
        for expired in [k for k, v in _sent.items() if now - v[0] > 3600]:
            del _sent[expired]
        if key in _sent:
            return _sent[key][1]
        try:
            with smtplib.SMTP_SSL("mail.spacemail.com", 465, timeout=25, context=ssl.create_default_context()) as smtp:
                smtp.login(settings.support_mail_address, settings.support_mail_password.get_secret_value())
                refused = smtp.send_message(message, from_addr=settings.support_mail_address, to_addrs=to + cc)
        except (smtplib.SMTPException, OSError) as exc:
            # Keep this request ID blocked to prevent uncertain deliveries being resent.
            result = {"sent": False, "uncertain": True, "warning": "Sending could not be confirmed. Check Sent in Spacemail before composing a new message."}
            _sent[key] = (now, result)
            return result
        result = {"sent": True, "message_id": str(message["Message-ID"]), "warning": None}
        if refused:
            result["warning"] = "Some recipients were rejected. Check the recipient addresses before following up."
        _sent[key] = (now, result)
        try:
            with mailbox(settings) as client:
                folders = _folders(client)
                sent = next((f for f in folders if "\\Sent" in f["flags"]), None)
                if sent is None:
                    sent = next((f for f in folders if f["name"].lower() in ("sent", "sent items", "sent messages")), None)
                if sent is None or client.append(_quote(sent["id"]), "(\\Seen)", None, message.as_bytes(policy=policy.SMTP))[0] != "OK":
                    result["warning"] = "Email sent, but its Sent-folder copy could not be saved. Do not resend."
        except HTTPException:
            result["warning"] = "Email sent, but its Sent-folder copy could not be saved. Do not resend."
        return result
