"""Filename sanitisation for vision attachments (spec 2026-05-21)."""
import pytest

from app.services.vision_attachments import sanitize_filename


@pytest.mark.parametrize("raw,expected", [
    ("simple.xlsx", "simple.xlsx"),
    ("with spaces.pdf", "with spaces.pdf"),
    ("bad<chars>.csv", "badchars.csv"),
    ("bad|chars?.xlsx", "badchars.xlsx"),
    ('quotes"and:colons.pdf', "quotesandcolons.pdf"),
    ("../escape.md", "escape.md"),
    ("....many.dots.txt", "many.dots.txt"),
    ("/abs/path/foo.xlsx", "foo.xlsx"),
    ("C:\\win\\foo.xlsx", "foo.xlsx"),
])
def test_sanitize_strips_forbidden_chars_and_paths(raw, expected):
    assert sanitize_filename(raw) == expected


def test_sanitize_rejects_empty():
    with pytest.raises(ValueError):
        sanitize_filename("")
    with pytest.raises(ValueError):
        sanitize_filename("....")


def test_sanitize_caps_length():
    long = "a" * 300 + ".xlsx"
    out = sanitize_filename(long)
    assert len(out) <= 255
    assert out.endswith(".xlsx")
