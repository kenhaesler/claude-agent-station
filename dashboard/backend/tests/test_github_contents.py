import base64
import pytest
import httpx
from unittest.mock import patch
from app.services.github_contents import read_file, ContentsResult, FileNotFound


@pytest.mark.asyncio
async def test_read_file_returns_decoded_body_and_sha():
    fake = {
        "sha": "abc123",
        "content": base64.b64encode(b"# hello\n").decode(),
        "encoding": "base64",
        "html_url": "https://github.com/o/r/blob/main/docs/vision.md",
    }
    with patch("app.services.github_contents._get_token", return_value="ghi"):
        async def fake_get(self, url, headers=None, params=None):
            return httpx.Response(200, json=fake, request=httpx.Request("GET", url))
        with patch.object(httpx.AsyncClient, "get", new=fake_get):
            result = await read_file(repo="o/r", path="docs/vision.md", branch="main")
    assert isinstance(result, ContentsResult)
    assert result.sha == "abc123"
    assert result.body == "# hello\n"
    assert result.html_url.endswith("/docs/vision.md")


@pytest.mark.asyncio
async def test_read_file_404_raises_FileNotFound():
    with patch("app.services.github_contents._get_token", return_value="ghi"):
        async def fake_get(self, url, headers=None, params=None):
            return httpx.Response(404, json={"message": "Not Found"}, request=httpx.Request("GET", url))
        with patch.object(httpx.AsyncClient, "get", new=fake_get):
            with pytest.raises(FileNotFound):
                await read_file(repo="o/r", path="docs/vision.md", branch="main")
