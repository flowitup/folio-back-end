"""Read when a photo or video was taken from the file's own metadata.

Images: EXIF DateTimeOriginal (0x9003) with OffsetTimeOriginal (0x9011), falling
back to DateTime (0x0132). A time without an offset is a phone's local wall clock;
it is read as Europe/Paris, the business time zone.
Videos: the container's ``creation_time`` tag (always UTC in MP4/MOV), via ffprobe
under the same limits as the poster-frame thumbnailer.

Every failure returns None: the capture date is a convenience, never a reason to
refuse an upload. Dates outside 1900-01-01 .. now+1 day are ignored too.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import warnings
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Optional

from PIL import Image

from app.domain.time import BUSINESS_TZ
from app.infrastructure.adapters.ffmpeg_video_thumbnailer import _limit_resources

_log = logging.getLogger(__name__)

_EXIF_IFD = 0x8769
_DATETIME_ORIGINAL = 0x9003
_OFFSET_TIME_ORIGINAL = 0x9011
_DATETIME = 0x0132
_ALLOWED_FORMATS = ("JPEG", "PNG", "WEBP")
_FFPROBE_TIMEOUT_SECONDS = 10
_OFFSET = re.compile(r"^([+-])(\d{2}):?(\d{2})$")
_MIN = datetime(1900, 1, 1, tzinfo=timezone.utc)
_AHEAD = timedelta(days=1)


def _bounded(value: datetime) -> Optional[datetime]:
    value = value.astimezone(timezone.utc)
    if value < _MIN or value > datetime.now(timezone.utc) + _AHEAD:
        return None
    return value


def parse_exif_datetime(raw: object, offset: object = None) -> Optional[datetime]:
    """'2026:09:01 10:30:00' (+ '+02:00') -> aware UTC datetime, or None."""
    if not isinstance(raw, str):
        return None
    try:
        naive = datetime.strptime(raw.strip().rstrip("\x00")[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    match = _OFFSET.match(offset.strip().rstrip("\x00")) if isinstance(offset, str) else None
    if match:
        sign = -1 if match.group(1) == "-" else 1
        delta = timedelta(hours=int(match.group(2)), minutes=int(match.group(3)))
        aware = naive.replace(tzinfo=timezone(sign * delta))
    else:
        aware = naive.replace(tzinfo=BUSINESS_TZ)
    return _bounded(aware)


def parse_iso_creation_time(raw: object) -> Optional[datetime]:
    """'2026-09-01T08:30:00.000000Z' -> aware UTC datetime, or None."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        value = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    # Cameras without a clock write the MP4 epoch (1904) or the Unix epoch.
    if value.year <= 1970:
        return None
    return _bounded(value)


class MediaCaptureDateReader:
    """Implements ICaptureDateReader for the accepted photo and video formats."""

    def read(self, data: bytes, content_type: str) -> Optional[datetime]:
        try:
            if content_type.startswith("video/"):
                return self._read_video(data)
            return self._read_image(data)
        except Exception:  # never fail an upload over metadata
            _log.info("Could not read the capture date of an uploaded %s", content_type, exc_info=True)
            return None

    @staticmethod
    def _read_image(data: bytes) -> Optional[datetime]:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(BytesIO(data), formats=_ALLOWED_FORMATS)
        exif = img.getexif()
        if not exif:
            return None
        sub = exif.get_ifd(_EXIF_IFD)
        found = parse_exif_datetime(sub.get(_DATETIME_ORIGINAL), sub.get(_OFFSET_TIME_ORIGINAL))
        return found or parse_exif_datetime(exif.get(_DATETIME))

    @staticmethod
    def _read_video(data: bytes) -> Optional[datetime]:
        if shutil.which("ffprobe") is None:
            return None
        with tempfile.TemporaryDirectory() as tmp:
            in_path = os.path.join(tmp, "input")
            with open(in_path, "wb") as fh:
                fh.write(data)
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file",
                    "-show_entries",
                    "format_tags=creation_time:stream_tags=creation_time",
                    "-of",
                    "json",
                    in_path,
                ],
                capture_output=True,
                timeout=_FFPROBE_TIMEOUT_SECONDS,
                check=False,
                preexec_fn=_limit_resources,
            )
        if result.returncode != 0:
            return None
        info = json.loads(result.stdout)
        candidates = [info.get("format", {}).get("tags", {}).get("creation_time")]
        candidates += [s.get("tags", {}).get("creation_time") for s in info.get("streams", [])]
        for raw in candidates:
            found = parse_iso_creation_time(raw)
            if found is not None:
                return found
        return None
