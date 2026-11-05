"""Parsing of container image references into registry/repository/tag."""

from __future__ import annotations

import logging
from dataclasses import dataclass

_logger = logging.getLogger(__name__)

DOCKER_HUB_REGISTRY = "registry-1.docker.io"

# Hostnames people commonly write for Docker Hub that aren't the actual
# registry API host (which is always registry-1.docker.io).
DOCKER_HUB_ALIASES = frozenset({"docker.io", "index.docker.io", DOCKER_HUB_REGISTRY})


class InvalidImageReferenceError(ValueError):
    """Raised when a string cannot be parsed as an image reference with a tag."""


@dataclass(frozen=True)
class ImageReference:
    registry: str
    repository: str
    tag: str

    def __str__(self) -> str:
        return f"{self.registry}/{self.repository}:{self.tag}"

    @property
    def repository_name(self) -> str:
        """The normalized `<registry>/<repository>`, without the tag."""
        return f"{self.registry}/{self.repository}"


def _split_tag(name: str) -> tuple[str, str]:
    """`ghcr.io/org/app:1.2.3` -> (`ghcr.io/org/app`, `1.2.3`); the tag is '' if none."""
    base, sep, tag = name.rpartition(":")
    if not sep or "/" in tag:
        # No ':' in the reference, or the ':' we split on belonged to a
        # registry `host:port`, not a tag.
        return name, ""
    return base, tag


def _split_repository(name: str) -> tuple[str, str]:
    """Split a tagless name into its (normalized) registry and repository."""
    first, sep, rest = name.partition("/")
    if sep and ("." in first or ":" in first or first == "localhost"):
        registry, repository = first, rest
    else:
        registry, repository = DOCKER_HUB_REGISTRY, name

    if registry in DOCKER_HUB_ALIASES:
        # Docker Hub's actual API host is always registry-1.docker.io, and
        # single-segment repository names implicitly live under `library/`,
        # regardless of which Docker Hub hostname the user wrote.
        registry = DOCKER_HUB_REGISTRY
        if "/" not in repository:
            repository = f"library/{repository}"
    return registry, repository


def repository_name(reference: str) -> str:
    """The normalized `<registry>/<repository>` of any image reference.

    Unlike `parse`, this accepts references without a tag or with a digest:
    `nginx`, `docker.io/library/nginx:1.25` and `nginx@sha256:...` all map to
    `registry-1.docker.io/library/nginx`. It is the key `check_for_update`
    uses for its `tags_cache`, so callers can use it to group or serialize
    checks of the same repository.
    """
    name, _ = _split_tag(reference.split("@", 1)[0])
    registry, repository = _split_repository(name)
    return f"{registry}/{repository}"


def normalize_digest(digest: str) -> str:
    """`repo@sha256:abc...` (as in Docker's RepoDigests) -> `sha256:abc...`."""
    return digest.rpartition("@")[2]


def short_digest(digest: str) -> str | None:
    """Abbreviate a digest for display: `repo@sha256:0123456789abcdef...` -> `sha256:0123456789ab`.

    Returns None if `digest` isn't of the form `[repo@]<algorithm>:<hex>`.
    """
    algorithm, sep, value = normalize_digest(digest).partition(":")
    if not (sep and algorithm and value):
        return None
    return f"{algorithm}:{value[:12]}"


def parse(reference: str, logger: logging.Logger | None = None) -> ImageReference:
    """Parse a reference like `ghcr.io/org/app:1.2.3` or `nginx:1.25`.

    The first path segment is treated as a registry host only if it looks
    like one (contains a '.' or ':', or is exactly 'localhost') - the same
    heuristic the Docker CLI uses. Otherwise the image is assumed to live on
    Docker Hub, with single-segment names getting the implicit `library/`
    namespace. A concrete tag is required; digests and untagged references
    are rejected since there is no version to compare against.
    """
    log = logger or _logger
    if "@" in reference:
        log.debug("rejecting %r: digest references are not supported", reference)
        raise InvalidImageReferenceError(
            f"{reference!r}: digest references are not supported, pass a tag instead"
        )

    name, tag = _split_tag(reference)
    if not tag:
        log.debug("rejecting %r: no explicit tag", reference)
        raise InvalidImageReferenceError(
            f"{reference!r} has no explicit tag; a concrete version tag is required"
        )

    registry, repository = _split_repository(name)
    ref = ImageReference(registry=registry, repository=repository, tag=tag)
    log.debug("parsed %r as %s", reference, ref)
    return ref
