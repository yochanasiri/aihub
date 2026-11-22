import base64
import json
from types import SimpleNamespace

from cryptography.fernet import Fernet
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.gmail import GmailService, _cipher, _email_record, _message_body, _search_query
from backend.app.models import Base, GmailImportedMessage, User


def _encoded(value):
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def test_gmail_search_query_uses_bounded_terms_and_omits_fillers():
    query = _search_query("What did Alex say about the invoice 4812 and the overdue payment?")

    assert query == "{alex say invoice 4812 overdue payment}"
    assert len(query[1:-1].split()) == 6


def test_plain_text_is_preferred_and_html_is_not_exposed():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": _encoded("Hello from plain text.")}},
            {"mimeType": "text/html", "body": {"data": _encoded("<p>Hello HTML</p><script>hidden</script>")}},
        ],
    }

    assert _message_body(payload) == "Hello from plain text."


def test_html_email_body_drops_script_and_style_content():
    payload = {
        "mimeType": "text/html",
        "body": {"data": _encoded("<p>Visible</p><style>hidden</style><script>secret()</script>")},
    }

    assert _message_body(payload) == "Visible"


def test_email_record_extracts_headers_and_decodes_body():
    message = {
        "id": "message-1",
        "threadId": "thread-1",
        "internalDate": "1750000000000",
        "snippet": "Preview",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "Sender <sender@example.com>"},
                {"name": "Subject", "value": "A message"},
            ],
            "body": {"data": _encoded("Full message body")},
        },
    }

    record = _email_record(message)
    assert record["id"] == "message-1"
    assert record["from"] == "Sender <sender@example.com>"
    assert record["subject"] == "A message"
    assert record["body"] == "Full message body"


def test_fernet_cipher_uses_configured_key():
    cipher = _cipher(SimpleNamespace(gmail_token_encryption_key=Fernet.generate_key().decode()))
    encrypted = cipher.encrypt(b"refresh-token")

    assert cipher.decrypt(encrypted) == b"refresh-token"


@pytest.mark.asyncio
async def test_imported_messages_are_scoped_to_the_requesting_user():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    cipher = Fernet(Fernet.generate_key())
    async with sessions() as session:
        session.add_all([
            User(id="user-one", email="one@iiti.ac.in", name="One"),
            User(id="user-two", email="two@iiti.ac.in", name="Two"),
        ])
        for user_id, message_id, subject in (
            ("user-one", "mail-one", "Invoice"),
            ("user-two", "mail-two", "Invoice"),
        ):
            content = {"id": message_id, "subject": subject, "body": "shared invoice details"}
            session.add(GmailImportedMessage(
                user_id=user_id,
                provider_message_id=message_id,
                encrypted_content=cipher.encrypt(json.dumps(content).encode()).decode(),
            ))
        await session.commit()

    service = object.__new__(GmailService)
    service.sessions = sessions
    service.cipher = cipher
    assert [item["id"] for item in await service._local_messages("user-one", "invoice", True)] == ["mail-one"]
    await engine.dispose()
