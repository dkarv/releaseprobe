"""End-to-end 'is there a newer version, and what changed' workflow."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field

from releaseprobe.changelog import ReleaseNote, fetch_release_notes
from releaseprobe.imageref import parse as parse_image_reference
from releaseprobe.probe import ReleaseInfo, probe_labels
from releaseprobe.registry import fetch_labels, list_tags
from releaseprobe.versioning import find_latest
from releaseprobe.versioning import parse as parse_version


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


def _resolve_current_version(tag: str, labeled_version: str | None) -> str:
    """Determine the version to compare against the registry's other tags.

    A concrete version tag (e.g. `1.2.3`) is authoritative on its own. A
    floating tag (e.g. `latest`, `stable`) carries no version itself, so the
    version baked into the image's own labels - which reflects whatever is
    actually installed - is used instead.
    """
    if parse_version(tag) is not None:
        return tag
    if labeled_version:
        return labeled_version
    raise UnknownVersionError(
        f"{tag!r} is not a version tag and the image has no version label; "
        "pass the currently installed image's labels (current_labels) to "
        "determine the current version"
    )


def check_for_update(
    image: str,
    current_labels: Mapping[str, str] | None = None,
    github_token: str | None = None,
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
    """
    ref = parse_image_reference(image)
    current_info = (
        probe_labels(current_labels)
        if current_labels is not None
        else probe_labels(fetch_labels(ref))
    )
    current_version = _resolve_current_version(ref.tag, current_info.version)

    latest_tag = find_latest(list_tags(ref), current_version)
    if latest_tag is None:
        return UpdateCheck(
            image=str(ref),
            current_version=current_version,
            latest_version=None,
            current_info=current_info,
        )

    latest_ref = dataclasses.replace(ref, tag=latest_tag)
    latest_info = probe_labels(fetch_labels(latest_ref))

    source = latest_info.source or current_info.source
    notes = (
        fetch_release_notes(
            source, current=current_version, latest=latest_tag, token=github_token
        )
        if source
        else []
    )

    return UpdateCheck(
        image=str(ref),
        current_version=current_version,
        latest_version=latest_tag,
        current_info=current_info,
        latest_info=latest_info,
        release_notes=notes,
    )
