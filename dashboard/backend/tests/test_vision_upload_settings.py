"""VISION_UPLOAD_DIR config (spec 2026-05-21)."""
from pathlib import Path

from app.config import Settings


def test_vision_upload_dir_default():
    s = Settings()
    assert Path(s.vision_upload_dir) == Path("/var/lib/claude-agent-station/vision-chat-uploads")


def test_vision_upload_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("STATION_VISION_UPLOAD_DIR", str(tmp_path / "uploads"))
    s = Settings()
    assert Path(s.vision_upload_dir) == tmp_path / "uploads"
