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
