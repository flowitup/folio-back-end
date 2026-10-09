"""Image type detection from the file's own bytes, not the client's label."""

from io import BytesIO

import pytest

from app.domain.value_objects.image_signature import sniff_image_stream, sniff_image_type


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", "image/png"),
        (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00", "image/jpeg"),
        (b"RIFF\x24\x00\x00\x00WEBPVP8 ", "image/webp"),
        (b"RIFF\x24\x00\x00\x00WAVEfmt ", None),
        (b"<!doctype html><html>", None),
        (b"%PDF-1.4", None),
        (b"", None),
    ],
)
def test_sniff_image_type(head, expected):
    assert sniff_image_type(head) == expected


def test_sniff_image_stream_rewinds_for_the_upload():
    data = b"\x89PNG\r\n\x1a\n" + b"rest of the file"
    stream = BytesIO(data)
    assert sniff_image_stream(stream) == "image/png"
    assert stream.read() == data
