from __future__ import annotations

import pytest

from releaseprobe.imageref import ImageReference, InvalidImageReferenceError, parse


def test_parse_docker_hub_official_image() -> None:
    assert parse("nginx:1.25") == ImageReference(
        registry="registry-1.docker.io", repository="library/nginx", tag="1.25"
    )


def test_parse_docker_hub_namespaced_image() -> None:
    assert parse("grafana/grafana:11.2.0") == ImageReference(
        registry="registry-1.docker.io", repository="grafana/grafana", tag="11.2.0"
    )


def test_parse_normalizes_explicit_docker_hub_hosts() -> None:
    # docker.io/index.docker.io are not the real registry API host, and
    # single-segment repos still need the implicit `library/` namespace
    # even when a Docker Hub host is spelled out explicitly. Getting this
    # wrong makes Docker Hub return 401 (not 404) for the bad repo path.
    for host in ("docker.io", "index.docker.io", "registry-1.docker.io"):
        assert parse(f"{host}/nginx:1.25") == ImageReference(
            registry="registry-1.docker.io", repository="library/nginx", tag="1.25"
        )
        assert parse(f"{host}/grafana/grafana:11.2.0") == ImageReference(
            registry="registry-1.docker.io", repository="grafana/grafana", tag="11.2.0"
        )


def test_parse_third_party_registry() -> None:
    assert parse("ghcr.io/org/app:1.2.3") == ImageReference(
        registry="ghcr.io", repository="org/app", tag="1.2.3"
    )


def test_parse_registry_with_port() -> None:
    assert parse("localhost:5000/myrepo:1.0") == ImageReference(
        registry="localhost:5000", repository="myrepo", tag="1.0"
    )


def test_parse_rejects_missing_tag() -> None:
    with pytest.raises(InvalidImageReferenceError, match="no explicit tag"):
        parse("nginx")


def test_parse_rejects_registry_with_port_but_no_tag() -> None:
    with pytest.raises(InvalidImageReferenceError, match="no explicit tag"):
        parse("localhost:5000/myrepo")


def test_parse_rejects_digest_references() -> None:
    with pytest.raises(InvalidImageReferenceError, match="digest references"):
        parse("nginx@sha256:" + "a" * 64)


def test_str_roundtrip() -> None:
    assert str(parse("ghcr.io/org/app:1.2.3")) == "ghcr.io/org/app:1.2.3"
