"""MediaCaptureDateReader: capture date from EXIF / video metadata (QA3 documents-photos-analyses-01)."""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timezone
from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image

from app.infrastructure.adapters.media_capture_date_reader import (
    MediaCaptureDateReader,
    parse_exif_datetime,
    parse_iso_creation_time,
)


def _jpeg(original: str | None = None, offset: str | None = None, plain: str | None = None) -> bytes:
    img = Image.new("RGB", (8, 8), (200, 10, 10))
    exif = Image.Exif()
    if plain:
        exif[0x0132] = plain
    if original:
        sub = exif.get_ifd(0x8769)
        sub[0x9003] = original
        if offset:
            sub[0x9011] = offset
    out = BytesIO()
    img.save(out, format="JPEG", exif=exif)
    return out.getvalue()


def test_exif_original_with_offset():
    found = MediaCaptureDateReader().read(_jpeg("2026:09:01 10:30:00", "+07:00"), "image/jpeg")
    assert found == datetime(2026, 9, 1, 3, 30, tzinfo=timezone.utc)


def test_exif_original_without_offset_is_paris_time():
    found = MediaCaptureDateReader().read(_jpeg("2026:09:01 10:30:00"), "image/jpeg")
    assert found == datetime(2026, 9, 1, 8, 30, tzinfo=timezone.utc)


def test_exif_plain_datetime_is_a_fallback():
    found = MediaCaptureDateReader().read(_jpeg(plain="2026:08:15 12:00:00"), "image/jpeg")
    assert found == datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc)


def test_no_exif_and_garbage_return_none():
    out = BytesIO()
    Image.new("RGB", (4, 4)).save(out, format="PNG")
    assert MediaCaptureDateReader().read(out.getvalue(), "image/png") is None
    assert MediaCaptureDateReader().read(b"not an image", "image/jpeg") is None


@pytest.mark.parametrize("raw", ["0000:00:00 00:00:00", "2999:01:01 00:00:00", "1850:01:01 00:00:00", None, 42])
def test_unusable_exif_values_are_ignored(raw):
    assert parse_exif_datetime(raw) is None


def test_iso_creation_time():
    assert parse_iso_creation_time("2026-09-01T08:30:00.000000Z") == datetime(2026, 9, 1, 8, 30, tzinfo=timezone.utc)
    assert parse_iso_creation_time("1904-01-01T00:00:00Z") is None
    assert parse_iso_creation_time("") is None


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_video_creation_time(tmp_path):
    path = tmp_path / f"{uuid4()}.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=32x32:d=0.2",
            "-metadata", "creation_time=2026-09-01T08:30:00Z", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
    )  # fmt: skip
    found = MediaCaptureDateReader().read(path.read_bytes(), "video/mp4")
    assert found == datetime(2026, 9, 1, 8, 30, tzinfo=timezone.utc)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_video_probe_runs_under_prlimit_not_a_preexec_fn(tmp_path, monkeypatch):
    """Regression review #5: no preexec_fn under gunicorn's gthread workers."""
    path = tmp_path / f"{uuid4()}.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=32x32:d=0.2", str(path)], check=True)
    calls = []
    real_run = subprocess.run

    def spy(args, **kwargs):
        calls.append((args, kwargs))
        return real_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    MediaCaptureDateReader().read(path.read_bytes(), "video/mp4")
    (args, kwargs), *_ = calls
    assert "preexec_fn" not in kwargs and kwargs["timeout"] > 0
    if shutil.which("prlimit"):
        assert args[0].endswith("prlimit") and args[args.index("--") + 1] == "ffprobe"
