"""S3-compatible attachment storage adapter (works with MinIO and AWS S3)."""

from __future__ import annotations

from typing import BinaryIO

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.application.invoice.ports import IAttachmentStorage


class S3AttachmentStorage(IAttachmentStorage):
    """Stores attachments in any S3-compatible object store (MinIO, AWS S3)."""

    def __init__(
        self,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str = "us-east-1",
    ) -> None:
        self._bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            # Path-style addressing — required for MinIO and works with AWS S3
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
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
