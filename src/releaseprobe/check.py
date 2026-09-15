"""End-to-end 'is there a newer version, and what changed' workflow."""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field

from releaseprobe.changelog import ReleaseNote, fetch_release_notes
from releaseprobe.imageref import parse as parse_image_reference
from releaseprobe.probe import ReleaseInfo, probe_labels
from releaseprobe.registry import fetch_labels, list_tags
from releaseprobe.versioning import find_latest
from releaseprobe.versioning import parse as parse_version

_logger = logging.getLogger(__name__)


class UnknownVersionError(RuntimeError):
    """Raised when the current version of an image cannot be determined."""


@dataclass
class UpdateCheck:
    image: str
    current_version: str
    latest_version: str | None
    current_info: ReleaseInfo
    latest_info: ReleaseInfo | None = None
    release_notes: list[ReleaseNote] = field(default_factory=list)

    @property
    def has_update(self) -> bool:
        return self.latest_version is not None


def _resolve_current_version(
    tag: str, labeled_version: str | None, *, logger: logging.Logger
) -> str:
    """Determine the version to compare against the registry's other tags.

    A concrete version tag (e.g. `1.2.3`) is authoritative on its own. A
    floating tag (e.g. `latest`, `stable`) carries no version itself, so the
    version baked into the image's own labels - which reflects whatever is
    actually installed - is used instead.
    """
    if parse_version(tag) is not None:
        return tag
    if labeled_version:
        logger.debug("tag %r is floating, using labeled version %r instead", tag, labeled_version)
        return labeled_version
    logger.warning("tag %r is floating and the image has no version label", tag)
    raise UnknownVersionError(
        f"{tag!r} is not a version tag and the image has no version label; "
        "pass the currently installed image's labels (current_labels) to "
        "determine the current version"
    )


def check_for_update(
    image: str,
    current_labels: Mapping[str, str] | None = None,
    github_token: str | None = None,
    logger: logging.Logger | None = None,
) -> UpdateCheck:
    """Check whether a newer tag exists for `image` and gather release notes for it.

    Lists the repository's tags to find the highest version newer than the
    current one, and - if the image's source label points at GitHub -
    fetches the real release notes for every version in between.

    For a pinned version tag (e.g. `1.2.3`), the current version is the tag
    itself and its labels are fetched straight from the registry. For a
    floating tag (e.g. `latest`, `stable`), the tag carries no version, so
    `current_labels` - the labels of the image actually installed, e.g. from
    `docker inspect --format '{{json .Config.Labels}}'` - must be passed to
    determine which version is currently running.

    `github_token` is used as the GitHub API bearer token if given,
    overriding the `GITHUB_TOKEN` environment variable, to raise GitHub's low
    unauthenticated rate limit when checking images with many releases.

    `logger` receives progress and diagnostic messages at various levels
    (DEBUG for details like requested URLs, INFO for the overall outcome,
    WARNING for recoverable oddities); it defaults to this module's own
    logger (`logging.getLogger("releaseprobe.check")`), which propagates
    to the standard `logging` configuration if not overridden. Pass a
    custom logger to route this into an application's own logging setup,
    e.g. to tag it with extra context when checking many images remotely.
    """
    log = logger or _logger
    log.info("checking %s for updates", image)

    ref = parse_image_reference(image, logger=log)
    current_info = (
        probe_labels(current_labels, logger=log)
        if current_labels is not None
        else probe_labels(fetch_labels(ref, logger=log), logger=log)
    )
    current_version = _resolve_current_version(ref.tag, current_info.version, logger=log)
    log.debug("current version of %s resolved to %s", ref, current_version)

    latest_tag = find_latest(list_tags(ref, logger=log), current_version, logger=log)
    if latest_tag is None:
        log.info("%s is already at the latest version (%s)", ref, current_version)
        return UpdateCheck(
            image=str(ref),
            current_version=current_version,
            latest_version=None,
            current_info=current_info,
        )

    log.info("newer version of %s available: %s -> %s", ref, current_version, latest_tag)
    latest_ref = dataclasses.replace(ref, tag=latest_tag)
    latest_info = probe_labels(fetch_labels(latest_ref, logger=log), logger=log)

    source = latest_info.source or current_info.source
    if source:
        notes = fetch_release_notes(
            source, current=current_version, latest=latest_tag, token=github_token, logger=log
        )
    else:
        log.debug("no source label found for %s, skipping release notes", ref)
        notes = []

    return UpdateCheck(
        image=str(ref),
        current_version=current_version,
        latest_version=latest_tag,
        current_info=current_info,
        latest_info=latest_info,
        release_notes=notes,
    )
