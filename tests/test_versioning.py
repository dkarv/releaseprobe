from __future__ import annotations

from releaseprobe.versioning import find_latest, parse


def test_parse_accepts_semver_and_v_prefix() -> None:
    assert parse("1.2.3") is not None
    assert parse("v1.2.3") is not None


def test_parse_rejects_non_versions() -> None:
    assert parse("latest") is None
    assert parse("sha256-deadbeef.sig") is None


def test_parse_rejects_bare_build_numbers() -> None:
    # A bare integer is a "valid" PEP 440 version, but tags like Docker
    # Hub build numbers (e.g. "9799770991") must not be mistaken for one.
    assert parse("9799770991") is None


def test_find_latest_ignores_bare_build_number_tags() -> None:
    tags = ["11.2.0", "11.3.0", "9799770991"]
    assert find_latest(tags, "11.2.0") == "11.3.0"


def test_find_latest_picks_highest_newer_version() -> None:
    tags = ["1.0.0", "1.2.0", "1.1.0", "latest", "1.2.0-rc1"]
    assert find_latest(tags, "1.0.0") == "1.2.0"


def test_find_latest_ignores_prereleases() -> None:
    tags = ["1.0.0", "2.0.0rc1"]
    assert find_latest(tags, "1.0.0") is None


def test_find_latest_returns_none_when_already_latest() -> None:
    assert find_latest(["1.0.0", "1.2.0"], "1.2.0") is None


def test_find_latest_returns_none_when_current_unparseable() -> None:
    assert find_latest(["1.0.0", "1.2.0"], "latest") is None


def test_find_latest_handles_v_prefix_consistently() -> None:
    assert find_latest(["v1.0.0", "v1.2.0"], "v1.0.0") == "v1.2.0"
