"""Version parsing and comparison for image tags.

Tags are compared as PEP 440 versions (which also covers plain semver-style
strings like `1.2.3` or `v1.2.3`). Tags that don't look like a dotted
release version to begin with - `latest`, `stable`, digests, attestation
tags like `sha256-abcd.sig`, bare build numbers, flavor-suffixed tags like
`1.2.3-alpine` - are ignored rather than compared: PEP 440 alone is too
permissive (it happily accepts a bare integer like a Docker Hub build
number as a "version"), so we first require at least a `X.Y` shape.
Pre-releases are never suggested as the "latest" version.
"""

from __future__ import annotations

import re

from packaging.version import InvalidVersion, Version

_DOTTED_VERSION = re.compile(r"^v?\d+(\.\d+){1,3}")


def parse(tag: str) -> Version | None:
    if not _DOTTED_VERSION.match(tag):
        return None
    try:
        return Version(tag)
    except InvalidVersion:
        return None


def find_latest(tags: list[str], current: str) -> str | None:
    """Return the highest version tag that is newer than `current`, if any."""
    current_version = parse(current)
    if current_version is None:
        return None

    candidates = [
        (version, tag)
        for tag in tags
        if (version := parse(tag)) is not None and not version.is_prerelease
    ]
    if not candidates:
        return None

    latest_version, latest_tag = max(candidates)
    return latest_tag if latest_version > current_version else None
