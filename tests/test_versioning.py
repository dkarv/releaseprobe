from __future__ import annotations

import pytest
from packaging.version import Version

from releaseprobe.versioning import UnknownVersionError, find_latest, parse, parse_flavored


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


def test_find_latest_raises_when_current_unparseable() -> None:
    # "Can't compare" must not look like "already up to date".
    with pytest.raises(UnknownVersionError):
        find_latest(["1.0.0", "1.2.0"], "latest")


def test_find_latest_handles_v_prefix_consistently() -> None:
    assert find_latest(["v1.0.0", "v1.2.0"], "v1.0.0") == "v1.2.0"


def test_parse_duplicati_channel_tags() -> None:
    stable = parse("v2.4.0.0_stable_2026-09-03-ls309")
    assert stable is not None
    assert not stable.is_prerelease
    assert stable.release == (2, 4, 0, 0)
    assert parse("v2.4.0.0_stable_2026-09-03") == Version("2.4.0.0")
    canary = parse("v2.4.0.105_canary_2026-09-10-ls310")
    assert canary is not None
    assert canary.is_prerelease
    assert parse("v2.4.0.0_bogus_2026-09-03") is None


def test_parse_linuxserver_build_suffix() -> None:
    assert parse("1.2.3-ls45") is not None
    assert parse("1.2.3-alpine") is None


def test_find_latest_duplicati_skips_canary_and_prefers_newer_build() -> None:
    tags = [
        "latest",
        "v2.3.0.0_stable_2026-06-01-ls290",
        "v2.4.0.0_stable_2026-09-03-ls309",
        "v2.4.0.0_stable_2026-09-03-ls310",
        "v2.4.0.105_canary_2026-09-10-ls311",
    ]
    assert find_latest(tags, "v2.3.0.0_stable_2026-06-01-ls290") == (
        "v2.4.0.0_stable_2026-09-03-ls310"
    )
    assert find_latest(tags, "v2.4.0.0_stable_2026-09-03-ls310") is None


def test_find_latest_newer_linuxserver_build_of_same_version() -> None:
    assert find_latest(["1.2.3-ls45", "1.2.3-ls46"], "1.2.3-ls45") == "1.2.3-ls46"


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("1.2.3", ("1.2.3", "")),
        ("1.31.7-alpine-slim", ("1.31.7", "alpine-slim")),
        ("2.0.0-rc1-alpine", ("2.0.0rc1", "alpine")),
        ("1.2.3-beta", ("1.2.3b0", "")),
        ("1.2.3-ls45", ("1.2.3+ls45", "")),
        ("v1.2-alpine3.20", ("1.2", "alpine3.20")),
    ],
)
def test_parse_flavored(tag: str, expected: tuple[str, str]) -> None:
    parsed = parse_flavored(tag)
    assert parsed is not None
    assert (str(parsed[0]), parsed[1]) == expected


@pytest.mark.parametrize(
    "tag", ["latest", "main", "arch_0_linux-amd64_ubuntu", "1-alpine", "1.2.3-"]
)
def test_parse_flavored_rejects_non_versions(tag: str) -> None:
    assert parse_flavored(tag) is None


def test_parse_still_rejects_flavored_tags() -> None:
    assert parse("1.2.3-alpine") is None


def test_find_latest_stays_within_flavor() -> None:
    tags = [
        "1.31.7-alpine-slim",
        "1.32.0-alpine-slim",
        "1.33.0",
        "1.34.0-alpine",
        "1.35.0-rc1-alpine-slim",
    ]
    assert find_latest(tags, "1.31.7-alpine-slim") == "1.32.0-alpine-slim"
    assert find_latest(tags, "1.30.0") == "1.33.0"
