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


needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg / ffprobe not installed"
)


def _clip(
    size: str, suffix: str = ".mp4", codec: tuple[str, ...] = ("-c:v", "libx264", "-preset", "ultrafast")
) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=suffix) as clip:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=duration=1:size={size}:rate=2"]
            + ["-pix_fmt", "yuv420p", *codec, clip.name],
            capture_output=True,
            check=True,
        )
        with open(clip.name, "rb") as fh:
            return fh.read()


@needs_ffmpeg
class TestFfmpegHardening:
    @pytest.mark.parametrize("size", ["1920x1080", "3840x2160", "7680x4320"])
    def test_phone_resolutions_up_to_8k_still_get_a_poster(self, size):
        poster = FfmpegVideoThumbnailer().generate(_clip(size), "video/mp4")
        assert poster[:2] == b"\xff\xd8"

    def test_mov_and_webm_containers_work(self):
        mov = FfmpegVideoThumbnailer().generate(_clip("640x360", ".mov"), "video/quicktime")
        webm = FfmpegVideoThumbnailer().generate(_clip("640x360", ".webm", ("-c:v", "libvpx")), "video/webm")
        assert mov[:2] == webm[:2] == b"\xff\xd8"

    def test_a_frame_wider_than_8192_px_is_refused_before_decoding(self, monkeypatch):
        ran = []
        monkeypatch.setattr(FfmpegVideoThumbnailer, "_run_ffmpeg", staticmethod(lambda *a, **k: ran.append(a) or True))
        with pytest.raises(ThumbnailGenerationError, match="exceeds"):
            FfmpegVideoThumbnailer().generate(_clip("8200x16"), "video/mp4")
        assert ran == []  # ffmpeg never decoded it

    def test_a_frame_over_40_megapixels_is_refused(self, monkeypatch):
        probe = {
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
            "streams": [{"codec_type": "video", "width": 8000, "height": 5200}],
        }
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=__import__("json").dumps(probe).encode()),
        )
        with pytest.raises(ThumbnailGenerationError, match="8000x5200"):
            FfmpegVideoThumbnailer._probe("/nonexistent")

    @pytest.mark.parametrize("big", ["7000x7000", "8200x16"])
    def test_a_huge_second_video_stream_behind_a_tiny_first_one_is_refused(self, big):
        with tempfile.NamedTemporaryFile(suffix=".mkv") as clip:
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y"]
                + ["-f", "lavfi", "-i", "color=c=black:size=64x64:duration=1:rate=1"]
                + ["-f", "lavfi", "-i", f"color=c=black:size={big}:duration=1:rate=1"]
                + ["-map", "0:v", "-map", "1:v", "-c:v", "libx264", "-preset", "ultrafast", clip.name],
                capture_output=True,
                check=True,
            )
            with open(clip.name, "rb") as fh:
                data = fh.read()
        with pytest.raises(ThumbnailGenerationError, match="exceeds"):
            FfmpegVideoThumbnailer().generate(data, "video/webm")

    def test_ffmpeg_decodes_the_first_video_stream_only(self, monkeypatch):
        clip = _clip("320x240")
        calls = []
        real_run = subprocess.run
        monkeypatch.setattr(subprocess, "run", lambda args, **kw: calls.append(args) or real_run(args, **kw))
        FfmpegVideoThumbnailer().generate(clip, "video/mp4")
        ffmpeg_args = next(a for a in calls if a[0] == "ffmpeg")
        assert ffmpeg_args[ffmpeg_args.index("-map") + 1] == "0:v:0"

    @pytest.mark.parametrize(
        "exc", [OSError("no exec"), subprocess.SubprocessError("preexec failed"), ValueError("rlimit")]
    )
    def test_a_child_that_cannot_start_is_a_thumbnail_error(self, monkeypatch, exc):
        clip = _clip("64x64")

        def boom(*a, **k):
            raise exc

        monkeypatch.setattr(subprocess, "run", boom)
        with pytest.raises(ThumbnailGenerationError):
            FfmpegVideoThumbnailer().generate(clip, "video/mp4")

    @pytest.mark.parametrize(
        "payload",
        [b"not a video at all", b"", b"%PDF-1.4\n" + b"0" * 200, b"GIF89a" + b"\x00" * 64],
    )
    def test_unreadable_or_foreign_content_is_a_thumbnail_error(self, payload):
        with pytest.raises(ThumbnailGenerationError):
            FfmpegVideoThumbnailer().generate(payload, "video/mp4")

    def test_a_container_outside_the_allow_list_is_refused(self):
        with tempfile.NamedTemporaryFile(suffix=".avi") as clip:
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=64x64:rate=2", clip.name],
                capture_output=True,
                check=True,
            )
            with open(clip.name, "rb") as fh:
                data = fh.read()
        with pytest.raises(ThumbnailGenerationError):
            FfmpegVideoThumbnailer().generate(data, "video/mp4")

    def test_ffprobe_and_ffmpeg_run_single_threaded_without_network_under_rlimits(self, monkeypatch):
        clip = _clip("320x240")
        calls = []
        real_run = subprocess.run

        def spy(args, **kwargs):
            calls.append((args, kwargs))
            return real_run(args, **kwargs)

        monkeypatch.setattr(subprocess, "run", spy)
        FfmpegVideoThumbnailer().generate(clip, "video/mp4")

        names = [c[0][0] for c in calls]
        assert names[0] == "ffprobe" and set(names[1:]) == {"ffmpeg"}  # the header check comes before any decode
        for args, kwargs in calls:
            assert args[args.index("-protocol_whitelist") + 1] == "file"
            assert kwargs["preexec_fn"] is not None and kwargs["timeout"] > 0
        ffmpeg_args = next(a for a, _ in calls if a[0] == "ffmpeg")
        assert ffmpeg_args.count("-threads") == 2 and ffmpeg_args[ffmpeg_args.index("-threads") + 1] == "1"
        assert ffmpeg_args[ffmpeg_args.index("-f") + 1] == "mov"

    def test_the_child_limits_are_applied(self):
        import resource

        from app.infrastructure.adapters import ffmpeg_video_thumbnailer as mod

        script = (
            "import resource, sys\n"
            "from app.infrastructure.adapters.ffmpeg_video_thumbnailer import _limit_resources\n"
            "_limit_resources()\n"
            "print(resource.getrlimit(resource.RLIMIT_AS)[0], resource.getrlimit(resource.RLIMIT_CPU)[0],"
            " resource.getrlimit(resource.RLIMIT_FSIZE)[0])\n"
        )
        import sys

        out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True).stdout.split()
        assert [int(v) for v in out] == [mod._RLIMIT_AS_BYTES, mod._RLIMIT_CPU_SECONDS, mod._RLIMIT_FSIZE_BYTES]
        assert resource.getrlimit(resource.RLIMIT_AS)[0] != mod._RLIMIT_AS_BYTES  # the parent keeps its own limits
