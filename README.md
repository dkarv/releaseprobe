# releaseprobe

Find release notes, URLs and other release metadata for container images by
reading their labels. Inspired by [Renovate's docker datasource](https://docs.renovatebot.com/modules/datasource/docker/):

- [OCI image labels](https://github.com/opencontainers/image-spec/blob/main/annotations.md)
  (`org.opencontainers.image.*`), preferred.
- [label-schema.org](http://label-schema.org/rc1/) labels (`org.label-schema.*`),
  as a fallback for older images.

releaseprobe does not talk to a registry or the Docker daemon itself - it
takes the labels you already have (from `docker inspect`, `skopeo inspect`,
`crane config`, the registry HTTP API, a Kubernetes pod spec, ...) as input
and tells you what release metadata they contain.

## Installation

```bash
pip install releaseprobe
```

## Usage

Pass labels as a JSON object, e.g. straight from `docker inspect`:

```bash
docker inspect --format '{{json .Config.Labels}}' ghcr.io/org/app:1.2.3 \
  | releaseprobe
```

```
version:       1.2.3
source:        https://github.com/org/app
url:           -
documentation: -
revision:      abc123
release notes: https://github.com/org/app
```

A path to a JSON file works too (`releaseprobe labels.json`); omit the path or
pass `-` to read from stdin. Add `--json` for machine-readable output.

## Library usage

```python
from releaseprobe import probe_labels

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

The container itself has no Docker CLI, so to try releaseprobe against a
real image, inspect it from your host and drop the labels into the workspace
(they land in the container too, since the workspace folder is mounted in):

```bash
# on your host
docker inspect --format '{{json .Config.Labels}}' ghcr.io/grafana/grafana:11.2.0 > labels.json
```

```bash
# inside the dev container
releaseprobe labels.json
```

## Releasing

Bump the `__version__` string in `src/releaseprobe/__init__.py` and merge to
`main`. The CI pipeline detects the version bump, tags the commit, creates a
GitHub release with auto-generated notes, and publishes the package to PyPI.

## License

MIT, see [LICENSE](LICENSE).
