from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from releaseprobe.changelog import ReleaseNote
from releaseprobe.check import UnknownVersionError, check_for_update


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
        patch("releaseprobe.check.fetch_release_notes") as mock_notes,
    ):
        result = check_for_update("ghcr.io/org/app:1.0.0")

    assert result.has_update
    assert result.release_notes == []
    mock_notes.assert_not_called()


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
