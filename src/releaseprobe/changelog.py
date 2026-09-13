"""Fetch real release notes for the versions between a current and latest tag.

Only GitHub-hosted sources are supported for now (identified from the
`org.opencontainers.image.source` / `org.label-schema.vcs-url` label), read
via the GitHub REST API's releases endpoint:
https://docs.github.com/en/rest/releases/releases
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from packaging.version import Version

from releaseprobe._http import next_page_url
from releaseprobe.versioning import parse as parse_version

_TIMEOUT = 10
_GITHUB_URL_RE = re.compile(r"github\.com[:/]+(?P<owner>[^/]+)/(?P<repo>[^/]+?)/?$")


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


def _list_releases(owner: str, repo: str, *, token: str | None) -> list[dict[str, Any]]:
    headers = _request_headers(token)
    releases: list[dict[str, Any]] = []
    url: str | None = f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=100"
    while url:
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:  # noqa: S310
                releases.extend(json.loads(response.read()))
                url = next_page_url(response.headers.get("Link"), url)
        except urllib.error.HTTPError as exc:
            raise ChangelogError(
                f"could not list releases for {owner}/{repo}: HTTP {exc.code}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ChangelogError(f"could not reach GitHub: {exc.reason}") from exc
    return releases


def fetch_release_notes(
    source_url: str, *, current: str, latest: str, token: str | None = None
) -> list[ReleaseNote]:
    """Return release notes for every version in (current, latest], oldest first.

    Returns an empty list (rather than raising) when the source isn't a
    GitHub repository, since callers should fall back to a plain link in
    that case.

    `token` is used as the GitHub API bearer token if given, overriding the
    `GITHUB_TOKEN` environment variable, to raise GitHub's low unauthenticated
    rate limit when checking images with many releases.
    """
    repo = parse_github_repo(source_url)
    if repo is None:
        return []
    owner, name = repo

    current_version = parse_version(current)
    latest_version = parse_version(latest)

    notes: list[tuple[Version, ReleaseNote]] = []
    for release in _list_releases(owner, name, token=token):
        tag = release.get("tag_name") or ""
        version = parse_version(tag)
        if version is None:
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
    return [note for _, note in notes]
