"""Fetch real release notes for the versions between a current and latest tag.

Only GitHub-hosted sources are supported for now (identified from the
`org.opencontainers.image.source` / `org.label-schema.vcs-url` label), read
via the GitHub REST API's releases endpoint:
https://docs.github.com/en/rest/releases/releases
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from packaging.version import Version

from releaseprobe._http import next_page_url
from releaseprobe.versioning import parse as parse_version
from releaseprobe.versioning import parse_flavored

_TIMEOUT = 10
_GITHUB_URL_RE = re.compile(r"github\.com[:/]+(?P<owner>[^/]+)/(?P<repo>[^/]+?)/?$")

_logger = logging.getLogger(__name__)


class ChangelogError(RuntimeError):
    """Raised when release notes cannot be fetched."""


@dataclass
class ReleaseNote:
    version: str
    name: str | None
    url: str | None
    body: str | None


def parse_github_repo(source_url: str) -> tuple[str, str] | None:
    """Extract (owner, repo) from a GitHub URL, or None if it isn't one."""
    match = _GITHUB_URL_RE.search(source_url.strip())
    if not match:
        return None
    owner = match.group("owner")
    repo = match.group("repo")
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    return owner, repo


def _request_headers(token: str | None) -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    token = token or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _list_releases(
    owner: str,
    repo: str,
    *,
    token: str | None,
    stop_at: Version | None = None,
    logger: logging.Logger,
) -> list[dict[str, Any]]:
    """List a repository's releases, newest first.

    GitHub returns releases newest first, so once a page contains a release
    at or below `stop_at` (the installed version), the older pages can't hold
    anything newer and aren't fetched. Besides saving requests, this keeps
    repositories with more than 1000 releases working: GitHub refuses to
    page past the first 1000 results (HTTP 422).
    """
    headers = _request_headers(token)
    releases: list[dict[str, Any]] = []
    url: str | None = f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=100"
    while url:
        logger.debug("GET %s", url)
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:  # noqa: S310
                page: list[dict[str, Any]] = json.loads(response.read())
                url = next_page_url(response.headers.get("Link"), url)
        except urllib.error.HTTPError as exc:
            if exc.code == 422 and releases:
                logger.warning(
                    "GitHub refused to list more releases for %s/%s (HTTP 422), "
                    "using the %d release(s) fetched so far",
                    owner,
                    repo,
                    len(releases),
                )
                break
            logger.warning("could not list releases for %s/%s: HTTP %d", owner, repo, exc.code)
            raise ChangelogError(
                f"could not list releases for {owner}/{repo}: HTTP {exc.code}"
            ) from exc
        except urllib.error.URLError as exc:
            logger.warning("could not reach GitHub: %s", exc.reason)
            raise ChangelogError(f"could not reach GitHub: {exc.reason}") from exc
        releases.extend(page)
        if url and stop_at is not None and _reaches(page, stop_at):
            logger.debug("reached releases at or below %s, not fetching older pages", stop_at)
            break
    logger.debug("fetched %d release(s) for %s/%s", len(releases), owner, repo)
    return releases


def short_revision(revision: str) -> str:
    """Abbreviate a full git commit hash like GitHub does (7 characters)."""
    return revision[:7] if re.fullmatch(r"[0-9a-f]{8,40}", revision) else revision


def _first_line(message: str) -> str:
    return message.strip().splitlines()[0] if message.strip() else ""


def fetch_commits_between(
    source_url: str,
    *,
    base: str,
    head: str,
    token: str | None = None,
    logger: logging.Logger | None = None,
) -> ReleaseNote | None:
    """Summarize the commits from git revision `base` to `head` as one release note.

    Meant for images built from a branch without version tags, where the
    only thing identifying a build is its `org.opencontainers.image.revision`.
    Uses GitHub's compare API, which lists up to 250 commits. Returns None
    when the source isn't a GitHub repository; raises `ChangelogError` if
    GitHub doesn't know one of the revisions (e.g. one inherited from a base
    image, which isn't part of this repository at all).
    """
    log = logger or _logger
    repo = parse_github_repo(source_url)
    if repo is None:
        log.debug("%s is not a GitHub repository, skipping the commit comparison", source_url)
        return None
    owner, name = repo
    url = f"https://api.github.com/repos/{owner}/{name}/compare/{base}...{head}"
    log.info("comparing %s/%s revisions %s...%s", owner, name, base, head)
    log.debug("GET %s", url)
    request = urllib.request.Request(url, headers=_request_headers(token))
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:  # noqa: S310
            data: dict[str, Any] = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        log.debug("could not compare %s...%s in %s/%s: HTTP %d", base, head, owner, name, exc.code)
        raise ChangelogError(
            f"could not compare {base}...{head} in {owner}/{name}: HTTP {exc.code}"
        ) from exc
    except urllib.error.URLError as exc:
        log.warning("could not reach GitHub: %s", exc.reason)
        raise ChangelogError(f"could not reach GitHub: {exc.reason}") from exc

    commits: list[dict[str, Any]] = data.get("commits") or []
    total = int(data.get("total_commits") or len(commits))
    status = data.get("status")
    log.debug("%s...%s is %s by %d commit(s)", base, head, status, total)
    lines = []
    # GitHub lists commits oldest first; show the newest first like the releases.
    for commit in reversed(commits):
        sha = str(commit.get("sha") or "")
        message = _first_line((commit.get("commit") or {}).get("message") or "")
        link = commit.get("html_url")
        ref = f"[`{short_revision(sha)}`]({link})" if link else f"`{short_revision(sha)}`"
        lines.append(f"- {ref} {message}".rstrip())
    if total > len(commits):
        lines.append(f"- … and {total - len(commits)} more")
    if status == "behind":
        body = "The new image was built from an older revision than the installed one."
    elif status == "diverged":
        body = "The new image was built from a diverged revision.\n\n" + "\n".join(lines)
    else:
        body = "\n".join(lines)
    return ReleaseNote(
        version=short_revision(head),
        name=f"{total} new commit{'' if total == 1 else 's'}",
        url=data.get("html_url") or f"https://github.com/{owner}/{name}/compare/{base}...{head}",
        body=body or None,
    )


def _reaches(page: list[dict[str, Any]], stop_at: Version) -> bool:
    """True if any release on the page is at or below `stop_at`."""
    for release in page:
        version = parse_version(release.get("tag_name") or "")
        if version is not None and version <= stop_at:
            return True
    return False


def _version_of(tag: str) -> Version | None:
    parsed = parse_flavored(tag)
    return parsed[0] if parsed else None


def fetch_release_notes(
    source_url: str,
    *,
    current: str,
    latest: str,
    token: str | None = None,
    logger: logging.Logger | None = None,
) -> list[ReleaseNote]:
    """Return release notes for every version in (current, latest], oldest first.

    Returns an empty list (rather than raising) when the source isn't a
    GitHub repository, since callers should fall back to a plain link in
    that case.

    `token` is used as the GitHub API bearer token if given, overriding the
    `GITHUB_TOKEN` environment variable, to raise GitHub's low unauthenticated
    rate limit when checking images with many releases.
    """
    log = logger or _logger
    repo = parse_github_repo(source_url)
    if repo is None:
        log.debug("%s is not a GitHub repository, skipping release notes", source_url)
        return []
    owner, name = repo
    log.info("fetching release notes for %s/%s between %s and %s", owner, name, current, latest)

    # Image tags may carry a flavor (`1.2.3-alpine`), GitHub releases don't.
    current_version = _version_of(current)
    latest_version = _version_of(latest)

    notes: list[tuple[Version, ReleaseNote]] = []
    releases = _list_releases(owner, name, token=token, stop_at=current_version, logger=log)
    for release in releases:
        tag = release.get("tag_name") or ""
        version = parse_version(tag)
        if version is None:
            log.debug("ignoring release %r: tag does not look like a version", tag)
            continue
        if current_version is not None and version <= current_version:
            continue
        if latest_version is not None and version > latest_version:
            continue
        notes.append(
            (
                version,
                ReleaseNote(
                    version=tag,
                    name=release.get("name"),
                    url=release.get("html_url"),
                    body=release.get("body"),
                ),
            )
        )

    notes.sort(key=lambda entry: entry[0])
    log.debug("found %d release note(s) in range for %s/%s", len(notes), owner, name)
    return [note for _, note in notes]
