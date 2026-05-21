"""MIME sniffing + allowlist for vision attachments (spec 2026-05-21)."""
import pytest

from app.services.vision_attachments import (
    AttachmentRejected, ALLOWED_MIMES, sniff_and_validate_mime,
)


def _pdf_bytes() -> bytes:
    return b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


def _png_bytes() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def test_sniff_accepts_pdf():
    mime = sniff_and_validate_mime(_pdf_bytes(), declared_filename="x.pdf")
    assert mime == "application/pdf"


def test_sniff_accepts_png():
    mime = sniff_and_validate_mime(_png_bytes(), declared_filename="x.png")
    assert mime == "image/png"


def test_sniff_rejects_unknown_type():
    with pytest.raises(AttachmentRejected) as exc:
        sniff_and_validate_mime(b"\x00\x01\x02not a real file", declared_filename="x.bin")
    assert "not a supported" in str(exc.value).lower()


def test_allowed_mimes_contains_expected_set():
    expected = {
        "application/pdf",
        "image/png", "image/jpeg", "image/gif", "image/webp",
        "text/plain", "text/markdown", "text/csv",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    assert expected <= ALLOWED_MIMES
