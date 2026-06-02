"""Media thumbnailer dispatcher.

Routes thumbnail generation to the right backend by media kind: images go
through Pillow, videos through ffmpeg poster-frame extraction. Implements the
same ``IImageThumbnailer.generate(data, content_type)`` contract so the upload
use case stays agnostic of media type.
"""

from __future__ import annotations

from app.application.project_photos.ports import IImageThumbnailer


class MediaThumbnailer:
    """Dispatch thumbnail generation to image or video backend by content type."""

    def __init__(
        self,
        image_thumbnailer: IImageThumbnailer,
        video_thumbnailer: IImageThumbnailer,
    ) -> None:
        self._image = image_thumbnailer
        self._video = video_thumbnailer

    def generate(self, data: bytes, content_type: str) -> bytes:
        if content_type.startswith("video/"):
            return self._video.generate(data, content_type)
        return self._image.generate(data, content_type)
