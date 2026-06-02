"""ffmpeg-backed video poster-frame thumbnailer.

Extracts a single still frame from an uploaded video and returns it as JPEG
bytes, matching the contract of the image thumbnailer so videos slot into the
same gallery thumbnail pipeline as photos. Shells out to the ffmpeg binary
(installed in the runtime image) via an explicit argument list — never a shell
string — so attacker-controlled bytes cannot influence the command.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile

from app.application.project_photos.exceptions import ThumbnailGenerationError

_log = logging.getLogger(__name__)

# Max edge of the poster (matches the image thumbnailer's neighbourhood).
_MAX_EDGE = 480
# Hard wall-clock cap so a pathological clip can't hang the request worker.
_FFMPEG_TIMEOUT_SECONDS = 20


class FfmpegVideoThumbnailer:
    """Generate a JPEG poster frame from a video via the ffmpeg binary."""

    def generate(self, data: bytes, content_type: str) -> bytes:  # noqa: ARG002
        if shutil.which("ffmpeg") is None:
            raise ThumbnailGenerationError("ffmpeg binary not available for video thumbnailing")

        with tempfile.TemporaryDirectory() as tmp:
            in_path = os.path.join(tmp, "input")
            out_path = os.path.join(tmp, "poster.jpg")
            with open(in_path, "wb") as fh:
                fh.write(data)

            # Try a frame ~1s in (skips black intro frames); fall back to the
            # very first frame for clips shorter than 1 second.
            for seek in ("1", "0"):
                if self._run_ffmpeg(in_path, out_path, seek) and os.path.getsize(out_path) > 0:
                    with open(out_path, "rb") as fh:
                        return fh.read()

        raise ThumbnailGenerationError("ffmpeg produced no poster frame for the uploaded video")

    @staticmethod
    def _run_ffmpeg(in_path: str, out_path: str, seek: str) -> bool:
        # -ss before -i = fast input seek; -frames:v 1 grabs one frame;
        # scale keeps aspect, caps the long edge, ensures even dims (-2).
        args = [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-ss",
            seek,
            "-i",
            in_path,
            "-frames:v",
            "1",
            "-vf",
            f"scale='min({_MAX_EDGE},iw)':-2",
            "-f",
            "image2",
            out_path,
        ]
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                timeout=_FFMPEG_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            _log.warning("ffmpeg poster extraction timed out (seek=%s)", seek)
            return False
        if result.returncode != 0:
            _log.info("ffmpeg poster attempt failed (seek=%s, rc=%s)", seek, result.returncode)
            return False
        return os.path.exists(out_path)
