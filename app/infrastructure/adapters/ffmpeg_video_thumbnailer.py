"""ffmpeg-backed video poster-frame thumbnailer.

Extracts a single still frame from an uploaded video and returns it as JPEG
bytes, matching the contract of the image thumbnailer so videos slot into the
same gallery thumbnail pipeline as photos. Shells out to the ffmpeg binary
(installed in the runtime image) via an explicit argument list — never a shell
string — so attacker-controlled bytes cannot influence the command.

A video is untrusted input that ffmpeg decodes inside the request worker, and a
file of a few hundred KB can declare a 16000x16000 frame. So ffprobe reads the
header first (size, container, with its own timeout) and a clip over the caps is
refused before anything is decoded; ffmpeg itself then runs on one thread, with
network protocols off, forced to the demuxer ffprobe identified, under address
space / CPU / file-size limits.
"""

from __future__ import annotations

import json
import logging
import os
import resource
import shutil
import subprocess
import tempfile

from app.application.project_photos.exceptions import ThumbnailGenerationError

_log = logging.getLogger(__name__)

# Max edge of the poster (matches the image thumbnailer's neighbourhood).
_MAX_EDGE = 480
# Hard wall-clock cap so a pathological clip can't hang the request worker.
_FFMPEG_TIMEOUT_SECONDS = 20
_FFPROBE_TIMEOUT_SECONDS = 10

# Largest declared frame: 8192 px on a side and 40 megapixels (8K UHD is 33 MP), so
# 4K and 8K phone clips still get a poster.
_MAX_SIDE = 8192
_MAX_PIXELS = 40_000_000

# Per-process limits for ffprobe / ffmpeg: address space (decoder buffers for a 40 MP
# frame stay well under it), CPU seconds, and the size of any file they may write.
_RLIMIT_AS_BYTES = 2 * 1024 * 1024 * 1024
_RLIMIT_CPU_SECONDS = 30
_RLIMIT_FSIZE_BYTES = 32 * 1024 * 1024

# The only containers phones and browsers produce for the accepted .mp4 / .mov / .webm
# uploads, as ffprobe names them -> the demuxer ffmpeg is told to use.
_DEMUXERS = {
    "mov,mp4,m4a,3gp,3g2,mj2": "mov",
    "matroska,webm": "matroska",
}


def _limit_resources() -> None:
    """preexec_fn of the child: cap what a hostile clip can make ffprobe / ffmpeg use."""
    try:
        resource.setrlimit(resource.RLIMIT_AS, (_RLIMIT_AS_BYTES, _RLIMIT_AS_BYTES))
    except (ValueError, OSError):  # not supported on every OS (macOS dev machines); Linux production has it
        pass
    resource.setrlimit(resource.RLIMIT_CPU, (_RLIMIT_CPU_SECONDS, _RLIMIT_CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_FSIZE, (_RLIMIT_FSIZE_BYTES, _RLIMIT_FSIZE_BYTES))


class FfmpegVideoThumbnailer:
    """Generate a JPEG poster frame from a video via the ffmpeg binary."""

    def generate(self, data: bytes, content_type: str) -> bytes:  # noqa: ARG002
        if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
            raise ThumbnailGenerationError("ffmpeg binary not available for video thumbnailing")

        with tempfile.TemporaryDirectory() as tmp:
            in_path = os.path.join(tmp, "input")
            out_path = os.path.join(tmp, "poster.jpg")
            with open(in_path, "wb") as fh:
                fh.write(data)

            demuxer = self._probe(in_path)

            # Try a frame ~1s in (skips black intro frames); fall back to the
            # very first frame for clips shorter than 1 second.
            for seek in ("1", "0"):
                if self._run_ffmpeg(in_path, out_path, seek, demuxer) and os.path.getsize(out_path) > 0:
                    with open(out_path, "rb") as fh:
                        return fh.read()

        raise ThumbnailGenerationError("ffmpeg produced no poster frame for the uploaded video")

    @staticmethod
    def _probe(in_path: str) -> str:
        """Check the clip's header against the caps; return the demuxer to force.

        Raises ThumbnailGenerationError (the route's 422) for anything unreadable,
        in another container, without a video stream, or over the size caps.
        """
        args = [
            "ffprobe",
            "-v",
            "error",
            "-protocol_whitelist",
            "file",
            "-show_entries",
            "stream=codec_type,width,height:format=format_name",
            "-of",
            "json",
            in_path,
        ]
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                timeout=_FFPROBE_TIMEOUT_SECONDS,
                check=False,
                preexec_fn=_limit_resources,
            )
        except subprocess.TimeoutExpired as exc:
            raise ThumbnailGenerationError("Video header could not be read in time") from exc
        except (subprocess.SubprocessError, OSError, ValueError) as exc:
            raise ThumbnailGenerationError("Video header could not be read") from exc
        if result.returncode != 0:
            raise ThumbnailGenerationError("Video could not be read")
        try:
            info = json.loads(result.stdout)
            demuxer = _DEMUXERS[info["format"]["format_name"]]
            # Every video stream is measured, not just the first: a tiny first stream must not
            # hide a huge second one.
            sizes = [
                (int(stream["width"]), int(stream["height"]))
                for stream in info["streams"]
                if stream.get("codec_type") == "video"
            ]
            if not sizes:
                raise ValueError("no video stream")
        except (ValueError, KeyError, TypeError) as exc:
            raise ThumbnailGenerationError("Video has no readable video stream in a supported container") from exc
        for width, height in sizes:
            if width < 1 or height < 1 or width > _MAX_SIDE or height > _MAX_SIDE or width * height > _MAX_PIXELS:
                raise ThumbnailGenerationError(f"Video frame size {width}x{height} exceeds the supported maximum")
        return demuxer

    @staticmethod
    def _run_ffmpeg(in_path: str, out_path: str, seek: str, demuxer: str) -> bool:
        # -ss before -i = fast input seek; -map 0:v:0 decodes the first video stream (the one
        # whose size ffprobe checked); -frames:v 1 grabs one frame;
        # scale keeps aspect, caps the long edge, ensures even dims (-2).
        # -threads 1 (decoder, then the JPEG encoder) bounds memory and CPU;
        # -protocol_whitelist file keeps a crafted container from reaching the network.
        args = [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-protocol_whitelist",
            "file",
            "-threads",
            "1",
            "-f",
            demuxer,
            "-ss",
            seek,
            "-i",
            in_path,
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-vf",
            f"scale='min({_MAX_EDGE},iw)':-2",
            "-threads",
            "1",
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
                preexec_fn=_limit_resources,
            )
        except subprocess.TimeoutExpired:
            _log.warning("ffmpeg poster extraction timed out (seek=%s)", seek)
            return False
        except (subprocess.SubprocessError, OSError, ValueError) as exc:
            raise ThumbnailGenerationError("ffmpeg could not be started for the uploaded video") from exc
        if result.returncode != 0:
            _log.info("ffmpeg poster attempt failed (seek=%s, rc=%s)", seek, result.returncode)
            return False
        return os.path.exists(out_path)
