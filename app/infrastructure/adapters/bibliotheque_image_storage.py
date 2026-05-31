"""BibliothequeImageStorage — S3/MinIO image storage for library products.

Thin wrapper over the same boto3 client as S3AttachmentStorage; key prefix is
library-products/{product_id}/ so product images are namespaced separately
from invoice attachments in the same bucket.

Image bytes are always uploaded by the ingestion client (POST /products/<id>/image);
this adapter does NOT scrape URLs.
"""

from __future__ import annotations

import logging
from typing import BinaryIO, Optional
from uuid import UUID

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

_log = logging.getLogger(__name__)

_KEY_PREFIX = "library-products"


def _product_key(product_id: UUID, filename: str) -> str:
    """Build the S3 key for a product image."""
    return f"{_KEY_PREFIX}/{product_id}/{filename}"


class BibliothequeImageStorage:
    """Implements IProductImageStorage over a S3-compatible object store."""

    def __init__(
        self,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str = "us-east-1",
        public_endpoint_url: str = "",
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
        self._public_client: Optional[object] = None
        if public_endpoint_url:
            self._public_client = boto3.client(
                "s3",
                endpoint_url=public_endpoint_url,
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

    def get_stream(self, key: str) -> tuple[BinaryIO, int]:
        """Return (body_stream, content_length) for the given key."""
        obj = self._client.get_object(Bucket=self._bucket, Key=key)
        return obj["Body"], int(obj.get("ContentLength", 0))

    def presigned_get_url(self, key: str, expires_in: int = 3600) -> str:
        """Return a presigned GET URL for browser download.

        Uses the public-facing client so the hostname in the URL is browser-reachable.
        Falls back to the internal client when no public endpoint is configured
        (e.g. local dev without a reverse proxy).
        """
        client = self._public_client if self._public_client is not None else self._client
        return client.generate_presigned_url(  # type: ignore[union-attr]
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    def delete(self, key: str) -> None:
        """Delete the object at key. S3 delete_object is idempotent."""
        self._client.delete_object(Bucket=self._bucket, Key=key)

    # ------------------------------------------------------------------
    # Helpers called by routes / use-cases
    # ------------------------------------------------------------------

    @staticmethod
    def build_key(product_id: UUID, filename: str = "image") -> str:
        """Build the canonical S3 key for a product's image."""
        return _product_key(product_id, filename)

    def head(self, key: str) -> Optional[dict]:
        """Return object metadata or None if the key does not exist."""
        try:
            resp = self._client.head_object(Bucket=self._bucket, Key=key)
            return {
                "ContentLength": resp["ContentLength"],
                "ContentType": resp.get("ContentType", "application/octet-stream"),
            }
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
