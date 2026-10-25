from __future__ import annotations

import io
import json
from email.message import Message
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from releaseprobe import registry
from releaseprobe.imageref import ImageReference
from releaseprobe.registry import (
    RegistryError,
    fetch_digest,
    fetch_labels,
    fetch_provenance_source,
    list_tags,
)


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


def test_list_tags_reuses_token_across_pages() -> None:
    page_one_url = "https://ghcr.io/v2/org/app/tags/list"
    page_two_url = "https://ghcr.io/v2/org/app/tags/list?n=1&last=1.0.0"
    calls: list[str] = []

    def fake_urlopen(request: Request, timeout: int = 10) -> FakeResponse:
        calls.append(request.full_url)
        if request.full_url.startswith("https://auth.example.com/token"):
            return _json_response(200, {"token": "test-token"})
        if request.get_header("Authorization") is None:
            raise _auth_error()
        if request.full_url == page_one_url:
            return _json_response(
                200, {"tags": ["1.0.0"]}, {"Link": f'<{page_two_url}>; rel="next"'}
            )
        if request.full_url == page_two_url:
            return _json_response(200, {"tags": ["1.2.3"]})
        raise AssertionError(f"unexpected URL {request.full_url}")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        assert list_tags(REF) == ["1.0.0", "1.2.3"]
    # One challenge + token fetch for the first page, the second page reuses the token.
    assert sum(url.startswith("https://auth.example.com/token") for url in calls) == 1
    assert calls.count(page_two_url) == 1


def test_list_tags_retries_network_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "_RETRY_DELAYS", (0.0, 0.0))
    attempts: list[str] = []

    def fake_urlopen(request: Request, timeout: int = 10) -> FakeResponse:
        attempts.append(request.full_url)
        if len(attempts) < 3:
            raise URLError("The handshake operation timed out")
        return _json_response(200, {"tags": ["1.2.3"]})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        assert list_tags(REF) == ["1.2.3"]
    assert len(attempts) == 3


def test_list_tags_gives_up_after_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "_RETRY_DELAYS", (0.0, 0.0))
    attempts: list[str] = []

    def fake_urlopen(request: Request, timeout: int = 10) -> FakeResponse:
        attempts.append(request.full_url)
        raise TimeoutError("timed out")

    with (
        patch("urllib.request.urlopen", side_effect=fake_urlopen),
        pytest.raises(RegistryError, match="could not reach"),
    ):
        list_tags(REF)
    assert len(attempts) == 3


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


def test_fetch_digest_uses_head_request() -> None:
    def fake_urlopen(request: Request, timeout: int = 10) -> FakeResponse:
        assert request.full_url == MANIFEST_URL
        assert request.get_method() == "HEAD"
        assert "manifest.list" in request.get_header("Accept")
        return FakeResponse(200, b"", {"Docker-Content-Digest": "sha256:indexdigest"})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        assert fetch_digest(REF) == "sha256:indexdigest"


def test_fetch_digest_without_header() -> None:
    with patch("urllib.request.urlopen", return_value=FakeResponse(200, b"")):
        assert fetch_digest(REF) is None


def test_fetch_labels_prefers_manifest_annotations() -> None:
    # e.g. netbirdio/netbird: the config only has the ubuntu base image's
    # version label, the real version and source are index annotations.
    manifest_list = {
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "annotations": {
            "org.opencontainers.image.version": "0.81.4",
            "org.opencontainers.image.source": "https://github.com/org/app",
        },
        "manifests": [
            {"digest": "sha256:amd64digest", "platform": {"os": "linux", "architecture": "amd64"}},
        ],
    }
    amd64_manifest = {
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "annotations": {"org.opencontainers.image.title": "app"},
        "config": {"digest": "sha256:configdigest"},
    }
    config = {"config": {"Labels": {"org.opencontainers.image.version": "24.04", "x": "y"}}}

    def fake_urlopen(request: Request, timeout: int = 10) -> FakeResponse:
        if request.full_url == MANIFEST_URL:
            return _json_response(200, manifest_list)
        if request.full_url == "https://ghcr.io/v2/org/app/manifests/sha256:amd64digest":
            return _json_response(200, amd64_manifest)
        if request.full_url == BLOB_URL:
            return _json_response(200, config)
        raise AssertionError(f"unexpected URL {request.full_url}")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        assert fetch_labels(REF) == {
            "org.opencontainers.image.version": "0.81.4",
            "org.opencontainers.image.source": "https://github.com/org/app",
            "org.opencontainers.image.title": "app",
            "x": "y",
        }


def _attested_index() -> dict[str, Any]:
    return {
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [
            {"digest": "sha256:arm64digest", "platform": {"os": "linux", "architecture": "arm64"}},
            {
                "digest": "sha256:armattestation",
                "platform": {"os": "unknown", "architecture": "unknown"},
                "annotations": {
                    "vnd.docker.reference.type": "attestation-manifest",
                    "vnd.docker.reference.digest": "sha256:arm64digest",
                },
            },
            {"digest": "sha256:amd64digest", "platform": {"os": "linux", "architecture": "amd64"}},
            {
                "digest": "sha256:attestation",
                "platform": {"os": "unknown", "architecture": "unknown"},
                "annotations": {
                    "vnd.docker.reference.type": "attestation-manifest",
                    "vnd.docker.reference.digest": "sha256:amd64digest",
                },
            },
        ],
    }


_VCS = {"vcs": {"source": "https://github.com/org/repo"}}


@pytest.mark.parametrize(
    "predicate",
    [
        # SLSA provenance v1
        {"runDetails": {"metadata": {"buildkit_metadata": _VCS}}},
        # SLSA provenance v0.2
        {"metadata": {"https://mobyproject.org/buildkit@v1#metadata": _VCS}},
    ],
)
def test_fetch_provenance_source(predicate: dict[str, Any]) -> None:
    attestation = {
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "layers": [
            {
                "digest": "sha256:sbom",
                "annotations": {"in-toto.io/predicate-type": "https://spdx.dev/Document"},
            },
            {
                "digest": "sha256:provenance",
                "annotations": {"in-toto.io/predicate-type": "https://slsa.dev/provenance/v1"},
            },
        ],
    }

    def fake_urlopen(request: Request, timeout: int = 10) -> FakeResponse:
        if request.full_url == MANIFEST_URL:
            return _json_response(200, _attested_index())
        if request.full_url == "https://ghcr.io/v2/org/app/manifests/sha256:attestation":
            return _json_response(200, attestation)
        if request.full_url == "https://ghcr.io/v2/org/app/blobs/sha256:provenance":
            return _json_response(200, {"predicate": predicate})
        raise AssertionError(f"unexpected URL {request.full_url}")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        assert fetch_provenance_source(REF) == "https://github.com/org/repo"


def test_fetch_provenance_source_without_attestation() -> None:
    index = _attested_index()
    index["manifests"] = [m for m in index["manifests"] if "annotations" not in m]
    with patch("urllib.request.urlopen", return_value=_json_response(200, index)):
        assert fetch_provenance_source(REF) is None


def test_fetch_provenance_source_for_single_manifest() -> None:
    manifest = {"mediaType": "application/vnd.oci.image.manifest.v1+json", "config": {}}
    with patch("urllib.request.urlopen", return_value=_json_response(200, manifest)):
        assert fetch_provenance_source(REF) is None
