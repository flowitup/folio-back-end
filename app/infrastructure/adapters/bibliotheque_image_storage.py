"""BibliothequeImageStorage — S3/MinIO image storage for library products.

Thin wrapper over the same boto3 client as S3AttachmentStorage; key prefix is
library-products/{product_id}/ so product images are namespaced separately
from invoice attachments in the same bucket.

Image bytes are always uploaded by the ingestion client (POST /products/<id>/image);
this adapter does NOT scrape URLs. Images are always served by streaming through
the API (get_stream), never via presigned URLs.
"""

from __future__ import annotations

import logging
from typing import BinaryIO
from uuid import UUID

import boto3
from botocore.client import Config as BotoConfig

_log = logging.getLogger(__name__)

_KEY_PREFIX = "library-products"
# Fixed object name used for every product image — the client-supplied filename
# is never interpolated into the key to prevent path-traversal via multipart upload.
_IMAGE_OBJECT_NAME = "image"


def _product_key(product_id: UUID) -> str:
    """Build the canonical S3 key for a product image."""
    return f"{_KEY_PREFIX}/{product_id}/{_IMAGE_OBJECT_NAME}"


class BibliothequeImageStorage:
    """Implements IProductImageStorage over a S3-compatible object store."""

    def __init__(
        self,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str = "us-east-1",
    ) -> None:
        self._bucket = bucket
        _cfg = BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"})
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=_cfg,
        )

    # ------------------------------------------------------------------
    # IProductImageStorage
    # ------------------------------------------------------------------

    def put(self, key: str, fileobj: BinaryIO, content_type: str) -> None:
        """Upload image bytes under the given S3 key."""
        self._client.upload_fileobj(
            fileobj,
            self._bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )

    def get_stream(self, key: str) -> tuple[BinaryIO, int, str]:
        """Return (body_stream, content_length, content_type) for the given key.

        Images are streamed back THROUGH the API rather than served via a
        presigned object-store URL. The store endpoint (minio:9000 in dev, the
        internal bucket host in prod) is not browser-reachable, so a presigned
        URL would 404 in the browser. Streaming keeps the byte path entirely
        server-side, mirroring how invoice attachments are served.
        """
        obj = self._client.get_object(Bucket=self._bucket, Key=key)
        content_type = obj.get("ContentType") or "application/octet-stream"
        return obj["Body"], int(obj.get("ContentLength", 0)), content_type

    # ------------------------------------------------------------------
    # Helpers called by routes / use-cases
    # ------------------------------------------------------------------

    @staticmethod
    def build_key(product_id: UUID) -> str:
        """Build the canonical S3 key for a product's image.

        Always uses the fixed object name — never the client-supplied filename.
        """
        return _product_key(product_id)
