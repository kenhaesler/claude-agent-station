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


import json

@pytest.mark.asyncio
async def test_write_file_creates_new_when_no_sha():
    captured = {}
    async def fake_put(self, url, headers=None, json=None):
        captured["url"] = url
        captured["body"] = json
        return httpx.Response(
            201,
            json={"content": {"sha": "new-sha"}, "commit": {"sha": "commit-sha"}},
            request=httpx.Request("PUT", url),
        )
    with patch("app.services.github_contents._get_token", return_value="ghi"):
        with patch.object(httpx.AsyncClient, "put", new=fake_put):
            from app.services.github_contents import write_file
            new_sha = await write_file(
                repo="o/r", path="docs/vision.md", branch="main",
                body="# hello\n", message="docs: test", current_sha=None,
            )
    assert new_sha == "new-sha"
    assert "sha" not in captured["body"]  # no sha sent on create


@pytest.mark.asyncio
async def test_write_file_updates_when_sha_matches():
    async def fake_put(self, url, headers=None, json=None):
        # Echo back a new sha to indicate success
        return httpx.Response(200, json={"content": {"sha": "updated-sha"}}, request=httpx.Request("PUT", url))
    with patch("app.services.github_contents._get_token", return_value="ghi"):
        with patch.object(httpx.AsyncClient, "put", new=fake_put):
            from app.services.github_contents import write_file
            new_sha = await write_file(
                repo="o/r", path="docs/vision.md", branch="main",
                body="# new\n", message="docs: refine", current_sha="old-sha",
            )
    assert new_sha == "updated-sha"


@pytest.mark.asyncio
async def test_write_file_409_on_stale_sha_raises_StaleSha():
    """When GitHub returns 409 on PUT, we re-fetch and raise StaleSha with current state."""
    async def fake_put(self, url, headers=None, json=None):
        return httpx.Response(409, json={"message": "stale sha"}, request=httpx.Request("PUT", url))
    fake_current = {
        "sha": "newer-sha",
        "content": base64.b64encode(b"someone else wrote this").decode(),
        "encoding": "base64",
        "html_url": "x",
    }
    async def fake_get(self, url, headers=None, params=None):
        return httpx.Response(200, json=fake_current, request=httpx.Request("GET", url))
    with patch("app.services.github_contents._get_token", return_value="ghi"):
        with patch.object(httpx.AsyncClient, "put", new=fake_put), \
             patch.object(httpx.AsyncClient, "get", new=fake_get):
            from app.services.github_contents import write_file, StaleSha
            with pytest.raises(StaleSha) as exc:
                await write_file(
                    repo="o/r", path="docs/vision.md", branch="main",
                    body="# mine\n", message="docs: refine", current_sha="my-old-sha",
                )
    assert exc.value.current_sha == "newer-sha"
    assert exc.value.current_body == "someone else wrote this"


@pytest.mark.asyncio
async def test_write_file_422_on_stale_sha_raises_StaleSha():
    """GitHub Contents API may return 422 (not 409) for stale-sha; handle both."""
    async def fake_put(self, url, headers=None, json=None):
        return httpx.Response(422, json={"message": "sha doesn't match"}, request=httpx.Request("PUT", url))
    fake_current = {
        "sha": "newer-sha",
        "content": base64.b64encode(b"someone else wrote this").decode(),
        "encoding": "base64",
        "html_url": "x",
    }
    async def fake_get(self, url, headers=None, params=None):
        return httpx.Response(200, json=fake_current, request=httpx.Request("GET", url))
    with patch("app.services.github_contents._get_token", return_value="ghi"):
        with patch.object(httpx.AsyncClient, "put", new=fake_put), \
             patch.object(httpx.AsyncClient, "get", new=fake_get):
            from app.services.github_contents import write_file, StaleSha
            with pytest.raises(StaleSha) as exc:
                await write_file(
                    repo="o/r", path="docs/vision.md", branch="main",
                    body="# mine\n", message="docs: refine", current_sha="my-old-sha",
                )
    assert exc.value.current_sha == "newer-sha"
    assert exc.value.current_body == "someone else wrote this"
