"""Pillow-backed IImageThumbnailer adapter.

Generates a server-side JPEG thumbnail with EXIF orientation correction.
Re-encoding through Pillow strips any malicious payloads embedded in the
original (defense against polyglot images on the thumbnail delivery path).
"""

from __future__ import annotations

import warnings
from io import BytesIO

from PIL import Image, ImageOps

from app.application.project_photos.exceptions import ThumbnailGenerationError

# Max dimension (width or height) for the thumbnail — keeps the output small
# while still being useful for a photo gallery grid view.
_MAX_EDGE = 480

# Hard ceiling on decoded pixel count: 80 megapixels.  Images larger than this
# are either decompression bombs or pathologically large; reject before
# allocating the full bitmap.  Pillow's own bomb guard uses the same threshold
# but only raises a warning by default — we set the module-level cap so Pillow's
# internal check also activates, then escalate to a hard error in generate().
_MAX_PIXELS = 80_000_000
Image.MAX_IMAGE_PIXELS = _MAX_PIXELS


class PillowImageThumbnailer:
    """Implements IImageThumbnailer using Pillow.

    - Applies EXIF orientation before resizing so portrait phone photos
      are not rendered sideways in the browser.
    - Converts to RGB before saving as JPEG (drops alpha channel if present,
      composited onto a white background).
    - Output is always JPEG quality 80 regardless of input format.
    """

    def generate(self, data: bytes, content_type: str) -> bytes:  # noqa: ARG002
        """Return JPEG thumbnail bytes, max _MAX_EDGE px on either edge.

        Args:
            data: Raw image bytes (JPEG, PNG, or WebP).
            content_type: MIME type of the input (unused — Pillow sniffs the
                          header itself, which is safer than trusting the client).

        Returns:
            JPEG-encoded thumbnail bytes.

        Raises:
            ThumbnailGenerationError: If the image cannot be decoded or resized,
                or if the declared pixel count exceeds the 80 MP decompression-bomb
                ceiling.
        """
        try:
            # Promote DecompressionBombWarning to an error within this block only
            # so we do not pollute the global warning filter for the rest of the
            # process.
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                img = Image.open(BytesIO(data))

            # img.size is populated lazily from the file header — reading it is
            # cheap and does not decode the full bitmap.  Reject gigapixel
            # declarations before Pillow allocates memory for the pixel array.
            width, height = img.size
            if width * height > _MAX_PIXELS:
                raise ThumbnailGenerationError(
                    f"Image dimensions {width}×{height} ({width * height:,} px) exceed "
                    f"the {_MAX_PIXELS:,} pixel decompression-bomb ceiling"
                )

            # Apply EXIF orientation tag so the thumbnail is correctly rotated.
            # ImageOps.exif_transpose is the canonical Pillow approach.
            img = ImageOps.exif_transpose(img)

            # thumbnail() is in-place and aspect-preserving — it only shrinks,
            # never enlarges.  Images already smaller than _MAX_EDGE are kept as-is.
            img.thumbnail((_MAX_EDGE, _MAX_EDGE))

            # JPEG does not support an alpha channel; convert RGBA/P to RGB.
            # Paste on a white background rather than discarding the alpha so that
            # transparent PNGs (logos, diagrams) don't become black on save.
            if img.mode in ("RGBA", "LA", "P"):
                background = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "P":
                    img = img.convert("RGBA")
                background.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
                img = background
            elif img.mode != "RGB":
                img = img.convert("RGB")

            out = BytesIO()
            img.save(out, format="JPEG", quality=80, optimize=True)
            return out.getvalue()

        except ThumbnailGenerationError:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ThumbnailGenerationError(f"Decompression bomb detected: {exc}") from exc
        except Exception as exc:
            raise ThumbnailGenerationError(f"Failed to generate thumbnail: {exc}") from exc
