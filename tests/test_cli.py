from __future__ import annotations

import json

from releaseprobe import __version__
from releaseprobe.cli import main


def test_version_flag(capsys) -> None:
    exit_code = None
    try:
        main(["--version"])
    except SystemExit as exc:
        exit_code = exc.code

    assert exit_code == 0
    assert __version__ in capsys.readouterr().out


def test_main_prints_human_summary(tmp_path, capsys) -> None:
    labels_file = tmp_path / "labels.json"
    labels_file.write_text(
        json.dumps(
            {
                "org.opencontainers.image.version": "1.2.3",
                "org.opencontainers.image.source": "https://github.com/org/app",
            }
        )
    )

    exit_code = main([str(labels_file)])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "version:       1.2.3" in out
    assert "release notes: https://github.com/org/app" in out


def test_main_prints_json(tmp_path, capsys) -> None:
    labels_file = tmp_path / "labels.json"
    labels_file.write_text(json.dumps({"org.opencontainers.image.version": "1.2.3"}))

    exit_code = main([str(labels_file), "--json"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert json.loads(out)["version"] == "1.2.3"


def test_main_reads_labels_from_stdin(monkeypatch, capsys) -> None:
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO('{"org.opencontainers.image.version": "9.9.9"}'))

    exit_code = main(["-"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "version:       9.9.9" in out


def test_main_reports_invalid_json(tmp_path, capsys) -> None:
    labels_file = tmp_path / "labels.json"
    labels_file.write_text("not json")

    exit_code = main([str(labels_file)])

    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_main_reports_missing_file(capsys) -> None:
    exit_code = main(["/nonexistent/labels.json"])

    assert exit_code == 1
    assert "error" in capsys.readouterr().err
