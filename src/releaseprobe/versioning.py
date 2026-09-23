"""Version parsing and comparison for image tags.

Tags are compared as PEP 440 versions (which also covers plain semver-style
strings like `1.2.3` or `v1.2.3`). Tags that don't look like a dotted
release version to begin with - `latest`, `stable`, digests, attestation
tags like `sha256-abcd.sig`, bare build numbers, flavor-suffixed tags like
`1.2.3-alpine` - are ignored rather than compared: PEP 440 alone is too
permissive (it happily accepts a bare integer like a Docker Hub build
number as a "version"), so we first require at least a `X.Y` shape.
Pre-releases are never suggested as the "latest" version.

Two common non-PEP 440 decorations are understood and normalized first:
a release channel with date as used by Duplicati (`2.4.0.0_stable_2026-09-03`,
where non-stable channels like `beta` or `canary` count as pre-releases) and
the linuxserver.io build suffix (`-ls309`), which becomes a local version so
that a newer build of the same upstream version sorts higher.
"""

from __future__ import annotations

import logging
import re

from packaging.version import InvalidVersion, Version

_DOTTED_VERSION = re.compile(r"^v?\d+(\.\d+){1,3}")

# e.g. `v2.4.0.0_stable_2026-09-03-ls309`, `2.4.0.0_canary_2026-08-01`, `1.2.3-ls45`
_DECORATED_VERSION = re.compile(
    r"^(?P<core>v?\d+(?:\.\d+){1,3})"
    r"(?:_(?P<channel>[a-z]+)_\d{4}-\d{2}-\d{2})?"
    r"(?:-ls(?P<build>\d+))?$"
)

# Release channels and the PEP 440 pre-release marker they map to ("" = final release).
_CHANNELS = {
    "stable": "",
    "beta": "b0",
    "canary": "a0",
    "experimental": "a0",
    "nightly": "dev0",
}

_logger = logging.getLogger(__name__)


def parse(tag: str, logger: logging.Logger | None = None) -> Version | None:
    log = logger or _logger
    if not _DOTTED_VERSION.match(tag):
        log.debug("ignoring tag %r: does not look like a dotted version", tag)
        return None
    normalized = _normalize(tag)
    if normalized is None:
        log.debug("ignoring tag %r: unknown release channel", tag)
        return None
    try:
        return Version(normalized)
    except InvalidVersion:
        log.debug("ignoring tag %r: not a valid version", tag)
        return None


def _normalize(tag: str) -> str | None:
    """Rewrite channel/build decorations into PEP 440 syntax; other tags pass through."""
    match = _DECORATED_VERSION.match(tag)
    if match is None or (match["channel"] is None and match["build"] is None):
        return tag
    marker = _CHANNELS.get(match["channel"] or "stable")
    if marker is None:
        return None
    local = f"+ls{match['build']}" if match["build"] else ""
    return f"{match['core']}{marker}{local}"


def find_latest(
    tags: list[str], current: str, logger: logging.Logger | None = None
) -> str | None:
    """Return the highest version tag that is newer than `current`, if any."""
    log = logger or _logger
    current_version = parse(current, logger=log)
    if current_version is None:
        log.warning("current version %r does not look like a version, cannot compare", current)
        return None

    candidates = [
        (version, tag)
        for tag in tags
        if (version := parse(tag, logger=log)) is not None and not version.is_prerelease
    ]
    log.debug(
        "%d of %d tag(s) look like comparable, non-prerelease versions", len(candidates), len(tags)
    )
    if not candidates:
        return None

    latest_version, latest_tag = max(candidates)
    if latest_version > current_version:
        log.debug(
            "latest version %s (%r) is newer than current %s",
            latest_version,
            latest_tag,
            current_version,
        )
        return latest_tag
    log.debug(
        "latest version found (%s) is not newer than current (%s)", latest_version, current_version
    )
    return None
