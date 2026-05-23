"""S3-compatible attachment storage adapter (works with MinIO and AWS S3)."""

from __future__ import annotations

import logging
from typing import BinaryIO, Optional

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

from app.application.invoice.ports import IAttachmentStorage

_log = logging.getLogger(__name__)


class S3AttachmentStorage(IAttachmentStorage):
    """Stores attachments in any S3-compatible object store (MinIO, AWS S3)."""

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
        self._boto_cfg = BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"})
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            # Path-style addressing — required for MinIO and works with AWS S3
            config=self._boto_cfg,
        )

        # Second client for presigned URLs reachable by the browser.
        # Uses the public endpoint so the embedded host in the URL is correct
        # (presigned URL signatures include the host).
        self._public_client: Optional[object] = None
        if public_endpoint_url:
            self._public_client = boto3.client(
                "s3",
                endpoint_url=public_endpoint_url,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region,
                config=self._boto_cfg,
            )

    def ensure_bucket(self) -> None:
        """Create the bucket if it does not already exist. Safe to call repeatedly."""
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchBucket", "NotFound"):
                self._client.create_bucket(Bucket=self._bucket)
            else:
                raise

    def put(self, key: str, fileobj: BinaryIO, content_type: str) -> None:
        self._client.upload_fileobj(
            fileobj,
            self._bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )

    def get_stream(self, key: str) -> tuple[BinaryIO, int]:
        obj = self._client.get_object(Bucket=self._bucket, Key=key)
        return obj["Body"], int(obj.get("ContentLength", 0))

    def delete(self, key: str) -> None:
        # S3 delete_object is idempotent — does not raise on missing keys.
        self._client.delete_object(Bucket=self._bucket, Key=key)

    # ------------------------------------------------------------------
    # Presigned upload support
    # ------------------------------------------------------------------

    @property
    def presigned_uploads_enabled(self) -> bool:
        """True when a public endpoint is configured for presigned URLs."""
        return self._public_client is not None

    def generate_presigned_put_url(self, key: str, content_type: str, expires_in: int = 600) -> str:
        """Generate a presigned PUT URL for direct browser upload.

        Uses the public-facing client so the URL hostname is reachable from
        the browser. Raises RuntimeError if no public endpoint is configured.
        """
        if self._public_client is None:
            raise RuntimeError("S3_PUBLIC_ENDPOINT_URL not configured — presigned uploads disabled")
        return self._public_client.generate_presigned_url(  # type: ignore[union-attr]
            "put_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ContentType": content_type,
            },
            ExpiresIn=expires_in,
        )

    def generate_presigned_get_url(self, key: str, expires_in: int = 3600) -> str:
        """Generate a presigned GET URL for direct browser download.

        Uses the public-facing client so the URL hostname is reachable from
        the browser. Raises RuntimeError if no public endpoint is configured.
        """
        if self._public_client is None:
            raise RuntimeError("S3_PUBLIC_ENDPOINT_URL not configured — presigned downloads disabled")
        return self._public_client.generate_presigned_url(  # type: ignore[union-attr]
            "get_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
            },
            ExpiresIn=expires_in,
        )

    def head_object(self, key: str) -> Optional[dict]:
        """Return object metadata dict or None if the key does not exist.

        The returned dict contains at least ``ContentLength`` and
        ``ContentType`` on success.
        """
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

    def ensure_cors(self, allowed_origins: list[str]) -> None:
        """Set CORS rules on the bucket to allow presigned PUT uploads.

        Safe to call repeatedly — overwrites the existing CORS configuration.
        """
        cors_config = {
            "CORSRules": [
                {
                    "AllowedOrigins": allowed_origins,
                    "AllowedMethods": ["GET", "HEAD", "PUT"],
                    "AllowedHeaders": ["Content-Type", "Content-Length"],
                    "ExposeHeaders": ["ETag"],
                    "MaxAgeSeconds": 3600,
                }
            ]
        }
        self._client.put_bucket_cors(Bucket=self._bucket, CORSConfiguration=cors_config)
        _log.info("S3 CORS configured for origins: %s", allowed_origins)
