"""Command line interface for releaseprobe."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from typing import TextIO

from releaseprobe import __version__
from releaseprobe.probe import probe_labels


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="releaseprobe",
        description=(
            "Find release notes, URLs and metadata from container image labels. "
            "Labels are read as a JSON object, e.g. the output of "
            "`docker inspect --format '{{json .Config.Labels}}' <image>`."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "labels_file",
        nargs="?",
        default="-",
        help="Path to a JSON file with the image labels, or '-' to read from stdin (default).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full result as JSON instead of a human-readable summary.",
    )
    return parser


def _read_labels(path: str, stdin: TextIO) -> dict[str, str]:
    if path == "-":
        raw = stdin.read()
    else:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    data = json.loads(raw) if raw.strip() else {}
    if not isinstance(data, dict):
        raise ValueError("labels JSON must be an object of label key/value pairs")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        labels = _read_labels(args.labels_file, sys.stdin)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: could not read labels: {exc}", file=sys.stderr)
        return 1

    info = probe_labels(labels)

    if args.json:
        print(json.dumps(dataclasses.asdict(info), indent=2))
        return 0

    print(f"version:       {info.version or '-'}")
    print(f"source:        {info.source or '-'}")
    print(f"url:           {info.url or '-'}")
    print(f"documentation: {info.documentation or '-'}")
    print(f"revision:      {info.revision or '-'}")
    print(f"release notes: {info.release_notes_url() or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
