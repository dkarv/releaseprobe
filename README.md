# releaseprobe

Find out whether a newer version of a container image is available, and what
changed. Inspired by [Renovate's docker datasource](https://docs.renovatebot.com/modules/datasource/docker/):

- Reads [OCI image labels](https://github.com/opencontainers/image-spec/blob/main/annotations.md)
  (`org.opencontainers.image.*`), preferring them over the deprecated
  [label-schema.org](http://label-schema.org/rc1/) labels (`org.label-schema.*`).
- Talks to the image's registry directly (Docker Hub, GHCR, Quay, or any
  registry implementing the [Docker Registry HTTP API v2](https://distribution.github.io/distribution/spec/api/))
  to list tags and fetch labels - no docker daemon required.
- If the image's source label points at GitHub, fetches the real release
  notes for every version between the current and the latest tag.

## Installation

```bash
pip install releaseprobe
```

## Usage

### Check for a newer version

```bash
releaseprobe check grafana/grafana:11.2.0
```

```
registry-1.docker.io/grafana/grafana:11.2.0: 11.2.0 -> 13.2.1

## 11.2.1
https://github.com/grafana/grafana/releases/tag/v11.2.1

### Features and enhancements
...
```

If the image is already at the latest version, it says so and exits; add
`--json` for machine-readable output (`current_version`, `latest_version`,
`has_update`, `release_notes`).

Set a `GITHUB_TOKEN` environment variable to avoid GitHub's low unauthenticated
rate limit when checking images with many releases.

#### Floating tags (`:latest`, `:stable`, ...)

A floating tag carries no version of its own, so there is nothing to compare
against the registry's other tags. Pass the *currently installed* image's
labels with `--current-labels` so releaseprobe can read the real version
(`org.opencontainers.image.version`) out of them instead:

```bash
docker inspect --format '{{json .Config.Labels}}' my-homeassistant-container \
  | releaseprobe check homeassistant/home-assistant:stable --current-labels -
```

A path to a JSON file works too (`--current-labels labels.json`). Without
`--current-labels`, a floating tag fails with an error rather than silently
comparing against the wrong version.

**Limitations:**

- Only public/anonymous registry access is supported - no credentials for
  private images or registries yet.
- Tags are compared as versions using a simple `X.Y[.Z...]` pattern (PEP 440
  under the hood, so `v1.2.3` works too); tags that don't look like a
  version - `latest`, digests, build numbers, `1.2.3-alpine` flavor suffixes -
  are ignored, and pre-releases are never suggested as "latest".
- Real release notes are only fetched for GitHub-hosted sources; other hosts
  fall back to just the source URL.

### Interpret labels you already have

For offline use (no network access), `releaseprobe labels` interprets a JSON
object of labels directly, e.g. straight from `docker inspect`:

```bash
docker inspect --format '{{json .Config.Labels}}' ghcr.io/org/app:1.2.3 \
  | releaseprobe labels
```

```
version:       1.2.3
source:        https://github.com/org/app
url:           -
documentation: -
revision:      abc123
release notes: https://github.com/org/app
```

A path to a JSON file works too (`releaseprobe labels labels.json`); omit the
path or pass `-` to read from stdin. Add `--json` for machine-readable output.

## Library usage

```python
from releaseprobe import check_for_update, probe_labels

result = check_for_update("grafana/grafana:11.2.0")
result.has_update  # True
result.latest_version  # "13.2.1"
result.release_notes  # list[ReleaseNote], oldest first

info = probe_labels({"org.opencontainers.image.source": "https://github.com/org/app"})
info.release_notes_url()  # "https://github.com/org/app"
```

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

pytest
ruff check .
mypy
```

### Dev container

A [dev container](.devcontainer/devcontainer.json) is included so you can try
releaseprobe in VS Code: open the repo and choose "Reopen in Container"
(needs the Dev Containers extension). It gives you a Python 3.12 environment
with the project installed in editable mode.

`releaseprobe check <image>` works straight away in the container - it only
needs outbound network access, not a docker daemon. To try the offline
`labels` command against a real image instead, inspect it from your host and
drop the labels into the workspace (they land in the container too, since the
workspace folder is mounted in):

```bash
# on your host
docker inspect --format '{{json .Config.Labels}}' ghcr.io/grafana/grafana:11.2.0 > labels.json
```

```bash
# inside the dev container
releaseprobe labels labels.json
```

## Releasing

Bump the `__version__` string in `src/releaseprobe/__init__.py` and merge to
`main`. The CI pipeline detects the version bump, tags the commit, creates a
GitHub release with auto-generated notes, and publishes the package to PyPI.

## License

MIT, see [LICENSE](LICENSE).
