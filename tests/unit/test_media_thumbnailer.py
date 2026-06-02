"""Unit tests for MediaThumbnailer dispatch + FfmpegVideoThumbnailer.

The dispatcher tests use fakes (no ffmpeg). The ffmpeg adapter test is skipped
when the binary is unavailable so CI without ffmpeg stays green.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from unittest.mock import MagicMock

import pytest

from app.application.project_photos.exceptions import ThumbnailGenerationError
from app.infrastructure.adapters.ffmpeg_video_thumbnailer import FfmpegVideoThumbnailer
from app.infrastructure.adapters.media_thumbnailer import MediaThumbnailer


class TestMediaThumbnailerDispatch:
    def test_image_routes_to_image_backend(self):
        image_tn, video_tn = MagicMock(), MagicMock()
        image_tn.generate.return_value = b"img-thumb"
        disp = MediaThumbnailer(image_thumbnailer=image_tn, video_thumbnailer=video_tn)

        out = disp.generate(b"data", "image/jpeg")

        assert out == b"img-thumb"
        image_tn.generate.assert_called_once()
        video_tn.generate.assert_not_called()

    def test_video_routes_to_video_backend(self):
        image_tn, video_tn = MagicMock(), MagicMock()
        video_tn.generate.return_value = b"poster"
        disp = MediaThumbnailer(image_thumbnailer=image_tn, video_thumbnailer=video_tn)

        out = disp.generate(b"data", "video/mp4")

        assert out == b"poster"
        video_tn.generate.assert_called_once()
        image_tn.generate.assert_not_called()


class TestFfmpegVideoThumbnailer:
    def test_missing_binary_raises(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda _: None)
        with pytest.raises(ThumbnailGenerationError):
            FfmpegVideoThumbnailer().generate(b"x", "video/mp4")

    @pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
    def test_generates_jpeg_poster_from_real_clip(self):
        # Synthesize a 1s test clip with ffmpeg's testsrc generator.
        with tempfile.NamedTemporaryFile(suffix=".mp4") as clip:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc=duration=1:size=320x240:rate=10",
                    "-pix_fmt",
                    "yuv420p",
                    clip.name,
                ],
                capture_output=True,
                check=True,
            )
            with open(clip.name, "rb") as fh:
                data = fh.read()

        poster = FfmpegVideoThumbnailer().generate(data, "video/mp4")
        # JPEG magic bytes.
        assert poster[:2] == b"\xff\xd8"
