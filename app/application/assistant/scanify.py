"""Feature C's OpenCV scan pipeline (``SCAN_MODE=opencv``, or the genai path's fallback).

Grayscale -> blur -> Canny -> largest 4-point contour covering >= 20% of the frame ->
perspective warp -> adaptive threshold. When no such contour is found, and a vision port
is supplied, asks DeepSeek for the four corners as percentage coordinates (one call, a
pydantic-validated ``Corners`` model) instead of guessing. ``to_pdf`` renders the result
at 200 dpi via ``img2pdf`` — the PDF attached to the invoice, whichever mode produced it.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from pydantic import BaseModel, Field

from app.application.assistant.ports import VisionLlmPort

#: A candidate contour must cover at least this fraction of the frame to be trusted as
#: "the receipt", not background clutter.
MIN_CONTOUR_AREA_RATIO = 0.20
#: img2pdf render resolution (plan section 3: "200 dpi" for the OpenCV-mode PDF).
PDF_DPI = 200

# Keep byte-identical across every call — see extract.py's S1 prompt for why.
CORNERS_SYSTEM_PROMPT_FR = (
    "Tu localises les 4 coins d'un ticket de caisse ou d'une facture sur une photo. Réponds uniquement avec un "
    "JSON {points: [{x, y}, {x, y}, {x, y}, {x, y}]} où x et y sont des pourcentages (0-100) de la largeur et de "
    "la hauteur de l'image, dans l'ordre haut-gauche, haut-droite, bas-droite, bas-gauche. N'invente rien "
    "d'autre."
)
_CORNERS_USER_TEXT = "Localise les 4 coins du document."


class CornerPoint(BaseModel):
    x: float
    y: float


class Corners(BaseModel):
    """Output of the DeepSeek corner-finding fallback — exactly 4 points."""

    points: list[CornerPoint] = Field(min_length=4, max_length=4)


def _order_points(points: np.ndarray) -> np.ndarray:
    """Order 4 points as top-left, top-right, bottom-right, bottom-left."""
    total = points.sum(axis=1)
    diff = np.diff(points, axis=1).flatten()
    ordered = np.zeros((4, 2), dtype="float32")
    ordered[0] = points[int(np.argmin(total))]
    ordered[2] = points[int(np.argmax(total))]
    ordered[1] = points[int(np.argmin(diff))]
    ordered[3] = points[int(np.argmax(diff))]
    return ordered


def _warp(gray: np.ndarray, quad: np.ndarray) -> np.ndarray:
    import cv2

    rect = _order_points(np.array(quad, dtype="float32"))
    top_left, top_right, bottom_right, bottom_left = rect
    width_a = float(np.linalg.norm(bottom_right - bottom_left))
    width_b = float(np.linalg.norm(top_right - top_left))
    max_width = max(int(width_a), int(width_b), 1)
    height_a = float(np.linalg.norm(top_right - bottom_right))
    height_b = float(np.linalg.norm(top_left - bottom_left))
    max_height = max(int(height_a), int(height_b), 1)
    destination = np.array(
        [[0, 0], [max_width - 1, 0], [max_width - 1, max_height - 1], [0, max_height - 1]], dtype="float32"
    )
    matrix = cv2.getPerspectiveTransform(rect, destination)
    warped: np.ndarray = cv2.warpPerspective(gray, matrix, (max_width, max_height))
    return warped


def _largest_quad(contours: list[np.ndarray], frame_area: float) -> Optional[np.ndarray]:
    import cv2

    best: Optional[np.ndarray] = None
    best_area = 0.0
    for contour in contours:
        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if len(approx) != 4:
            continue
        area = cv2.contourArea(approx)
        if area < MIN_CONTOUR_AREA_RATIO * frame_area:
            continue
        if area > best_area:
            best_area = area
            best = approx.reshape(4, 2)
    return best


def _corners_from_vision(vision: VisionLlmPort, image_bytes: bytes, width: int, height: int) -> Optional[np.ndarray]:
    try:
        corners = vision.chat_json(
            system=CORNERS_SYSTEM_PROMPT_FR,
            user_text=_CORNERS_USER_TEXT,
            images=[image_bytes],
            model_cls=Corners,
        )
    except Exception:
        return None
    return np.array([[point.x / 100.0 * width, point.y / 100.0 * height] for point in corners.points], dtype="float32")


def scanify(image_bytes: bytes, vision: Optional[VisionLlmPort] = None) -> bytes:
    """Return a processed (perspective-corrected, thresholded) JPEG, ready for `to_pdf`.

    Falls back to a DeepSeek corner prompt when no >= 20%-of-frame quadrilateral contour
    is found, then to an unwarped (but still thresholded) grayscale image when even that
    is unavailable — this function always returns something rather than raising, since
    a bad scan is still strictly better than none for the assistant's reply.
    """
    import cv2

    array = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("scanify: could not decode image bytes.")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    frame_area = float(image.shape[0] * image.shape[1])
    quad = _largest_quad(list(contours), frame_area)

    if quad is None and vision is not None:
        height, width = gray.shape[:2]
        quad = _corners_from_vision(vision, image_bytes, width, height)

    warped = _warp(gray, quad) if quad is not None else gray
    thresholded = cv2.adaptiveThreshold(warped, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 25, 15)
    ok, buffer = cv2.imencode(".jpg", thresholded)
    if not ok:
        raise ValueError("scanify: failed to encode the processed image.")
    return bytes(buffer.tobytes())


def to_pdf(image_bytes: bytes, dpi: int = PDF_DPI) -> bytes:
    """Render a scan image into a single-page PDF at ``dpi`` (plan: 200 dpi)."""
    import img2pdf

    layout_fun = img2pdf.get_fixed_dpi_layout_fun((dpi, dpi))
    result: bytes = img2pdf.convert(image_bytes, layout_fun=layout_fun)
    return result
