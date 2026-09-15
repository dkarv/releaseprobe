"""Derive release metadata from a container image's labels.

The labels themselves are expected to already have been collected by the
caller (e.g. via `docker inspect`, `skopeo inspect`, `crane config`, the
registry HTTP API, or a Kubernetes pod spec) - this module only interprets
them, following the same label conventions as Renovate's docker datasource:
https://docs.renovatebot.com/modules/datasource/docker/
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field

from releaseprobe import labels as label_keys

_logger = logging.getLogger(__name__)


def _first(labels: Mapping[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = labels.get(key)
        if value:
            return value
    return None


@dataclass
class ReleaseInfo:
    version: str | None = None
    source: str | None = None
    url: str | None = None
    documentation: str | None = None
    revision: str | None = None
    raw_labels: dict[str, str] = field(default_factory=dict)

    def release_notes_url(self) -> str | None:
        """Best-effort guess at a release notes / changelog URL.

        Prefers the VCS source repository (as Renovate does when deriving a
        changelog), then falls back to the generic project URL and finally
        the documentation URL.
        """
        return self.source or self.url or self.documentation


def probe_labels(
    labels: Mapping[str, str], logger: logging.Logger | None = None
) -> ReleaseInfo:
    """Extract release metadata from a mapping of container image labels."""
    log = logger or _logger
    info = ReleaseInfo(
        version=_first(labels, label_keys.VERSION_KEYS),
        source=_first(labels, label_keys.SOURCE_KEYS),
        url=labels.get(label_keys.OCI_URL),
        documentation=labels.get(label_keys.OCI_DOCUMENTATION),
        revision=_first(labels, label_keys.REVISION_KEYS),
        raw_labels=dict(labels),
    )
    log.debug(
        "probed %d label(s): version=%s source=%s", len(labels), info.version, info.source
    )
    if info.version is None:
        log.debug("no version label found among %s", label_keys.VERSION_KEYS)
    return info
