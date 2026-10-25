"""Minimal Docker Registry HTTP API v2 client.

Implements just enough of the spec (https://distribution.github.io/distribution/spec/api/)
to list an image's tags and read its labels and build provenance, using the anonymous Bearer-token
auth flow common to Docker Hub, GHCR, Quay and most registries that implement
the spec. Only public/anonymous access is supported for now.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import Message
from typing import Any

from releaseprobe._http import next_page_url
from releaseprobe.imageref import ImageReference

_TIMEOUT = 10
# Delays (in seconds) before each retry of a request that failed at the
# network level, e.g. a TLS handshake timeout. Registries like GHCR
# occasionally drop connections when a repository with thousands of tags is
# paged through, and a single dropped request shouldn't fail the whole check.
_RETRY_DELAYS = (1.0, 2.0)

_logger = logging.getLogger(__name__)

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

_ATTESTATION_MANIFEST = "attestation-manifest"
_PROVENANCE_PREDICATE_PREFIX = "https://slsa.dev/provenance/"

# Where BuildKit records the build's git repository in its provenance
# attestation: SLSA provenance v1, then v0.2.
_PROVENANCE_VCS_SOURCE_PATHS = (
    ("runDetails", "metadata", "buildkit_metadata", "vcs", "source"),
    ("metadata", "https://mobyproject.org/buildkit@v1#metadata", "vcs", "source"),
)

_WWW_AUTHENTICATE_PARAM = re.compile(r'(\w+)="([^"]*)"')


class RegistryError(RuntimeError):
    """Raised when a registry cannot be queried or returns something unexpected."""


@dataclass
class _Response:
    status: int
    headers: Message
    body: bytes


def _get(
    url: str, headers: dict[str, str], logger: logging.Logger, *, method: str = "GET"
) -> _Response:
    request = urllib.request.Request(url, headers=headers, method=method)
    for attempt, delay in enumerate((*_RETRY_DELAYS, None)):
        logger.debug("%s %s", method, url)
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:  # noqa: S310
                return _Response(response.status, response.headers, response.read())
        except urllib.error.HTTPError as exc:
            return _Response(exc.code, exc.headers, exc.read())
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            if delay is None:
                logger.warning(
                    "could not reach %s after %d attempt(s): %s", url, attempt + 1, reason
                )
                raise RegistryError(f"could not reach {url}: {reason}") from exc
            logger.debug("could not reach %s (%s), retrying in %.0fs", url, reason, delay)
            time.sleep(delay)
    raise AssertionError("unreachable")


def _auth_params(www_authenticate: str) -> dict[str, str]:
    return dict(_WWW_AUTHENTICATE_PARAM.findall(www_authenticate))


def _fetch_token(params: dict[str, str], logger: logging.Logger) -> str:
    realm = params.get("realm")
    if not realm:
        raise RegistryError("registry auth challenge is missing a realm")
    logger.debug("fetching auth token from %s", realm)
    query = urllib.parse.urlencode({k: v for k, v in params.items() if k != "realm"})
    response = _get(f"{realm}?{query}", {}, logger)
    if response.status >= 400:
        raise RegistryError(f"could not authenticate with {realm}: HTTP {response.status}")
    data = json.loads(response.body)
    token = data.get("token") or data.get("access_token")
    if not token:
        raise RegistryError(f"auth server at {realm} did not return a token")
    return str(token)


def _get_with_auth(
    url: str,
    *,
    accept: str | None = None,
    auth: dict[str, str] | None = None,
    method: str = "GET",
    logger: logging.Logger | None = None,
) -> _Response:
    """GET `url`, answering a Bearer auth challenge if the registry sends one.

    `auth` caches the `Authorization` header between calls: pass the same
    dict to every request of a multi-request operation (e.g. paging through
    tags) so the token is fetched once instead of once per request.
    """
    log = logger or _logger
    auth = {} if auth is None else auth
    headers = {"Accept": accept} if accept else {}
    response = _get(url, {**headers, **auth}, log, method=method)
    if response.status == 401:
        log.debug("%s requires authentication, fetching a bearer token", url)
        params = _auth_params(response.headers.get("WWW-Authenticate", ""))
        if not params.get("realm"):
            raise RegistryError(f"authentication required for {url} but no realm was offered")
        auth["Authorization"] = f"Bearer {_fetch_token(params, log)}"
        response = _get(url, {**headers, **auth}, log, method=method)
    if response.status >= 400:
        log.warning("registry request to %s failed with HTTP %d", url, response.status)
        raise RegistryError(f"registry request to {url} failed with HTTP {response.status}")
    return response


def list_tags(ref: ImageReference, logger: logging.Logger | None = None) -> list[str]:
    """Return every tag published for the image's repository."""
    log = logger or _logger
    log.info("listing tags for %s/%s", ref.registry, ref.repository)
    tags: list[str] = []
    auth: dict[str, str] = {}
    url: str | None = f"https://{ref.registry}/v2/{ref.repository}/tags/list"
    while url:
        response = _get_with_auth(url, auth=auth, logger=log)
        data = json.loads(response.body)
        tags.extend(data.get("tags") or [])
        url = next_page_url(response.headers.get("Link"), url)
        if url:
            log.debug("following pagination link to %s", url)
    log.debug("found %d tag(s) for %s/%s", len(tags), ref.registry, ref.repository)
    return tags


def _select_platform_manifest(
    manifests: list[dict[str, Any]],
    *,
    os_: str = "linux",
    arch: str = "amd64",
    logger: logging.Logger,
) -> dict[str, Any]:
    for entry in manifests:
        platform = entry.get("platform", {})
        if platform.get("os") == os_ and platform.get("architecture") == arch:
            return entry
    logger.warning(
        "no manifest found for platform %s/%s, falling back to the first entry", os_, arch
    )
    return manifests[0]


def _fetch_manifest(
    ref: ImageReference, reference: str, auth: dict[str, str], logger: logging.Logger
) -> tuple[dict[str, Any], dict[str, str]]:
    """Fetch the platform-specific manifest and the annotations along the way.

    Annotations of a manifest list are overridden by those of the
    platform-specific manifest it resolves to.
    """
    url = f"https://{ref.registry}/v2/{ref.repository}/manifests/{reference}"
    response = _get_with_auth(url, accept=MANIFEST_ACCEPT, auth=auth, logger=logger)
    manifest: dict[str, Any] = json.loads(response.body)
    annotations = dict(manifest.get("annotations") or {})
    media_type = manifest.get("mediaType") or response.headers.get("Content-Type")
    if media_type in MANIFEST_LIST_MEDIA_TYPES:
        logger.debug(
            "%s is a manifest list (%s), resolving platform-specific manifest", url, media_type
        )
        platform_manifest = _select_platform_manifest(manifest["manifests"], logger=logger)
        manifest, platform_annotations = _fetch_manifest(
            ref, str(platform_manifest["digest"]), auth, logger
        )
        annotations.update(platform_annotations)
    return manifest, annotations


def fetch_labels(ref: ImageReference, logger: logging.Logger | None = None) -> dict[str, str]:
    """Fetch an image's labels straight from the registry, no docker daemon required.

    The image config's labels are merged with the annotations of its manifest
    (list), which take precedence: a label can be inherited from the base
    image (e.g. `org.opencontainers.image.version: 24.04` from `ubuntu:24.04`),
    while annotations are always set for the image itself - some images, like
    `netbirdio/netbird`, only publish their real version and source there.
    """
    log = logger or _logger
    log.info("fetching labels for %s", ref)
    auth: dict[str, str] = {}
    manifest, annotations = _fetch_manifest(ref, ref.tag, auth, log)
    config_digest = manifest["config"]["digest"]
    url = f"https://{ref.registry}/v2/{ref.repository}/blobs/{config_digest}"
    response = _get_with_auth(url, auth=auth, logger=log)
    config: dict[str, Any] = json.loads(response.body)
    labels = config.get("config", {}).get("Labels") or {}
    log.debug("fetched %d label(s) and %d annotation(s) for %s", len(labels), len(annotations), ref)
    return {**labels, **annotations}


def fetch_provenance_source(
    ref: ImageReference, logger: logging.Logger | None = None
) -> str | None:
    """Return the git repository an image was built from, per its build provenance.

    BuildKit (`docker buildx build`) attaches a SLSA provenance attestation
    to multi-platform images by default, recording the repository of a git
    build context. This costs three more requests than reading labels, so it
    is meant as a fallback for images that don't label their source. Returns
    None if the image has no such attestation or it doesn't name a source.
    """
    log = logger or _logger
    auth: dict[str, str] = {}
    base = f"https://{ref.registry}/v2/{ref.repository}"
    response = _get_with_auth(
        f"{base}/manifests/{ref.tag}", accept=MANIFEST_ACCEPT, auth=auth, logger=log
    )
    index: dict[str, Any] = json.loads(response.body)
    manifests: list[dict[str, Any]] = index.get("manifests") or []
    platform_digest = (
        _select_platform_manifest(manifests, logger=log).get("digest") if manifests else None
    )
    attestation = next(
        (
            entry
            for entry in manifests
            if (entry.get("annotations") or {}).get("vnd.docker.reference.type")
            == _ATTESTATION_MANIFEST
            and entry["annotations"].get("vnd.docker.reference.digest") == platform_digest
        ),
        None,
    )
    if attestation is None:
        log.debug("%s has no attestation manifest", ref)
        return None

    response = _get_with_auth(
        f"{base}/manifests/{attestation['digest']}", accept=MANIFEST_ACCEPT, auth=auth, logger=log
    )
    for layer in json.loads(response.body).get("layers") or []:
        predicate_type = (layer.get("annotations") or {}).get("in-toto.io/predicate-type", "")
        if not predicate_type.startswith(_PROVENANCE_PREDICATE_PREFIX):
            continue
        response = _get_with_auth(f"{base}/blobs/{layer['digest']}", auth=auth, logger=log)
        source = _provenance_vcs_source(json.loads(response.body).get("predicate") or {})
        if source:
            log.debug("provenance of %s names source %s", ref, source)
            return source
    log.debug("provenance of %s names no source", ref)
    return None


def _provenance_vcs_source(predicate: dict[str, Any]) -> str | None:
    for path in _PROVENANCE_VCS_SOURCE_PATHS:
        value: Any = predicate
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if isinstance(value, str) and value:
            return value
    return None


def fetch_digest(
    ref: ImageReference,
    *,
    auth: dict[str, str] | None = None,
    logger: logging.Logger | None = None,
) -> str | None:
    """Return the manifest digest the image's tag currently points to.

    This is the digest `docker pull` records in the image's `RepoDigests`
    (for multi-platform images, the digest of the manifest list, not of a
    platform-specific manifest). A HEAD request is used, which unlike a GET
    doesn't count towards Docker Hub's pull rate limit. Returns None if the
    registry doesn't send a `Docker-Content-Digest` header.

    `auth` caches the `Authorization` header like for `_get_with_auth`: pass
    the same dict when resolving several tags of one repository.
    """
    log = logger or _logger
    url = f"https://{ref.registry}/v2/{ref.repository}/manifests/{ref.tag}"
    response = _get_with_auth(url, accept=MANIFEST_ACCEPT, auth=auth, method="HEAD", logger=log)
    digest = response.headers.get("Docker-Content-Digest")
    log.debug("%s points to %s", ref, digest)
    return digest
