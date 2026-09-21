"""Unit tests for `app.application.assistant.scanify` — the OpenCV fallback pipeline.

Builds a synthetic "receipt" with numpy/cv2 (a white quadrilateral on a dark background)
rather than shipping a real photo fixture — the geometry is what `scanify` cares about,
not photorealism.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from app.application.assistant.scanify import Corners, scanify, to_pdf
from tests.fakes.ai import ScriptedVision


def _synthetic_receipt(rotated: bool = False) -> bytes:
    canvas = np.zeros((500, 400, 3), dtype=np.uint8)
    if rotated:
        points = np.array([[120, 40], [360, 90], [300, 460], [60, 410]], dtype=np.int32)
        cv2.fillPoly(canvas, [points], (255, 255, 255))
    else:
        cv2.rectangle(canvas, (60, 40), (340, 460), (255, 255, 255), -1)
    cv2.putText(canvas, "TICKET 12.34E", (80, 250), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)
    ok, buffer = cv2.imencode(".jpg", canvas)
    assert ok
    return buffer.tobytes()


class TestScanify:
    def test_returns_jpeg_bytes_for_axis_aligned_receipt(self) -> None:
        result = scanify(_synthetic_receipt())
        assert isinstance(result, bytes)
        assert len(result) > 0
        # A JPEG magic number confirms cv2.imencode(".jpg", ...) round-tripped.
        assert result[:2] == b"\xff\xd8"

    def test_warps_a_rotated_quadrilateral(self) -> None:
        result = scanify(_synthetic_receipt(rotated=True))
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_falls_back_to_vision_corners_when_no_contour_found(self) -> None:
        # A blank frame has no >=20%-of-frame quadrilateral contour — scanify must ask
        # the vision port for corners instead of raising.
        blank = np.full((300, 300, 3), 127, dtype=np.uint8)
        ok, buffer = cv2.imencode(".jpg", blank)
        assert ok
        vision = ScriptedVision(
            json_answers=[
                Corners(points=[{"x": 10, "y": 10}, {"x": 90, "y": 10}, {"x": 90, "y": 90}, {"x": 10, "y": 90}])
            ]
        )
        result = scanify(buffer.tobytes(), vision=vision)
        assert isinstance(result, bytes)
        assert len(vision.json_calls) == 1

    def test_raises_on_undecodable_bytes(self) -> None:
        with pytest.raises(ValueError):
            scanify(b"not-an-image")


class TestToPdf:
    def test_produces_a_valid_pdf(self) -> None:
        scan = scanify(_synthetic_receipt())
        pdf_bytes = to_pdf(scan)
        assert pdf_bytes.startswith(b"%PDF-")
        assert len(pdf_bytes) > 0
