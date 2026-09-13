from __future__ import annotations

import io
import json
from email.message import Message
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

from releaseprobe.imageref import ImageReference
from releaseprobe.registry import fetch_labels, list_tags


class FakeResponse:
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self._body = body
        self.headers = Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


def _json_response(status: int, data: Any, headers: dict[str, str] | None = None) -> FakeResponse:
    return FakeResponse(status, json.dumps(data).encode(), headers)


def _auth_error(realm: str = "https://auth.example.com/token", service: str = "example.com"):
    headers = Message()
    headers["WWW-Authenticate"] = f'Bearer realm="{realm}",service="{service}",scope="pull"'
    return HTTPError("https://example.com", 401, "Unauthorized", headers, io.BytesIO(b"{}"))


REF = ImageReference(registry="ghcr.io", repository="org/app", tag="1.2.3")


def test_list_tags_simple() -> None:
    def fake_urlopen(request: Request, timeout: int = 10) -> FakeResponse:
        assert request.full_url == "https://ghcr.io/v2/org/app/tags/list"
        return _json_response(200, {"tags": ["1.0.0", "1.2.3"]})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        assert list_tags(REF) == ["1.0.0", "1.2.3"]


def test_list_tags_handles_auth_challenge() -> None:
    calls: list[str] = []

    def fake_urlopen(request: Request, timeout: int = 10) -> FakeResponse:
        calls.append(request.full_url)
        if request.full_url == "https://ghcr.io/v2/org/app/tags/list":
            if request.get_header("Authorization") is None:
                raise _auth_error()
            assert request.get_header("Authorization") == "Bearer test-token"
            return _json_response(200, {"tags": ["1.2.3"]})
        if request.full_url.startswith("https://auth.example.com/token"):
            return _json_response(200, {"token": "test-token"})
        raise AssertionError(f"unexpected URL {request.full_url}")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        assert list_tags(REF) == ["1.2.3"]
    assert calls.count("https://ghcr.io/v2/org/app/tags/list") == 2


def test_list_tags_follows_pagination() -> None:
    page_one_url = "https://ghcr.io/v2/org/app/tags/list"
    page_two_url = "https://ghcr.io/v2/org/app/tags/list?n=1&last=1.0.0"

    def fake_urlopen(request: Request, timeout: int = 10) -> FakeResponse:
        if request.full_url == page_one_url:
            return _json_response(
                200, {"tags": ["1.0.0"]}, {"Link": f'<{page_two_url}>; rel="next"'}
            )
        if request.full_url == page_two_url:
            return _json_response(200, {"tags": ["1.2.3"]})
        raise AssertionError(f"unexpected URL {request.full_url}")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        assert list_tags(REF) == ["1.0.0", "1.2.3"]


MANIFEST_URL = "https://ghcr.io/v2/org/app/manifests/1.2.3"
BLOB_URL = "https://ghcr.io/v2/org/app/blobs/sha256:configdigest"


def test_fetch_labels_single_manifest() -> None:
    manifest = {
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {"digest": "sha256:configdigest"},
    }
    config = {"config": {"Labels": {"org.opencontainers.image.version": "1.2.3"}}}

    def fake_urlopen(request: Request, timeout: int = 10) -> FakeResponse:
        if request.full_url == MANIFEST_URL:
            return _json_response(200, manifest)
        if request.full_url == BLOB_URL:
            return _json_response(200, config)
        raise AssertionError(f"unexpected URL {request.full_url}")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        assert fetch_labels(REF) == {"org.opencontainers.image.version": "1.2.3"}


def test_fetch_labels_resolves_manifest_list() -> None:
    manifest_list = {
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [
            {"digest": "sha256:arm64digest", "platform": {"os": "linux", "architecture": "arm64"}},
            {"digest": "sha256:amd64digest", "platform": {"os": "linux", "architecture": "amd64"}},
        ],
    }
    amd64_manifest = {
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {"digest": "sha256:configdigest"},
    }
    config = {"config": {"Labels": {"org.opencontainers.image.source": "https://github.com/org/app"}}}

    def fake_urlopen(request: Request, timeout: int = 10) -> FakeResponse:
        if request.full_url == MANIFEST_URL:
            return _json_response(200, manifest_list)
        if request.full_url == "https://ghcr.io/v2/org/app/manifests/sha256:amd64digest":
            return _json_response(200, amd64_manifest)
        if request.full_url == BLOB_URL:
            return _json_response(200, config)
        raise AssertionError(f"unexpected URL {request.full_url}")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        assert fetch_labels(REF) == {"org.opencontainers.image.source": "https://github.com/org/app"}
