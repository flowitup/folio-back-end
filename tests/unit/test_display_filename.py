"""Unit tests for the display-filename control-character rules."""

from __future__ import annotations

import pytest

from app.domain.value_objects.display_filename import has_control_chars, strip_control_chars


@pytest.mark.parametrize("name", ["a\nb.pdf", "a\rb.pdf", "a\tb.pdf", "a\x00b.pdf", "a\x1fb.pdf", "a\x7fb.pdf"])
def test_control_characters_are_detected_and_stripped(name):
    assert has_control_chars(name)
    assert strip_control_chars(name) == "ab.pdf"


@pytest.mark.parametrize("name", ["Plan RDC.pdf", "Hóa đơn tháng 3.pdf", "Devis n°12 (v2).pdf"])
def test_ordinary_names_are_left_alone(name):
    assert not has_control_chars(name)
    assert strip_control_chars(name) == name
