"""End-to-end checks against real registries, driven by the samples in tests/data.

Each `<name>.txt` holds an image reference and `<name>.json` the labels of
the installed image (as `docker inspect --format '{{json .Config.Labels}}'`
prints them). Every pair is run through `releaseprobe check` and must lead to
a usable result: either a newer version (optionally with release notes), or
- if there is no update - a current version that really matches one of the
registry's version tags, so a silently failed comparison doesn't pass as
"already at the latest version".

These tests talk to container registries and GitHub; deselect them with
`pytest -m "not network"`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from releaseprobe.cli import main
from releaseprobe.imageref import parse as parse_image_reference
from releaseprobe.registry import list_tags
from releaseprobe.versioning import parse as parse_version

DATA_DIR = Path(__file__).parent / "data"

SAMPLES = sorted(path.stem for path in DATA_DIR.glob("*.txt") if path.with_suffix(".json").exists())


@pytest.mark.network
@pytest.mark.parametrize("name", SAMPLES)
def test_check_sample(name: str, capsys: pytest.CaptureFixture[str]) -> None:
    image = (DATA_DIR / f"{name}.txt").read_text(encoding="utf-8").strip()
    if not image:
        pytest.skip(f"{name}.txt has no image reference yet")
    labels_file = DATA_DIR / f"{name}.json"

    exit_code = main(["check", image, "--current-labels", str(labels_file), "--json"])
    captured = capsys.readouterr()
    assert exit_code == 0, captured.err

    result = json.loads(captured.out)
    current = parse_version(result["current_version"])
    assert current is not None, f"current version {result['current_version']!r} not comparable"

    if result["has_update"]:
        latest = parse_version(result["latest_version"])
        assert latest is not None
        assert latest > current
        for note in result["release_notes"]:
            assert note["url"] or note["body"], f"empty release note {note}"
        return

    tags = list_tags(parse_image_reference(image))
    assert any(parse_version(tag) == current for tag in tags), (
        f"no update reported, but no registry tag matches {result['current_version']!r}"
    )
