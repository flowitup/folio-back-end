"""Image type detection from a file's first bytes.

Pure stdlib — zero Flask/SQLAlchemy/infra dependencies.

An upload's Content-Type is whatever the client says. Checking only that let
any bytes labelled image/png (an HTML page, say) be stored and served back as
a photo; the file's own signature says what it really is.
"""

from __future__ import annotations

from typing import BinaryIO, Optional

# Enough bytes for every signature below (WebP needs 12).
IMAGE_SIGNATURE_PEEK_BYTES = 12


def sniff_image_type(head: bytes) -> Optional[str]:
    """Return the MIME type of a PNG, JPEG or WebP starting with ``head``, else None."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(head) >= 12 and head[0:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def sniff_image_stream(fileobj: BinaryIO) -> Optional[str]:
    """Sniff ``fileobj``'s image type from its first bytes, then rewind it for the upload."""
    head = fileobj.read(IMAGE_SIGNATURE_PEEK_BYTES)
    fileobj.seek(0)
    return sniff_image_type(head)
