"""Command line interface for releaseprobe."""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sys
from typing import TextIO

from releaseprobe import __version__
from releaseprobe.changelog import ChangelogError
from releaseprobe.check import UnknownVersionError, UpdateCheck, check_for_update
from releaseprobe.imageref import InvalidImageReferenceError
from releaseprobe.probe import ReleaseInfo, probe_labels
from releaseprobe.registry import RegistryError

CheckError = (InvalidImageReferenceError, RegistryError, ChangelogError, UnknownVersionError)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="releaseprobe",
        description="Find release notes, URLs and metadata from container image labels and tags.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase log verbosity on stderr (-v for INFO, -vv for DEBUG); "
        "useful for debugging a failed check remotely.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    labels_parser = subparsers.add_parser(
        "labels",
        help="Interpret a JSON object of image labels (offline, no network access).",
        description=(
            "Interpret a JSON object of image labels, e.g. the output of "
            "`docker inspect --format '{{json .Config.Labels}}' <image>`."
        ),
    )
    labels_parser.add_argument(
        "labels_file",
        nargs="?",
        default="-",
        help="Path to a JSON file with the image labels, or '-' to read from stdin (default).",
    )
    labels_parser.add_argument(
        "--json", action="store_true", help="Print the full result as JSON."
    )

    check_parser = subparsers.add_parser(
        "check",
        help="Query the image's registry for a newer tag and summarize what changed.",
        description=(
            "Look up whether a newer tag is available for an image (Docker Hub or any "
            "registry implementing the Docker Registry HTTP API v2), and if so, fetch "
            "the release notes for every version in between."
        ),
    )
    check_parser.add_argument("image", help="Image reference, e.g. ghcr.io/org/app:1.2.3")
    check_parser.add_argument(
        "--current-labels",
        metavar="FILE",
        help=(
            "Path to a JSON file with the currently installed image's labels "
            "(e.g. the output of `docker inspect --format '{{json .Config.Labels}}' "
            "<container>`), or '-' for stdin. Required to detect updates when the "
            "image reference uses a floating tag like `:latest` or `:stable`, since "
            "the tag itself carries no version to compare from."
        ),
    )
    check_parser.add_argument("--json", action="store_true", help="Print the full result as JSON.")

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


def _print_release_info(info: ReleaseInfo, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(dataclasses.asdict(info), indent=2))
        return
    print(f"version:       {info.version or '-'}")
    print(f"source:        {info.source or '-'}")
    print(f"url:           {info.url or '-'}")
    print(f"documentation: {info.documentation or '-'}")
    print(f"revision:      {info.revision or '-'}")
    print(f"release notes: {info.release_notes_url() or '-'}")


def _run_labels(args: argparse.Namespace) -> int:
    try:
        labels = _read_labels(args.labels_file, sys.stdin)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: could not read labels: {exc}", file=sys.stderr)
        return 1

    _print_release_info(probe_labels(labels), as_json=args.json)
    return 0


def _print_check_result(result: UpdateCheck) -> None:
    if not result.has_update:
        print(f"{result.image}: already at the latest version ({result.current_version}).")
        return

    print(f"{result.image}: {result.current_version} -> {result.latest_version}")

    if not result.release_notes:
        source = result.latest_info.source if result.latest_info else None
        print(f"release notes: {source or '-'}")
        return

    for note in result.release_notes:
        print(f"\n## {note.name or note.version}")
        if note.url:
            print(note.url)
        if note.body:
            print(f"\n{note.body}")


def _run_check(args: argparse.Namespace) -> int:
    current_labels = None
    if args.current_labels is not None:
        try:
            current_labels = _read_labels(args.current_labels, sys.stdin)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"error: could not read current labels: {exc}", file=sys.stderr)
            return 1

    try:
        result = check_for_update(args.image, current_labels)
    except CheckError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(
            json.dumps(
                {
                    "image": result.image,
                    "current_version": result.current_version,
                    "latest_version": result.latest_version,
                    "has_update": result.has_update,
                    "release_notes": [dataclasses.asdict(n) for n in result.release_notes],
                },
                indent=2,
            )
        )
        return 0

    _print_check_result(result)
    return 0


_VERBOSITY_LEVELS = (logging.WARNING, logging.INFO, logging.DEBUG)


def _configure_logging(verbosity: int) -> None:
    level = _VERBOSITY_LEVELS[min(verbosity, len(_VERBOSITY_LEVELS) - 1)]
    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format="%(levelname)s %(name)s: %(message)s",
        force=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    if args.command == "labels":
        return _run_labels(args)
    return _run_check(args)


if __name__ == "__main__":
    raise SystemExit(main())
