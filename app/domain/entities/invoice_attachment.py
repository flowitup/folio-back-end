"""Invoice attachment domain entity — metadata for a file uploaded to an invoice."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID


@dataclass
class InvoiceAttachment:
    """Domain entity representing a file attached to an invoice.

    The file content lives in object storage (S3/MinIO). This entity holds
    only the metadata + storage key needed to retrieve and manage it.
    """

    id: UUID
    invoice_id: UUID
    filename: str  # original filename as uploaded by user
    storage_key: str  # opaque key in the object store
    mime_type: str
    size_bytes: int
    uploaded_at: datetime
    uploaded_by: Optional[UUID] = None
