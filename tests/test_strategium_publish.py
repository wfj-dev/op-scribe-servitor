import asyncio
from types import SimpleNamespace

from opscribe import strategium_publish as publisher


def test_publish_url_accepts_https_and_loopback_http(monkeypatch):
    monkeypatch.setenv("STRATEGIUM_PUBLISH_URL", "https://example.test/internal/roster/snapshot")
    assert publisher.publish_url().startswith("https://")

    monkeypatch.setenv("STRATEGIUM_PUBLISH_URL", "http://127.0.0.1:8787/internal/roster/snapshot")
    assert publisher.publish_url().startswith("http://127.0.0.1")

    monkeypatch.setenv("STRATEGIUM_PUBLISH_URL", "http://localhost:8787/internal/roster/snapshot")
    assert publisher.publish_url().startswith("http://localhost")

    monkeypatch.setenv("STRATEGIUM_PUBLISH_URL", "http://evil.example/internal/roster/snapshot")
    assert publisher.publish_url() == ""


def test_publish_snapshot_disables_redirects(monkeypatch):
    monkeypatch.setenv("STRATEGIUM_PUBLISH_URL", "http://127.0.0.1:8787/internal/roster/snapshot")
    monkeypatch.setenv("STRATEGIUM_BOT_SHARED_SECRET", "test-secret")
    monkeypatch.setattr(publisher, "build_snapshot", lambda _bot: {"members": []})

    captured = {}

    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class Session:
        def post(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return Response()

    result = asyncio.run(publisher.publish_snapshot(SimpleNamespace(), Session()))

    assert result is True
    assert captured["allow_redirects"] is False