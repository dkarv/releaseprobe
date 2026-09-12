"""Well-known container image label keys relevant to release metadata.

Two competing conventions are in use in the wild, mirroring how Renovate's
docker datasource reads them (see
https://docs.renovatebot.com/modules/datasource/docker/):

- OCI Image Format Specification (current):
  https://github.com/opencontainers/image-spec/blob/main/annotations.md
- label-schema.org (deprecated, still common on older images):
  http://label-schema.org/rc1/
"""

from __future__ import annotations

# OCI Image Format Specification annotations.
OCI_SOURCE = "org.opencontainers.image.source"
OCI_URL = "org.opencontainers.image.url"
OCI_DOCUMENTATION = "org.opencontainers.image.documentation"
OCI_VERSION = "org.opencontainers.image.version"
OCI_REVISION = "org.opencontainers.image.revision"
OCI_VENDOR = "org.opencontainers.image.vendor"
OCI_TITLE = "org.opencontainers.image.title"
OCI_DESCRIPTION = "org.opencontainers.image.description"
OCI_CREATED = "org.opencontainers.image.created"

# label-schema.org annotations (deprecated), used as a fallback when the OCI
# label is absent.
LABEL_SCHEMA_VCS_URL = "org.label-schema.vcs-url"
LABEL_SCHEMA_VCS_REF = "org.label-schema.vcs-ref"
LABEL_SCHEMA_VERSION = "org.label-schema.version"

# Field -> ordered list of label keys to check, OCI first, label-schema as fallback.
SOURCE_KEYS = (OCI_SOURCE, LABEL_SCHEMA_VCS_URL)
REVISION_KEYS = (OCI_REVISION, LABEL_SCHEMA_VCS_REF)
VERSION_KEYS = (OCI_VERSION, LABEL_SCHEMA_VERSION)
