"""Unit coverage for safe catalog documentation rendering."""

from __future__ import annotations

from syntara.plugin_platform.services.documentation_renderer import render_plugin_documentation


def test_renderer_keeps_commonmark_structure_and_https_links_only() -> None:
    rendered = render_plugin_documentation(
        "# GitHub\n\n[Docs](https://docs.example.test) [Relative](/unsafe) [Mail](mailto:unsafe@example.test)"
    )

    assert rendered == (
        '<h1>GitHub</h1>\n<p><a href="https://docs.example.test" rel="noopener noreferrer">Docs</a> '
        '<a rel="noopener noreferrer">Relative</a> <a rel="noopener noreferrer">Mail</a></p>\n'
    )


def test_renderer_treats_raw_html_and_unsafe_markdown_links_as_text() -> None:
    rendered = render_plugin_documentation(
        '<script>alert("unsafe")</script>\n\n[Unsafe](javascript:alert(1))\n\n<img src="https://images.example.test/a">'
    )

    assert "<script>" not in rendered
    assert "<img" not in rendered
    assert "javascript:" in rendered
    assert "&lt;script&gt;" in rendered
