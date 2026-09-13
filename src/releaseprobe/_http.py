"""Internal HTTP helpers shared by the registry and changelog clients."""

from __future__ import annotations

import re
import urllib.parse

_LINK_NEXT = re.compile(r'<([^>]+)>\s*;\s*rel="next"')


def next_page_url(link_header: str | None, current_url: str) -> str | None:
    """Extract the `rel="next"` target from an RFC 5988 `Link` header, if any."""
    if not link_header:
        return None
    match = _LINK_NEXT.search(link_header)
    if not match:
        return None
    return str(urllib.parse.urljoin(current_url, match.group(1)))
