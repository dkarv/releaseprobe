from __future__ import annotations

from releaseprobe import labels
from releaseprobe.probe import probe_labels


def test_probe_labels_reads_oci_annotations() -> None:
    info = probe_labels(
        {
            labels.OCI_VERSION: "1.2.3",
            labels.OCI_SOURCE: "https://github.com/org/app",
            labels.OCI_URL: "https://app.example.com",
            labels.OCI_DOCUMENTATION: "https://docs.example.com",
            labels.OCI_REVISION: "abc123",
        }
    )

    assert info.version == "1.2.3"
    assert info.source == "https://github.com/org/app"
    assert info.url == "https://app.example.com"
    assert info.documentation == "https://docs.example.com"
    assert info.revision == "abc123"


def test_probe_labels_falls_back_to_label_schema() -> None:
    info = probe_labels(
        {
            labels.LABEL_SCHEMA_VERSION: "1.0.0",
            labels.LABEL_SCHEMA_VCS_URL: "https://github.com/org/legacy",
            labels.LABEL_SCHEMA_VCS_REF: "def456",
        }
    )

    assert info.version == "1.0.0"
    assert info.source == "https://github.com/org/legacy"
    assert info.revision == "def456"


def test_probe_labels_prefers_oci_over_label_schema() -> None:
    info = probe_labels(
        {
            labels.OCI_SOURCE: "https://github.com/org/app",
            labels.LABEL_SCHEMA_VCS_URL: "https://github.com/org/legacy",
        }
    )

    assert info.source == "https://github.com/org/app"


def test_probe_labels_keeps_raw_labels() -> None:
    raw = {labels.OCI_VERSION: "1.2.3"}
    info = probe_labels(raw)

    assert info.raw_labels == raw


def test_release_notes_url_prefers_source_then_url_then_documentation() -> None:
    assert (
        probe_labels(
            {
                labels.OCI_SOURCE: "https://github.com/org/app",
                labels.OCI_URL: "https://app.example.com",
                labels.OCI_DOCUMENTATION: "https://docs.example.com",
            }
        ).release_notes_url()
        == "https://github.com/org/app"
    )

    assert (
        probe_labels(
            {
                labels.OCI_URL: "https://app.example.com",
                labels.OCI_DOCUMENTATION: "https://docs.example.com",
            }
        ).release_notes_url()
        == "https://app.example.com"
    )

    assert (
        probe_labels({labels.OCI_DOCUMENTATION: "https://docs.example.com"}).release_notes_url()
        == "https://docs.example.com"
    )


def test_release_notes_url_returns_none_when_absent() -> None:
    assert probe_labels({}).release_notes_url() is None
