from __future__ import annotations

import logging
from collections.abc import Iterator
from unittest.mock import patch

import pytest

from releaseprobe.changelog import ChangelogError, ReleaseNote
from releaseprobe.check import UnknownVersionError, check_for_update
from releaseprobe.registry import RegistryError


@pytest.fixture(autouse=True)
def _no_provenance() -> Iterator[None]:
    """Keep the provenance fallback offline; tests that need it patch it themselves."""
    with patch("releaseprobe.check.fetch_provenance_source", return_value=None):
        yield


def test_check_for_update_reports_no_update() -> None:
    with (
        patch("releaseprobe.check.fetch_labels", return_value={}),
        patch("releaseprobe.check.list_tags", return_value=["1.0.0"]),
    ):
        result = check_for_update("ghcr.io/org/app:1.0.0")

    assert not result.has_update
    assert result.latest_version is None
    assert result.current_version == "1.0.0"


def test_check_for_update_finds_newer_version_and_notes() -> None:
    current_labels = {"org.opencontainers.image.source": "https://github.com/org/app"}
    latest_labels = {"org.opencontainers.image.source": "https://github.com/org/app"}
    notes = [ReleaseNote(version="1.2.0", name="1.2.0", url="u", body="what changed")]

    def fake_fetch_labels(ref: object, **kwargs: object) -> dict[str, str]:
        return latest_labels if getattr(ref, "tag", None) == "1.2.0" else current_labels

    with (
        patch("releaseprobe.check.fetch_labels", side_effect=fake_fetch_labels),
        patch("releaseprobe.check.list_tags", return_value=["1.0.0", "1.2.0"]),
        patch("releaseprobe.check.fetch_release_notes", return_value=notes) as mock_notes,
    ):
        result = check_for_update("ghcr.io/org/app:1.0.0")

    assert result.has_update
    assert result.latest_version == "1.2.0"
    assert result.release_notes == notes
    mock_notes.assert_called_once()
    call_args, call_kwargs = mock_notes.call_args
    assert call_args == ("https://github.com/org/app",)
    assert call_kwargs["current"] == "1.0.0"
    assert call_kwargs["latest"] == "1.2.0"
    assert call_kwargs["token"] is None
    assert isinstance(call_kwargs["logger"], logging.Logger)


def test_check_for_update_skips_changelog_without_source() -> None:
    with (
        patch("releaseprobe.check.fetch_labels", return_value={}),
        patch("releaseprobe.check.list_tags", return_value=["1.0.0", "1.2.0"]),
        patch("releaseprobe.check.fetch_provenance_source", return_value=None) as mock_prov,
        patch("releaseprobe.check.fetch_release_notes") as mock_notes,
    ):
        result = check_for_update("docker.io/org/app:1.0.0")

    assert result.has_update
    assert result.release_notes == []
    assert mock_prov.call_args.args[0].tag == "1.2.0"
    mock_notes.assert_not_called()


def test_check_for_update_skips_provenance_when_labeled() -> None:
    labels = {"org.opencontainers.image.source": "https://github.com/org/app"}
    with (
        patch("releaseprobe.check.fetch_labels", return_value=labels),
        patch("releaseprobe.check.list_tags", return_value=["1.0.0", "1.2.0"]),
        patch("releaseprobe.check.fetch_provenance_source") as mock_prov,
        patch("releaseprobe.check.fetch_release_notes", return_value=[]),
    ):
        check_for_update("ghcr.io/org/app:1.0.0")

    mock_prov.assert_not_called()


def test_check_for_update_uses_provenance_source() -> None:
    notes = [ReleaseNote(version="1.2.0", name="1.2.0", url="u", body="b")]
    with (
        patch("releaseprobe.check.fetch_labels", return_value={}),
        patch("releaseprobe.check.list_tags", return_value=["1.0.0", "1.2.0"]),
        patch(
            "releaseprobe.check.fetch_provenance_source",
            return_value="https://github.com/org/real-repo",
        ),
        patch("releaseprobe.check.fetch_release_notes", return_value=notes) as mock_notes,
    ):
        result = check_for_update("ghcr.io/org/app:1.0.0")

    assert result.release_notes == notes
    assert result.latest_info is not None
    assert result.latest_info.source == "https://github.com/org/real-repo"
    assert mock_notes.call_args.args == ("https://github.com/org/real-repo",)


def test_check_for_update_ignores_unreadable_provenance() -> None:
    with (
        patch("releaseprobe.check.fetch_labels", return_value={}),
        patch("releaseprobe.check.list_tags", return_value=["1.0.0", "1.2.0"]),
        patch("releaseprobe.check.fetch_provenance_source", side_effect=RegistryError("boom")),
        patch("releaseprobe.check.fetch_release_notes") as mock_notes,
    ):
        result = check_for_update("docker.io/org/app:1.0.0")

    assert result.has_update
    mock_notes.assert_not_called()


def test_check_for_update_guesses_ghcr_source() -> None:
    notes = [ReleaseNote(version="1.2.0", name="1.2.0", url="u", body="b")]
    with (
        patch("releaseprobe.check.fetch_labels", return_value={}),
        patch("releaseprobe.check.list_tags", return_value=["1.0.0", "1.2.0"]),
        patch("releaseprobe.check.fetch_provenance_source", return_value=None),
        patch("releaseprobe.check.fetch_release_notes", return_value=notes) as mock_notes,
    ):
        result = check_for_update("ghcr.io/org/app/sub:1.0.0")

    assert result.release_notes == notes
    assert mock_notes.call_args.args == ("https://github.com/org/app",)
    # Only a guess, so it isn't reported as the image's source.
    assert result.latest_info is not None
    assert result.latest_info.source is None


def test_check_for_update_tolerates_wrong_ghcr_guess() -> None:
    with (
        patch("releaseprobe.check.fetch_labels", return_value={}),
        patch("releaseprobe.check.list_tags", return_value=["1.0.0", "1.2.0"]),
        patch("releaseprobe.check.fetch_provenance_source", return_value=None),
        patch(
            "releaseprobe.check.fetch_release_notes",
            side_effect=ChangelogError("could not list releases for org/app: HTTP 404"),
        ),
    ):
        result = check_for_update("ghcr.io/org/app:1.0.0")

    assert result.has_update
    assert result.release_notes == []


def test_check_for_update_uses_current_labels_for_floating_tag() -> None:
    current_labels = {"org.opencontainers.image.version": "1.0.0"}

    with (
        patch("releaseprobe.check.fetch_labels", return_value={}) as mock_fetch,
        patch("releaseprobe.check.list_tags", return_value=["1.0.0", "1.2.0"]),
        patch("releaseprobe.check.fetch_release_notes", return_value=[]),
    ):
        result = check_for_update("ghcr.io/org/app:latest", current_labels)

    assert result.current_version == "1.0.0"
    assert result.has_update
    assert result.latest_version == "1.2.0"
    # Current labels were supplied, so the registry is only hit for the latest tag.
    mock_fetch.assert_called_once()


def test_check_for_update_passes_github_token() -> None:
    labels = {"org.opencontainers.image.source": "https://github.com/org/app"}

    with (
        patch("releaseprobe.check.fetch_labels", return_value=labels),
        patch("releaseprobe.check.list_tags", return_value=["1.0.0", "1.2.0"]),
        patch("releaseprobe.check.fetch_release_notes", return_value=[]) as mock_notes,
    ):
        check_for_update("ghcr.io/org/app:1.0.0", github_token="secret")

    mock_notes.assert_called_once()
    call_args, call_kwargs = mock_notes.call_args
    assert call_args == ("https://github.com/org/app",)
    assert call_kwargs["current"] == "1.0.0"
    assert call_kwargs["latest"] == "1.2.0"
    assert call_kwargs["token"] == "secret"
    assert isinstance(call_kwargs["logger"], logging.Logger)


def test_check_for_update_floating_tag_without_version_raises() -> None:
    with (
        patch("releaseprobe.check.fetch_labels", return_value={}),
        patch("releaseprobe.check.list_tags", return_value=["1.0.0", "1.2.0"]),
    ):
        with pytest.raises(UnknownVersionError):
            check_for_update("ghcr.io/org/app:latest")


def test_check_for_update_ignores_inherited_version_label() -> None:
    # e.g. ollama/ollama:latest, labeled with the version of its ubuntu:24.04 base.
    current_labels = {"org.opencontainers.image.version": "24.04"}

    with (
        patch("releaseprobe.check.fetch_labels", return_value={}),
        patch("releaseprobe.check.list_tags", return_value=["0.40.4", "0.40.5", "latest"]),
    ):
        with pytest.raises(UnknownVersionError):
            check_for_update("ollama/ollama:latest", current_labels)


def test_check_for_update_trusts_version_label_matching_a_tag() -> None:
    current_labels = {"org.opencontainers.image.version": "v1.0.0"}

    with (
        patch("releaseprobe.check.fetch_labels", return_value={}),
        patch("releaseprobe.check.list_tags", return_value=["1.0.0", "1.2.0"]),
        patch("releaseprobe.check.fetch_digest") as mock_digest,
        patch("releaseprobe.check.fetch_release_notes", return_value=[]),
    ):
        result = check_for_update(
            "ghcr.io/org/app:latest", current_labels, current_digests=["sha256:aaa"]
        )

    assert result.current_version == "v1.0.0"
    assert result.latest_version == "1.2.0"
    mock_digest.assert_not_called()


@pytest.mark.parametrize("label", ["main", "latest", "arch_0_linux-amd64_ubuntu"])
def test_check_for_update_unparseable_version_label_raises(label: str) -> None:
    # Used to be reported as "already up to date" with the label as version.
    current_labels = {"org.opencontainers.image.version": label}
    with (
        patch("releaseprobe.check.fetch_labels", return_value={}),
        patch("releaseprobe.check.list_tags", return_value=["1.0.0", "1.2.0", "main"]),
    ):
        with pytest.raises(UnknownVersionError):
            check_for_update("ghcr.io/org/app:latest", current_labels)


def test_check_for_update_unparseable_version_label_falls_back_to_digest() -> None:
    current_labels = {"org.opencontainers.image.version": "main"}
    with (
        patch("releaseprobe.check.fetch_labels", return_value={}),
        patch("releaseprobe.check.list_tags", return_value=["1.0.0", "1.2.0"]),
        patch("releaseprobe.check.fetch_digest", side_effect=["sha256:new", "sha256:old"]),
        patch("releaseprobe.check.fetch_release_notes", return_value=[]),
    ):
        result = check_for_update(
            "ghcr.io/org/app:latest", current_labels, current_digests=["sha256:old"]
        )

    assert result.current_version == "1.0.0"
    assert result.latest_version == "1.2.0"


def test_check_for_update_flavored_label_matching_a_tag() -> None:
    current_labels = {"org.opencontainers.image.version": "1.31.7-alpine-slim"}
    tags = ["1.31.7-alpine-slim", "1.32.0-alpine-slim", "1.40.0", "latest"]
    with (
        patch("releaseprobe.check.fetch_labels", return_value={}),
        patch("releaseprobe.check.list_tags", return_value=tags),
        patch("releaseprobe.check.fetch_release_notes", return_value=[]),
    ):
        result = check_for_update("ghcr.io/org/app:latest", current_labels)

    assert result.current_version == "1.31.7-alpine-slim"
    assert result.latest_version == "1.32.0-alpine-slim"


def test_check_for_update_ignores_flavored_label_of_base_image() -> None:
    # e.g. an app built on nginx:1.31.7-alpine-slim, inheriting its version label.
    current_labels = {"org.opencontainers.image.version": "1.31.7-alpine-slim"}
    with (
        patch("releaseprobe.check.fetch_labels", return_value={}),
        patch("releaseprobe.check.list_tags", return_value=["2.0.0", "2.1.0", "latest"]),
    ):
        with pytest.raises(UnknownVersionError):
            check_for_update("ghcr.io/org/app:latest", current_labels)


def test_check_for_update_pinned_flavored_tag() -> None:
    with (
        patch("releaseprobe.check.fetch_labels", return_value={}),
        patch(
            "releaseprobe.check.list_tags", return_value=["1.2.0-alpine", "1.3.0-alpine", "1.4.0"]
        ),
    ):
        result = check_for_update("docker.io/org/app:1.2.0-alpine")

    assert result.current_version == "1.2.0-alpine"
    assert result.latest_version == "1.3.0-alpine"


def test_check_for_update_resolves_floating_tag_by_digest() -> None:
    current_labels = {"org.opencontainers.image.version": "24.04"}
    digests = {"0.40.5": "sha256:new", "0.40.4": "sha256:old", "0.40.3": "sha256:older"}

    def fake_fetch_digest(ref: object, **kwargs: object) -> str:
        return digests[getattr(ref, "tag", "")]

    with (
        patch("releaseprobe.check.fetch_labels", return_value={}),
        patch("releaseprobe.check.list_tags", return_value=[*digests, "0.41.0-rc0", "latest"]),
        patch("releaseprobe.check.fetch_digest", side_effect=fake_fetch_digest) as mock_digest,
        patch("releaseprobe.check.fetch_release_notes", return_value=[]),
    ):
        result = check_for_update(
            "ollama/ollama:latest",
            current_labels,
            current_digests=["ollama/ollama@sha256:old"],
        )

    assert result.current_version == "0.40.4"
    assert result.latest_version == "0.40.5"
    # Newest first, stopping at the match; pre-releases aren't looked up.
    assert [call.args[0].tag for call in mock_digest.call_args_list] == ["0.40.5", "0.40.4"]


def test_check_for_update_raises_when_digest_matches_no_tag() -> None:
    with (
        patch("releaseprobe.check.fetch_labels", return_value={}),
        patch("releaseprobe.check.list_tags", return_value=["1.0.0", "1.2.0"]),
        patch("releaseprobe.check.fetch_digest", return_value="sha256:other"),
    ):
        with pytest.raises(UnknownVersionError):
            check_for_update("ghcr.io/org/app:latest", {}, current_digests=["sha256:aaa"])


def test_check_for_update_uses_custom_logger() -> None:
    custom_logger = logging.getLogger("custom-test-logger")

    with (
        patch("releaseprobe.check.fetch_labels", return_value={}) as mock_fetch,
        patch("releaseprobe.check.list_tags", return_value=["1.0.0"]) as mock_list,
    ):
        check_for_update("ghcr.io/org/app:1.0.0", logger=custom_logger)

    assert mock_fetch.call_args.kwargs["logger"] is custom_logger
    assert mock_list.call_args.kwargs["logger"] is custom_logger


def test_check_for_update_defaults_to_module_logger(caplog) -> None:
    with (
        patch("releaseprobe.check.fetch_labels", return_value={}),
        patch("releaseprobe.check.list_tags", return_value=["1.0.0"]),
        caplog.at_level(logging.INFO, logger="releaseprobe.check"),
    ):
        check_for_update("ghcr.io/org/app:1.0.0")

    assert any("already at the latest version" in message for message in caplog.messages)


_REV_OLD = "a" * 40
_REV_NEW = "b" * 40
_SOURCE = {"org.opencontainers.image.source": "https://github.com/org/app"}


def test_check_for_update_compares_revisions_of_floating_tag() -> None:
    installed = {**_SOURCE, "org.opencontainers.image.revision": _REV_OLD}
    remote = {**_SOURCE, "org.opencontainers.image.revision": _REV_NEW}
    note = ReleaseNote(version="bbbbbbb", name="3 new commits", url="u", body="- c")
    with (
        patch("releaseprobe.check.fetch_labels", return_value=remote) as mock_labels,
        patch("releaseprobe.check.list_tags", return_value=["latest", "main"]),
        patch("releaseprobe.check.fetch_commits_between", return_value=note) as mock_compare,
    ):
        result = check_for_update("ghcr.io/org/app:latest", installed)

    assert result.by_revision
    assert result.has_update
    assert result.current_version == "aaaaaaa"
    assert result.latest_version == "bbbbbbb"
    assert result.release_notes == [note]
    assert mock_labels.call_args.args[0].tag == "latest"
    assert mock_compare.call_args.kwargs["base"] == _REV_OLD
    assert mock_compare.call_args.kwargs["head"] == _REV_NEW


def test_check_for_update_revision_compare_failure_still_reports_update() -> None:
    installed = {**_SOURCE, "org.opencontainers.image.revision": _REV_OLD}
    remote = {**_SOURCE, "org.opencontainers.image.revision": _REV_NEW}
    with (
        patch("releaseprobe.check.fetch_labels", return_value=remote),
        patch("releaseprobe.check.list_tags", return_value=["latest"]),
        patch("releaseprobe.check.fetch_commits_between", side_effect=ChangelogError("404")),
    ):
        result = check_for_update("ghcr.io/org/app:latest", installed)

    assert result.by_revision
    assert result.latest_version == "bbbbbbb"
    assert result.release_notes == []


def test_check_for_update_same_revision_is_up_to_date() -> None:
    labels = {**_SOURCE, "org.opencontainers.image.revision": _REV_OLD}
    with (
        patch("releaseprobe.check.fetch_labels", return_value=labels),
        patch("releaseprobe.check.list_tags", return_value=["latest"]),
        patch("releaseprobe.check.fetch_digest", return_value="sha256:abc"),
    ):
        result = check_for_update(
            "ghcr.io/org/app:latest", labels, current_digests=["ghcr.io/org/app@sha256:abc"]
        )

    assert result.by_revision
    assert not result.has_update
    assert result.current_version == "aaaaaaa"


def test_check_for_update_same_revision_other_digest_is_unknown() -> None:
    # The revision label is inherited from the base image: it can't tell builds apart.
    labels = {"org.opencontainers.image.revision": _REV_OLD}
    with (
        patch("releaseprobe.check.fetch_labels", return_value=labels),
        patch("releaseprobe.check.list_tags", return_value=["latest"]),
        patch("releaseprobe.check.fetch_digest", return_value="sha256:new"),
        pytest.raises(UnknownVersionError),
    ):
        check_for_update("ghcr.io/org/app:latest", labels, current_digests=["sha256:old"])


def test_check_for_update_without_revision_is_unknown() -> None:
    with (
        patch("releaseprobe.check.fetch_labels", return_value={}),
        patch("releaseprobe.check.list_tags", return_value=["latest"]),
        pytest.raises(UnknownVersionError),
    ):
        check_for_update("ghcr.io/org/app:latest", {"org.opencontainers.image.version": "main"})


def test_check_for_update_reuses_cached_tags() -> None:
    cache: dict[str, list[str]] = {}
    with (
        patch("releaseprobe.check.fetch_labels", return_value={}),
        patch("releaseprobe.check.list_tags", return_value=["1.0.0", "1.1.0"]) as mock_tags,
    ):
        first = check_for_update("ghcr.io/org/app:1.0.0", {}, tags_cache=cache)
        second = check_for_update("ghcr.io/org/app:1.1.0", {}, tags_cache=cache)

    mock_tags.assert_called_once()
    assert cache == {"ghcr.io/org/app": ["1.0.0", "1.1.0"]}
    assert first.latest_version == "1.1.0"
    assert not second.has_update
