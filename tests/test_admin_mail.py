import base64
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import mail_service as mail
from app.config import Settings
from app.main import app
from app.admin_auth import require_admin
from app.config import get_settings


def configured():
    return Settings(_env_file=None, support_mail_password="mail-secret")


def fake_imap(monkeypatch):
    client = MagicMock()
    client.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\Sent) "/" "Sent"', b'(\\Trash) "/" "Trash"'])
    client.select.return_value = ("OK", [b"1"])
    client.response.return_value = ("UIDVALIDITY", [b"123"])
    client.capabilities = (b"IMAP4REV1", b"MOVE")
    monkeypatch.setattr(mail.imaplib, "IMAP4_SSL", lambda *a, **kw: client)
    return client


def test_mailbox_requires_password_and_redacts_secrets():
    settings = configured()
    assert "mail-secret" not in repr(settings)
    with pytest.raises(HTTPException) as exc:
        with mail.mailbox(Settings(_env_file=None)):
            pass
    assert exc.value.status_code == 503


@pytest.mark.parametrize("path", ["/status", "/folders", "/messages", "/messages/1?uid_validity=1", "/messages/1/attachments/0?uid_validity=1"])
def test_all_mail_reads_require_admin(unauthenticated_client, path):
    assert unauthenticated_client.get("/v1/admin/mail" + path).status_code == 401


def test_mail_status_only_exposes_configuration(unauthenticated_client):
    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(user=SimpleNamespace(id="admin"))
    app.dependency_overrides[get_settings] = configured
    response = unauthenticated_client.get("/v1/admin/mail/status")
    assert response.status_code == 200
    assert response.json() == {"configured": True, "address": "support@barcodenest.com"}
    assert response.headers["cache-control"] == "no-store"
    assert "mail-secret" not in response.text


def test_mail_sending_rejects_untrusted_origin(unauthenticated_client, monkeypatch):
    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(user=SimpleNamespace(id="admin"))
    app.dependency_overrides[get_settings] = configured
    sender = MagicMock()
    monkeypatch.setattr(mail, "send_message", sender)
    response = unauthenticated_client.post("/v1/admin/mail/send", headers={"Origin": "https://attacker.example"}, json={
        "request_id": "test-request-123456", "to": "a@example.com", "subject": "Hello", "body": "Hello",
    })
    assert response.status_code == 403
    sender.assert_not_called()


def test_non_admin_cannot_read_mail(unauthenticated_client):
    from app.user_auth import get_current_user
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(user=SimpleNamespace(is_admin=False))
    assert unauthenticated_client.get("/v1/admin/mail/status").status_code == 403


def test_list_uses_uid_and_does_not_mark_read(monkeypatch):
    client = fake_imap(monkeypatch)
    def uid(command, *args):
        if command == "SEARCH":
            return "OK", [b"1 2"]
        return "OK", [(b'1 (UID 2 FLAGS () BODY[HEADER] {60}', b'From: Customer <a@example.com>\r\nSubject: =?utf-8?q?Hello_=E2=9C=93?=\r\n\r\n'), b')']
    client.uid.side_effect = uid
    result = mail.list_messages(configured(), "INBOX", "", 1)
    assert result["uid_validity"] == "123"
    assert result["items"][0]["uid"] == "2"
    assert result["items"][0]["subject"] == "Hello ✓"
    assert "BODY.PEEK" in client.uid.call_args_list[1].args[-1]
    assert client.select.call_args.kwargs["readonly"] is True
    client.logout.assert_called_once()


def test_stale_uid_validity_blocks_mutation(monkeypatch):
    client = fake_imap(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        mail.mutate_message(configured(), 1, mail.MailMutation(uid_validity="999", seen=True))
    assert exc.value.status_code == 409
    client.uid.assert_not_called()


def test_move_uses_atomic_uid_move_not_global_expunge(monkeypatch):
    client = fake_imap(monkeypatch)
    client.uid.side_effect = [("OK", [(b"1", b"Message-ID: <one@example.com>\r\n\r\n")]), ("OK", [])]
    assert mail.mutate_message(configured(), 1, mail.MailMutation(uid_validity="123", destination="Trash"))["ok"]
    assert client.uid.call_args.args == ("MOVE", "1", '"Trash"')
    client.expunge.assert_not_called()


def test_message_too_large_is_rejected_before_body_fetch(monkeypatch):
    client = fake_imap(monkeypatch)
    client.uid.return_value = ("OK", [b"1 (RFC822.SIZE 99999999)"])
    with pytest.raises(HTTPException) as exc:
        mail.read_message(configured(), "INBOX", 1, "123")
    assert exc.value.status_code == 413
    assert client.uid.call_count == 1


def test_html_messages_are_text_without_remote_images_or_scripts():
    message = EmailMessage()
    message.set_content('<p>Hello customer</p><script>steal()</script><img src="https://tracker.example/pixel"><p>Thank you</p>', subtype="html")
    result = mail._body(message)
    assert "Hello customer" in result and "Thank you" in result
    assert "steal" not in result and "tracker" not in result and "<" not in result


def test_attachment_is_downloaded_as_attachment(monkeypatch, unauthenticated_client):
    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(user=SimpleNamespace(id="admin"))
    monkeypatch.setattr(mail, "download_attachment", lambda *a: (b"<script>evil</script>", "attachment; filename*=UTF-8''test.html"))
    response = unauthenticated_client.get("/v1/admin/mail/messages/1/attachments/0?uid_validity=123")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment")
    assert "sandbox" in response.headers["content-security-policy"]


def test_header_injection_is_rejected():
    with pytest.raises(ValidationError):
        mail.OutgoingMail(request_id="test-message-123456", to="a@example.com\r\nBcc: x@example.com", subject="Hello", body="Hi")


def test_send_is_deduplicated_and_saved_to_sent(monkeypatch):
    client = fake_imap(monkeypatch)
    client.append.return_value = ("OK", [])
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    smtp.send_message.return_value = {}
    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", lambda *a, **kw: smtp)
    mail._sent.clear()
    outgoing = mail.OutgoingMail(request_id="test-message-123456", to="Customer <a@example.com>", subject="Re: Help", body="Hello", in_reply_to="<customer@example.com>", attachments=[mail.OutgoingAttachment(name="notes.txt", content=base64.b64encode(b"notes").decode())])
    first = mail.send_message(configured(), outgoing, "admin")
    second = mail.send_message(configured(), outgoing, "admin")
    assert first == second and first["sent"]
    smtp.send_message.assert_called_once()
    sent = smtp.send_message.call_args.args[0]
    assert sent["From"] == "support@barcodenest.com"
    assert sent["In-Reply-To"] == "<customer@example.com>"
    assert len(list(sent.iter_attachments())) == 1
    client.append.assert_called_once()


def test_sent_copy_failure_does_not_resend(monkeypatch):
    client = fake_imap(monkeypatch)
    client.append.return_value = ("NO", [])
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    smtp.send_message.return_value = {}
    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", lambda *a, **kw: smtp)
    mail._sent.clear()
    outgoing = mail.OutgoingMail(request_id="test-copy-error-123456", to="a@example.com", subject="Hello", body="Hello")
    result = mail.send_message(configured(), outgoing, "admin")
    assert result["sent"] and "Do not resend" in result["warning"]
    mail.send_message(configured(), outgoing, "admin")
    smtp.send_message.assert_called_once()


def test_uncertain_delivery_is_never_automatically_retried(monkeypatch):
    fake_imap(monkeypatch)
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    smtp.send_message.side_effect = OSError("connection lost")
    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", lambda *a, **kw: smtp)
    mail._sent.clear()
    outgoing = mail.OutgoingMail(request_id="test-uncertain-123456", to="a@example.com", subject="Hello", body="Hello")
    assert mail.send_message(configured(), outgoing, "admin")["uncertain"]
    assert mail.send_message(configured(), outgoing, "admin")["uncertain"]
    smtp.send_message.assert_called_once()


def test_invalid_attachment_never_connects_to_smtp(monkeypatch):
    smtp = MagicMock()
    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", smtp)
    outgoing = mail.OutgoingMail(request_id="test-bad-file-123456", to="a@example.com", subject="Hello", body="Hello",
        attachments=[mail.OutgoingAttachment(name="file.txt", content="not-base64!")])
    with pytest.raises(HTTPException) as exc:
        mail.send_message(configured(), outgoing, "admin")
    assert exc.value.status_code == 422
    smtp.assert_not_called()


def test_folder_input_cannot_inject_imap_commands(monkeypatch):
    client = fake_imap(monkeypatch)
    with pytest.raises(HTTPException):
        mail.read_message(configured(), 'INBOX\r\nDELETE INBOX', 1, "123")
    client.select.assert_not_called()


def test_read_message_preserves_attachment_and_text(monkeypatch):
    client = fake_imap(monkeypatch)
    message = EmailMessage()
    message["From"] = "Customer <a@example.com>"
    message["Subject"] = "Question"
    message.set_content("How can I get started?")
    message.add_attachment(b"attachment data", maintype="application", subtype="octet-stream", filename="sample.txt")
    raw = message.as_bytes()
    def fetch(command, uid, fields):
        if fields == "(RFC822.SIZE)":
            return "OK", [f"1 (RFC822.SIZE {len(raw)})".encode()]
        return "OK", [(b"1 (UID 1 FLAGS () BODY[]", raw)]
    client.uid.side_effect = fetch
    result = mail.read_message(configured(), "INBOX", 1, "123")
    assert "How can I get started?" in result["body"]
    assert result["attachments"][0]["name"] == "sample.txt"
    data, disposition = mail.download_attachment(configured(), "INBOX", 1, "123", 0)
    assert data == b"attachment data" and "sample.txt" in disposition


def test_connection_check_authenticates_without_sending(monkeypatch):
    fake_imap(monkeypatch)
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", lambda *a, **kw: smtp)
    assert mail.check_connection(configured()) == {"incoming": True, "outgoing": True, "folders": 3}
    smtp.login.assert_called_once()
    smtp.send_message.assert_not_called()
