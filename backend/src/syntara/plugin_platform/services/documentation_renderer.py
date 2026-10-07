"""Safe rendering for verified plugin documentation.

The artifact verifier proves source Markdown belongs to a digest-pinned plugin,
but publisher text is still untrusted display input. This module accepts only
bounded UTF-8 text, renders CommonMark with raw HTML disabled, and then applies
an explicit HTML allowlist before any value can be persisted or returned.
"""

from __future__ import annotations

from typing import NoReturn

import nh3
from markdown_it import MarkdownIt

from syntara.plugin_platform.exceptions import CatalogSourceConfigurationError

_MAX_SOURCE_BYTES = 1_048_576
_MAX_RENDERED_BYTES = 4 * _MAX_SOURCE_BYTES
_ALLOWED_TAGS = {
    "a",
    "blockquote",
    "br",
    "code",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "li",
    "ol",
    "p",
    "pre",
    "strong",
    "ul",
}
_ALLOWED_ATTRIBUTES = {"a": {"href", "title"}}
_MARKDOWN = MarkdownIt("commonmark", {"html": False, "linkify": False})


def render_plugin_documentation(markdown: str) -> str:
    """Render verified Markdown to bounded, allowlisted HTML.

    Remote images, raw HTML, styles, scripts, relative URLs, non-HTTPS links,
    and arbitrary attributes are not part of the output contract. The original
    Markdown is deliberately not returned or persisted by this layer.
    """
    if not isinstance(markdown, str) or "\x00" in markdown:
        _fail()
    try:
        source_bytes = markdown.encode("utf-8")
    except UnicodeEncodeError:
        _fail()
    if len(source_bytes) > _MAX_SOURCE_BYTES:
        _fail()
    rendered = _MARKDOWN.render(markdown)
    sanitized = nh3.clean(
        rendered,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        url_schemes={"https"},
        url_relative="deny",
        link_rel="noopener noreferrer",
    )
    if len(sanitized.encode("utf-8")) > _MAX_RENDERED_BYTES:
        _fail()
    return sanitized


def _fail() -> NoReturn:
    raise CatalogSourceConfigurationError
