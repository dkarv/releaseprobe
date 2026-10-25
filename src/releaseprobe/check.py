"""End-to-end 'is there a newer version, and what changed' workflow."""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from releaseprobe.changelog import ChangelogError, ReleaseNote, fetch_release_notes
from releaseprobe.imageref import ImageReference
from releaseprobe.imageref import parse as parse_image_reference
from releaseprobe.probe import ReleaseInfo, probe_labels
from releaseprobe.registry import (
    RegistryError,
    fetch_digest,
    fetch_labels,
    fetch_provenance_source,
    list_tags,
)
from releaseprobe.versioning import find_latest
from releaseprobe.versioning import parse as parse_version

_logger = logging.getLogger(__name__)

# How many of the newest version tags to resolve when looking for the one an
# installed digest belongs to. Each costs one (HEAD) request; an install
# older than this many releases is reported as an unknown version instead.
_MAX_DIGEST_LOOKUPS = 25


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
    ref: ImageReference,
    tags: list[str],
    labeled_version: str | None,
    digests: Iterable[str],
    *,
    logger: logging.Logger,
) -> str:
    """Determine the version to compare against the registry's other tags.

    A concrete version tag (e.g. `1.2.3`) is authoritative on its own. A
    floating tag (e.g. `latest`, `stable`) carries no version itself, so the
    version baked into the image's own labels - which reflects whatever is
    actually installed - is used instead, as long as the repository actually
    publishes a tag with that version. Images often don't set the version
    label themselves and inherit their base image's instead (e.g. `24.04`
    from `ubuntu:24.04`), which says nothing about the image's own version.

    Failing that, the installed image's manifest digests (its `RepoDigests`)
    are matched against the digests of the newest version tags.
    """
    if parse_version(ref.tag, logger=logger) is not None:
        return ref.tag
    if labeled_version:
        if _is_published(labeled_version, tags, logger=logger):
            logger.debug(
                "tag %r is floating, using labeled version %r instead", ref.tag, labeled_version
            )
            return labeled_version
        logger.warning(
            "ignoring version label %r of %s: no tag of the repository has that version, "
            "so it is likely inherited from the base image",
            labeled_version,
            ref,
        )
    digest_version = _version_from_digests(ref, tags, digests, logger=logger)
    if digest_version is not None:
        return digest_version
    logger.warning("tag %r is floating and the image's version could not be determined", ref.tag)
    raise UnknownVersionError(
        f"{ref.tag!r} is not a version tag and the image has no usable version label; "
        "pass the currently installed image's labels (current_labels) or digests "
        "(current_digests) to determine the current version"
    )


def _is_published(version: str, tags: list[str], *, logger: logging.Logger) -> bool:
    """True if one of the tags has the given version (or the version can't be checked)."""
    parsed = parse_version(version, logger=logger)
    if parsed is None:
        # Not comparable anyway; find_latest() reports that.
        return True
    tag_versions = [v for tag in tags if (v := parse_version(tag, logger=logger)) is not None]
    # Without any version tags there's nothing to check against, nor to update to.
    return not tag_versions or parsed in tag_versions


def _normalize_digest(digest: str) -> str:
    """`repo@sha256:abc` (as in RepoDigests) -> `sha256:abc`."""
    return digest.rpartition("@")[2]


def _version_from_digests(
    ref: ImageReference, tags: list[str], digests: Iterable[str], *, logger: logging.Logger
) -> str | None:
    """Find the newest version tag pointing to one of the installed image's digests."""
    wanted = {_normalize_digest(d) for d in digests if d}
    if not wanted:
        return None
    candidates = sorted(
        (
            (version, tag)
            for tag in tags
            if (version := parse_version(tag, logger=logger)) is not None
            and not version.is_prerelease
        ),
        reverse=True,
    )[:_MAX_DIGEST_LOOKUPS]
    logger.debug("looking for %s among the digests of %d tag(s)", wanted, len(candidates))
    auth: dict[str, str] = {}
    for _, tag in candidates:
        if fetch_digest(dataclasses.replace(ref, tag=tag), auth=auth, logger=logger) in wanted:
            logger.debug("installed image of %s matches tag %r", ref, tag)
            return tag
    logger.debug("installed image of %s matches none of the newest version tags", ref)
    return None


def _provenance_source(ref: ImageReference, *, logger: logging.Logger) -> str | None:
    """The source named by the image's build provenance; failures just mean "unknown"."""
    try:
        return fetch_provenance_source(ref, logger=logger)
    except (RegistryError, ValueError) as exc:
        logger.debug("could not read the build provenance of %s: %s", ref, exc)
        return None


def _guess_source(ref: ImageReference) -> str | None:
    """Guess the GitHub repository of a GHCR image from its name, by convention.

    GHCR packages are published by a GitHub user or organization and usually
    named after the repository they're built from: `ghcr.io/<owner>/<repo>`
    from `github.com/<owner>/<repo>`. That's not guaranteed, so this is only
    used when nothing better is known.
    """
    if ref.registry != "ghcr.io":
        return None
    owner, _, rest = ref.repository.partition("/")
    repo = rest.split("/", 1)[0]
    return f"https://github.com/{owner}/{repo}" if owner and repo else None


def check_for_update(
    image: str,
    current_labels: Mapping[str, str] | None = None,
    github_token: str | None = None,
    logger: logging.Logger | None = None,
    current_digests: Iterable[str] | None = None,
) -> UpdateCheck:
    """Check whether a newer tag exists for `image` and gather release notes for it.

    Lists the repository's tags to find the highest version newer than the
    current one, and - if the image's source label points at GitHub -
    fetches the real release notes for every version in between. Images
    without a source label fall back to the source named by their build
    provenance and, for GHCR images, to the GitHub repository of the same name.

    For a pinned version tag (e.g. `1.2.3`), the current version is the tag
    itself and its labels are fetched straight from the registry. For a
    floating tag (e.g. `latest`, `stable`), the tag carries no version, so
    `current_labels` - the labels of the image actually installed, e.g. from
    `docker inspect --format '{{json .Config.Labels}}'` - must be passed to
    determine which version is currently running. If those labels carry no
    usable version, `current_digests` - the installed image's manifest
    digests, e.g. from `docker image inspect --format '{{json .RepoDigests}}'`,
    either as `repo@sha256:...` or bare `sha256:...` - are matched against the
    registry's version tags instead.

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
    tags = list_tags(ref, logger=log)
    current_version = _resolve_current_version(
        ref, tags, current_info.version, current_digests or (), logger=log
    )
    log.debug("current version of %s resolved to %s", ref, current_version)

    latest_tag = find_latest(tags, current_version, logger=log)
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
    if not source:
        source = _provenance_source(latest_ref, logger=log)
        if source:
            latest_info = dataclasses.replace(latest_info, source=source)
    guessed_source = None if source else _guess_source(ref)

    if source:
        notes = fetch_release_notes(
            source, current=current_version, latest=latest_tag, token=github_token, logger=log
        )
    elif guessed_source:
        log.debug("no source found for %s, trying %s", ref, guessed_source)
        try:
            notes = fetch_release_notes(
                guessed_source,
                current=current_version,
                latest=latest_tag,
                token=github_token,
                logger=log,
            )
        except ChangelogError as exc:
            # Only a guess, so not finding release notes there isn't an error.
            log.debug("no release notes at guessed source %s: %s", guessed_source, exc)
            notes = []
    else:
        log.debug("no source found for %s, skipping release notes", ref)
        notes = []

    return UpdateCheck(
        image=str(ref),
        current_version=current_version,
        latest_version=latest_tag,
        current_info=current_info,
        latest_info=latest_info,
        release_notes=notes,
    )
