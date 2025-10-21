"""Reusable helpers for styling Django admin list displays."""

from __future__ import annotations

from typing import Optional

from django.utils.html import format_html

ELLIPSIS = "..."


def truncate_text(text: Optional[str], length: int, *, suffix: str = ELLIPSIS) -> str:
    """Return ``text`` shortened to ``length`` characters, adding ``suffix`` when truncated."""
    if not text:
        return ""
    text = text.strip()
    if len(text) <= length:
        return text
    return f"{text[:length].rstrip()}{suffix}"


def render_span(
    content: str,
    *,
    title: Optional[str] = None,
    color: Optional[str] = None,
    font_size: Optional[str] = None,
    italic: bool = False,
) -> str:
    """Render a ``<span>`` element with optional styling and tooltip."""
    styles = []
    if color:
        styles.append(f"color: {color}")
    if font_size:
        styles.append(f"font-size: {font_size}")
    if italic:
        styles.append("font-style: italic")

    style_attr = format_html(' style="{}"', "; ".join(styles)) if styles else ""
    title_attr = format_html(' title="{}"', title) if title else ""

    return format_html('<span{}{}>{}</span>', style_attr, title_attr, content)


def render_tooltip_span(full_text: str, preview_text: str, *, color: Optional[str] = None) -> str:
    """Render a tooltip span showing ``preview_text`` while storing ``full_text`` in the title."""
    return render_span(preview_text, title=full_text, color=color)


def render_feedback_icon(feedback_type: Optional[str]) -> str:
    """Render a coloured emoji indicator for a feedback type string."""
    feedback_styles = {
        "like": {"emoji": "👍", "color": "#16a34a"},
        "dislike": {"emoji": "👎", "color": "#dc2626"},
    }
    if not feedback_type or feedback_type not in feedback_styles:
        return render_span("—", color="#6b7280")

    config = feedback_styles[feedback_type]
    return render_span(config["emoji"], color=config["color"], font_size="18px")


def render_empty_placeholder(text: str = "No text") -> str:
    """Render a muted italic placeholder span."""
    return render_span(text, color="#6b7280", italic=True)


def render_link(url: str, text: str, *, title: Optional[str] = None) -> str:
    """Render an anchor element with optional tooltip."""
    title_attr = format_html(' title="{}"', title) if title else ""
    return format_html('<a href="{}"{}>{}</a>', url, title_attr, text)


def render_status_badge(label: str, *, color: str, emoji: Optional[str] = None) -> str:
    """Render a pill-style badge with consistent styling for status indicators."""
    content = f"{emoji} {label}".strip() if emoji else label
    return format_html(
        '<span style="color: {color}; border: 1px solid {color}; padding: 2px 8px; '
        'border-radius: 4px; font-size: 11px; font-weight: 500;">{content}</span>',
        color=color,
        content=content,
    )


def render_code_block(text: str) -> str:
    """
    Render a code-style block with monospace font and background.

    Uses CSS variables that automatically adapt to Django admin's light/dark theme.
    """
    return format_html(
        '<code style="background-color: var(--darkened-bg); color: var(--body-fg); '
        'padding: 2px 6px; border-radius: 3px; font-family: monospace;">{}</code>',
        text,
    )
