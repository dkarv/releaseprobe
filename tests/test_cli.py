from __future__ import annotations

import json
from unittest.mock import patch

from releaseprobe import __version__
from releaseprobe.changelog import ReleaseNote
from releaseprobe.check import UpdateCheck
from releaseprobe.cli import main
from releaseprobe.probe import ReleaseInfo


def test_version_flag(capsys) -> None:
    exit_code = None
    try:
        main(["--version"])
    except SystemExit as exc:
        exit_code = exc.code

    assert exit_code == 0
    assert __version__ in capsys.readouterr().out


def test_labels_prints_human_summary(tmp_path, capsys) -> None:
    labels_file = tmp_path / "labels.json"
    labels_file.write_text(
        json.dumps(
            {
                "org.opencontainers.image.version": "1.2.3",
                "org.opencontainers.image.source": "https://github.com/org/app",
            }
        )
    )

    exit_code = main(["labels", str(labels_file)])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "version:       1.2.3" in out
    assert "release notes: https://github.com/org/app" in out


def test_labels_prints_json(tmp_path, capsys) -> None:
    labels_file = tmp_path / "labels.json"
    labels_file.write_text(json.dumps({"org.opencontainers.image.version": "1.2.3"}))

    exit_code = main(["labels", str(labels_file), "--json"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert json.loads(out)["version"] == "1.2.3"


def test_labels_reads_from_stdin(monkeypatch, capsys) -> None:
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO('{"org.opencontainers.image.version": "9.9.9"}'))

    exit_code = main(["labels", "-"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "version:       9.9.9" in out


def test_labels_reports_invalid_json(tmp_path, capsys) -> None:
    labels_file = tmp_path / "labels.json"
    labels_file.write_text("not json")

    exit_code = main(["labels", str(labels_file)])

    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_labels_reports_missing_file(capsys) -> None:
    exit_code = main(["labels", "/nonexistent/labels.json"])

    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_check_reports_no_update(capsys) -> None:
    result = UpdateCheck(
        image="ghcr.io/org/app:1.0.0",
        current_version="1.0.0",
        latest_version=None,
        current_info=ReleaseInfo(),
    )
    with patch("releaseprobe.cli.check_for_update", return_value=result):
        exit_code = main(["check", "ghcr.io/org/app:1.0.0"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "already at the latest version" in out


def test_check_prints_release_notes(capsys) -> None:
    result = UpdateCheck(
        image="ghcr.io/org/app:1.0.0",
        current_version="1.0.0",
        latest_version="1.2.0",
        current_info=ReleaseInfo(),
        latest_info=ReleaseInfo(source="https://github.com/org/app"),
        release_notes=[
            ReleaseNote(version="1.2.0", name="1.2.0", url="https://x", body="what changed")
        ],
    )
    with patch("releaseprobe.cli.check_for_update", return_value=result):
        exit_code = main(["check", "ghcr.io/org/app:1.0.0"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "1.0.0 -> 1.2.0" in out
    assert "what changed" in out


def test_check_falls_back_to_link_without_release_notes(capsys) -> None:
    result = UpdateCheck(
        image="ghcr.io/org/app:1.0.0",
        current_version="1.0.0",
        latest_version="1.2.0",
        current_info=ReleaseInfo(),
        latest_info=ReleaseInfo(source="https://gitlab.com/org/app"),
        release_notes=[],
    )
    with patch("releaseprobe.cli.check_for_update", return_value=result):
        exit_code = main(["check", "ghcr.io/org/app:1.0.0"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "release notes: https://gitlab.com/org/app" in out


def test_check_prints_json(capsys) -> None:
    result = UpdateCheck(
        image="ghcr.io/org/app:1.0.0",
        current_version="1.0.0",
        latest_version="1.2.0",
        current_info=ReleaseInfo(),
        release_notes=[],
    )
    with patch("releaseprobe.cli.check_for_update", return_value=result):
        exit_code = main(["check", "ghcr.io/org/app:1.0.0", "--json"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert json.loads(out)["latest_version"] == "1.2.0"


def test_check_reports_errors(capsys) -> None:
    from releaseprobe.registry import RegistryError

    with patch("releaseprobe.cli.check_for_update", side_effect=RegistryError("boom")):
        exit_code = main(["check", "ghcr.io/org/app:1.0.0"])

    assert exit_code == 1
    assert "boom" in capsys.readouterr().err


def test_check_reports_unknown_version_error(capsys) -> None:
    from releaseprobe.check import UnknownVersionError

    with patch(
        "releaseprobe.cli.check_for_update", side_effect=UnknownVersionError("no version")
    ):
        exit_code = main(["check", "ghcr.io/org/app:latest"])

    assert exit_code == 1
    assert "no version" in capsys.readouterr().err


def test_check_passes_current_labels_from_file(tmp_path) -> None:
    labels_file = tmp_path / "current.json"
    labels_file.write_text(json.dumps({"org.opencontainers.image.version": "1.0.0"}))

    result = UpdateCheck(
        image="ghcr.io/org/app:latest",
        current_version="1.0.0",
        latest_version=None,
        current_info=ReleaseInfo(),
    )
    with patch("releaseprobe.cli.check_for_update", return_value=result) as mock_check:
        exit_code = main(
            ["check", "ghcr.io/org/app:latest", "--current-labels", str(labels_file)]
        )

    assert exit_code == 0
    mock_check.assert_called_once_with(
        "ghcr.io/org/app:latest", {"org.opencontainers.image.version": "1.0.0"}
    )


def test_check_passes_current_labels_from_stdin(monkeypatch) -> None:
    import io

    monkeypatch.setattr(
        "sys.stdin", io.StringIO('{"org.opencontainers.image.version": "1.0.0"}')
    )

    result = UpdateCheck(
        image="ghcr.io/org/app:latest",
        current_version="1.0.0",
        latest_version=None,
        current_info=ReleaseInfo(),
    )
    with patch("releaseprobe.cli.check_for_update", return_value=result) as mock_check:
        exit_code = main(["check", "ghcr.io/org/app:latest", "--current-labels", "-"])

    assert exit_code == 0
    mock_check.assert_called_once_with(
        "ghcr.io/org/app:latest", {"org.opencontainers.image.version": "1.0.0"}
    )


def test_check_reports_invalid_current_labels(tmp_path, capsys) -> None:
    labels_file = tmp_path / "current.json"
    labels_file.write_text("not json")

    exit_code = main(
        ["check", "ghcr.io/org/app:latest", "--current-labels", str(labels_file)]
    )

    assert exit_code == 1
    assert "error" in capsys.readouterr().err
