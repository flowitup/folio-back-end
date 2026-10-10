"""UploadAttachmentUseCase keeps a stored filename within its varchar(255) column."""

from __future__ import annotations

import io
from types import SimpleNamespace
from uuid import uuid4

from app.application.invoice.upload_attachment import UploadAttachmentUseCase

PDF = b"%PDF-1.4\n%fake\n"


class _Invoices:
    def find_by_id(self, invoice_id):
        return SimpleNamespace(id=invoice_id)


class _Attachments:
    def __init__(self):
        self.saved = []

    def save(self, attachment):
        self.saved.append(attachment)
        return attachment


class _Storage:
    def __init__(self):
        self.keys = []

    def put(self, key, fileobj, content_type):
        self.keys.append(key)


def _upload(filename: str):
    attachments, storage = _Attachments(), _Storage()
    uc = UploadAttachmentUseCase(_Invoices(), attachments, storage)
    att = uc.execute(
        invoice_id=uuid4(),
        filename=filename,
        mime_type="application/pdf",
        size_bytes=len(PDF),
        fileobj=io.BytesIO(PDF),
    )
    return att, storage.keys[0]


def test_long_name_is_shortened_keeping_its_extension():
    # A 300-character name sent straight to the API failed the insert with a 500
    att, key = _upload("x" * 296 + ".pdf")
    assert len(att.filename) == 255
    assert att.filename == "x" * 251 + ".pdf"
    assert key.endswith("/" + att.filename)


def test_name_within_the_column_is_kept_as_is():
    name = "y" * 251 + ".pdf"
    att, _ = _upload(name)
    assert att.filename == name


def test_overlong_extension_is_cut_to_the_column():
    att, _ = _upload("a." + "b" * 300)
    assert len(att.filename) == 255
