"""Unit tests for PillowImageThumbnailer."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from app.application.project_photos.exceptions import ThumbnailGenerationError
from app.infrastructure.adapters.pillow_image_thumbnailer import PillowImageThumbnailer

_MAX_EDGE = 480


def _make_png(width: int = 200, height: int = 100, color: tuple = (255, 0, 0)) -> bytes:
    """Build a minimal PNG in-memory."""
    img = Image.new("RGB", (width, height), color=color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_png_rgba(width: int = 200, height: int = 100) -> bytes:
    img = Image.new("RGBA", (width, height), color=(0, 128, 255, 128))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _png_with_exif_orientation(orientation_tag: int, width: int = 100, height: int = 200) -> bytes:
    """Create a PNG with an EXIF orientation tag.

    Orientation tag 6 means 90-degree CW rotation is required → dimensions swap
    when exif_transpose is applied (width becomes height and vice versa).
    """
    img = Image.new("RGB", (width, height), color=(10, 20, 30))
    exif = img.getexif()
    exif[0x0112] = orientation_tag  # 0x0112 = Orientation tag ID
    buf = BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes())
    return buf.getvalue()


class TestGenerate:
    def setup_method(self):
        self.thumbnailer = PillowImageThumbnailer()

    def test_returns_jpeg_bytes(self):
        data = _make_png(300, 200)
        result = self.thumbnailer.generate(data, "image/png")
        # JPEG magic bytes: FF D8 FF
        assert result[:3] == b"\xff\xd8\xff"

    def test_max_edge_constraint_wide_image(self):
        data = _make_png(1200, 300)
        result = self.thumbnailer.generate(data, "image/png")
        img = Image.open(BytesIO(result))
        assert max(img.width, img.height) <= _MAX_EDGE

    def test_max_edge_constraint_tall_image(self):
        data = _make_png(200, 900)
        result = self.thumbnailer.generate(data, "image/png")
        img = Image.open(BytesIO(result))
        assert max(img.width, img.height) <= _MAX_EDGE

    def test_small_image_not_enlarged(self):
        """Images smaller than _MAX_EDGE on both edges are not upscaled."""
        data = _make_png(100, 80)
        result = self.thumbnailer.generate(data, "image/png")
        img = Image.open(BytesIO(result))
        # Pillow thumbnail() only shrinks; original dims should be preserved
        assert img.width <= 100
        assert img.height <= 80

    def test_aspect_ratio_preserved(self):
        """Thumbnail must preserve aspect ratio (2:1 wide)."""
        data = _make_png(1000, 500)
        result = self.thumbnailer.generate(data, "image/png")
        img = Image.open(BytesIO(result))
        ratio = img.width / img.height
        assert abs(ratio - 2.0) < 0.1

    def test_rgba_png_converted_to_rgb_jpeg(self):
        """RGBA input must produce a valid JPEG (no alpha channel error)."""
        data = _make_png_rgba(400, 300)
        result = self.thumbnailer.generate(data, "image/png")
        img = Image.open(BytesIO(result))
        assert img.format == "JPEG"
        assert img.mode == "RGB"

    def test_content_type_ignored_sniffs_header(self):
        """content_type param is ignored; Pillow sniffs from file header."""
        data = _make_png(200, 150)
        # Pass wrong content_type — should still work
        result = self.thumbnailer.generate(data, "application/octet-stream")
        assert result[:3] == b"\xff\xd8\xff"

    def test_corrupt_bytes_raises_thumbnail_generation_error(self):
        with pytest.raises(ThumbnailGenerationError):
            self.thumbnailer.generate(b"notanimage", "image/jpeg")

    def test_empty_bytes_raises_thumbnail_generation_error(self):
        with pytest.raises(ThumbnailGenerationError):
            self.thumbnailer.generate(b"", "image/jpeg")


class TestImageModeCoverage:
    """Exercise the less-common image mode conversion branches in generate()."""

    def setup_method(self):
        self.thumbnailer = PillowImageThumbnailer()

    def _make_bytes(self, img: "Image.Image", fmt: str = "PNG") -> bytes:
        buf = BytesIO()
        img.save(buf, format=fmt)
        return buf.getvalue()

    def test_palette_mode_image_converted(self):
        """Palette-mode (P) PNG triggers the P→RGBA path (line 62)."""
        img = Image.new("P", (100, 80))
        data = self._make_bytes(img)
        result = self.thumbnailer.generate(data, "image/png")
        assert result[:3] == b"\xff\xd8\xff"

    def test_grayscale_image_converted(self):
        """Grayscale (L) PNG hits the elif img.mode != 'RGB' branch (line 66)."""
        img = Image.new("L", (80, 60), color=128)
        data = self._make_bytes(img)
        result = self.thumbnailer.generate(data, "image/png")
        out_img = Image.open(BytesIO(result))
        assert out_img.format == "JPEG"

    def test_la_mode_image_converted(self):
        """LA (grayscale+alpha) PNG triggers the RGBA/LA branch (line 63 mask path)."""
        img = Image.new("LA", (60, 60), (128, 200))
        data = self._make_bytes(img)
        result = self.thumbnailer.generate(data, "image/png")
        assert result[:3] == b"\xff\xd8\xff"

    def test_thumbnail_generation_error_re_raised(self):
        """ThumbnailGenerationError propagates without wrapping (line 73)."""
        from unittest.mock import patch as _patch
        from app.application.project_photos.exceptions import ThumbnailGenerationError

        data = _make_png(50, 50)
        with _patch("app.infrastructure.adapters.pillow_image_thumbnailer.Image.open") as mock_open:
            mock_open.side_effect = ThumbnailGenerationError("pre-existing")
            with pytest.raises(ThumbnailGenerationError, match="pre-existing"):
                self.thumbnailer.generate(data, "image/png")


class TestExifOrientation:
    """Assert that EXIF orientation is applied so portrait photos are not sideways."""

    def setup_method(self):
        self.thumbnailer = PillowImageThumbnailer()

    def test_orientation_6_rotates_dimensions(self):
        """EXIF orientation 6 = 90° CW → width and height swap after transpose.

        We build a 100×200 image (portrait but stored landscape) tagged with
        orientation=6. After exif_transpose the image should be 200×100 equivalent
        (or within thumbnail bounds proportionally).
        """
        data = _png_with_exif_orientation(6, width=100, height=200)
        result = self.thumbnailer.generate(data, "image/jpeg")
        img = Image.open(BytesIO(result))
        # After applying orientation=6 (90° CW), the stored 100×200 becomes
        # visually 200×100. Thumbnail then reduces to ≤480 on each edge.
        # The key assertion is that width > height (landscape) after rotation.
        assert img.width > img.height, f"Expected landscape dims after EXIF 90° CW, got {img.width}×{img.height}"

    def test_orientation_8_rotates_dimensions(self):
        """EXIF orientation 8 = 90° CCW → dimensions swap."""
        data = _png_with_exif_orientation(8, width=100, height=200)
        result = self.thumbnailer.generate(data, "image/jpeg")
        img = Image.open(BytesIO(result))
        assert img.width > img.height
