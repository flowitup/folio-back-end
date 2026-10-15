"""Unit tests for the display-filename control-character rules."""

from __future__ import annotations

import pytest

from app.domain.value_objects.display_filename import has_control_chars, quoted_ascii_filename, strip_control_chars


@pytest.mark.parametrize("name", ["a\nb.pdf", "a\rb.pdf", "a\tb.pdf", "a\x00b.pdf", "a\x1fb.pdf", "a\x7fb.pdf"])
def test_control_characters_are_detected_and_stripped(name):
    assert has_control_chars(name)
    assert strip_control_chars(name) == "ab.pdf"


@pytest.mark.parametrize("name", ["Plan RDC.pdf", "Hóa đơn tháng 3.pdf", "Devis n°12 (v2).pdf"])
def test_ordinary_names_are_left_alone(name):
    assert not has_control_chars(name)
    assert strip_control_chars(name) == name


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ('FAC"; x=y.pdf', "FAC_; x=y.pdf"),
        ("a\\b.pdf", "a_b.pdf"),
        ("Hóa đơn.pdf", "H_a __n.pdf"),
        ("a\r\nb.pdf", "ab.pdf"),
        ("FAC-2026-001.pdf", "FAC-2026-001.pdf"),
    ],
)
def test_quoted_ascii_filename_cannot_end_the_quoted_parameter(name, expected):
    assert quoted_ascii_filename(name) == expected
    assert not any(c in quoted_ascii_filename(name) for c in '"\\\r\n')
