"""Minimal Docker Registry HTTP API v2 client.

Implements just enough of the spec (https://distribution.github.io/distribution/spec/api/)
to list an image's tags and read its labels, using the anonymous Bearer-token
auth flow common to Docker Hub, GHCR, Quay and most registries that implement
the spec. Only public/anonymous access is supported for now.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import Message
from typing import Any

from releaseprobe._http import next_page_url
from releaseprobe.imageref import ImageReference

_TIMEOUT = 10

MANIFEST_ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    ]
)

MANIFEST_LIST_MEDIA_TYPES = frozenset(
    {
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    }
)

_WWW_AUTHENTICATE_PARAM = re.compile(r'(\w+)="([^"]*)"')


class RegistryError(RuntimeError):
    """Raised when a registry cannot be queried or returns something unexpected."""


@dataclass
class _Response:
    status: int
    headers: Message
    body: bytes


def _get(url: str, headers: dict[str, str]) -> _Response:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:  # noqa: S310
            return _Response(response.status, response.headers, response.read())
    except urllib.error.HTTPError as exc:
        return _Response(exc.code, exc.headers, exc.read())
    except urllib.error.URLError as exc:
        raise RegistryError(f"could not reach {url}: {exc.reason}") from exc


def _auth_params(www_authenticate: str) -> dict[str, str]:
    return dict(_WWW_AUTHENTICATE_PARAM.findall(www_authenticate))


def _fetch_token(params: dict[str, str]) -> str:
    realm = params.get("realm")
    if not realm:
        raise RegistryError("registry auth challenge is missing a realm")
    query = urllib.parse.urlencode({k: v for k, v in params.items() if k != "realm"})
    response = _get(f"{realm}?{query}", {})
    if response.status >= 400:
        raise RegistryError(f"could not authenticate with {realm}: HTTP {response.status}")
    data = json.loads(response.body)
    token = data.get("token") or data.get("access_token")
    if not token:
        raise RegistryError(f"auth server at {realm} did not return a token")
    return str(token)


def _get_with_auth(url: str, *, accept: str | None = None) -> _Response:
    headers = {"Accept": accept} if accept else {}
    response = _get(url, headers)
    if response.status == 401:
        params = _auth_params(response.headers.get("WWW-Authenticate", ""))
        if not params.get("realm"):
            raise RegistryError(f"authentication required for {url} but no realm was offered")
        headers["Authorization"] = f"Bearer {_fetch_token(params)}"
        response = _get(url, headers)
    if response.status >= 400:
        raise RegistryError(f"registry request to {url} failed with HTTP {response.status}")
    return response


def list_tags(ref: ImageReference) -> list[str]:
    """Return every tag published for the image's repository."""
    tags: list[str] = []
    url: str | None = f"https://{ref.registry}/v2/{ref.repository}/tags/list"
    while url:
        response = _get_with_auth(url)
        data = json.loads(response.body)
        tags.extend(data.get("tags") or [])
        url = next_page_url(response.headers.get("Link"), url)
    return tags


def _select_platform_manifest(
    manifests: list[dict[str, Any]], *, os_: str = "linux", arch: str = "amd64"
) -> dict[str, Any]:
    for entry in manifests:
        platform = entry.get("platform", {})
        if platform.get("os") == os_ and platform.get("architecture") == arch:
            return entry
    return manifests[0]


def _fetch_manifest(ref: ImageReference, reference: str) -> dict[str, Any]:
    url = f"https://{ref.registry}/v2/{ref.repository}/manifests/{reference}"
    response = _get_with_auth(url, accept=MANIFEST_ACCEPT)
    manifest: dict[str, Any] = json.loads(response.body)
    media_type = manifest.get("mediaType") or response.headers.get("Content-Type")
    if media_type in MANIFEST_LIST_MEDIA_TYPES:
        platform_manifest = _select_platform_manifest(manifest["manifests"])
        return _fetch_manifest(ref, str(platform_manifest["digest"]))
    return manifest


def fetch_labels(ref: ImageReference) -> dict[str, str]:
    """Fetch an image's labels straight from the registry, no docker daemon required."""
    manifest = _fetch_manifest(ref, ref.tag)
    config_digest = manifest["config"]["digest"]
    url = f"https://{ref.registry}/v2/{ref.repository}/blobs/{config_digest}"
    response = _get_with_auth(url)
    config: dict[str, Any] = json.loads(response.body)
    labels = config.get("config", {}).get("Labels") or {}
    return dict(labels)
