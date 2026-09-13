from __future__ import annotations

import json
from email.message import Message
from unittest.mock import patch
from urllib.request import Request

from releaseprobe.changelog import fetch_release_notes, parse_github_repo


class FakeResponse:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None) -> None:
        self._body = body
        self.headers = Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


def test_parse_github_repo_variants() -> None:
    assert parse_github_repo("https://github.com/org/app") == ("org", "app")
    assert parse_github_repo("https://github.com/org/app.git") == ("org", "app")
    assert parse_github_repo("http://github.com/org/app/") == ("org", "app")
    assert parse_github_repo("git@github.com:org/app.git") == ("org", "app")


def test_parse_github_repo_rejects_non_github() -> None:
    assert parse_github_repo("https://gitlab.com/org/app") is None


def test_fetch_release_notes_filters_to_version_range() -> None:
    releases = [
        {"tag_name": "v1.3.0", "name": "1.3.0", "html_url": "u3", "body": "three"},
        {"tag_name": "v1.2.0", "name": "1.2.0", "html_url": "u2", "body": "two"},
        {"tag_name": "v1.1.0", "name": "1.1.0", "html_url": "u1", "body": "one"},
        {"tag_name": "v1.0.0", "name": "1.0.0", "html_url": "u0", "body": "zero"},
        {"tag_name": "not-a-version", "name": "bad", "html_url": "ux", "body": "x"},
    ]

    def fake_urlopen(request: Request, timeout: int = 10) -> FakeResponse:
        assert "api.github.com/repos/org/app/releases" in request.full_url
        return FakeResponse(json.dumps(releases).encode())

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        notes = fetch_release_notes(
            "https://github.com/org/app", current="1.0.0", latest="1.2.0"
        )

    assert [n.version for n in notes] == ["v1.1.0", "v1.2.0"]
    assert notes[0].body == "one"


def test_fetch_release_notes_returns_empty_for_non_github_source() -> None:
    assert fetch_release_notes("https://gitlab.com/org/app", current="1.0.0", latest="1.2.0") == []


def test_fetch_release_notes_uses_given_token_over_env(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    captured_headers: dict[str, str] = {}

    def fake_urlopen(request: Request, timeout: int = 10) -> FakeResponse:
        captured_headers.update(request.headers)
        return FakeResponse(b"[]")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        fetch_release_notes(
            "https://github.com/org/app", current="1.0.0", latest="1.2.0", token="arg-token"
        )

    assert captured_headers["Authorization"] == "Bearer arg-token"


def test_fetch_release_notes_falls_back_to_env_token(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    captured_headers: dict[str, str] = {}

    def fake_urlopen(request: Request, timeout: int = 10) -> FakeResponse:
        captured_headers.update(request.headers)
        return FakeResponse(b"[]")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        fetch_release_notes("https://github.com/org/app", current="1.0.0", latest="1.2.0")

    assert captured_headers["Authorization"] == "Bearer env-token"


def test_fetch_release_notes_follows_pagination() -> None:
    page_one = "https://api.github.com/repos/org/app/releases?per_page=100"
    page_two = "https://api.github.com/repos/org/app/releases?per_page=100&page=2"

    release_one = {"tag_name": "v1.1.0", "name": None, "html_url": "u1", "body": "one"}
    release_two = {"tag_name": "v1.2.0", "name": None, "html_url": "u2", "body": "two"}

    def fake_urlopen(request: Request, timeout: int = 10) -> FakeResponse:
        if request.full_url == page_one:
            return FakeResponse(
                json.dumps([release_one]).encode(), {"Link": f'<{page_two}>; rel="next"'}
            )
        if request.full_url == page_two:
            return FakeResponse(json.dumps([release_two]).encode())
        raise AssertionError(f"unexpected URL {request.full_url}")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        notes = fetch_release_notes(
            "https://github.com/org/app", current="1.0.0", latest="1.2.0"
        )

    assert [n.version for n in notes] == ["v1.1.0", "v1.2.0"]
