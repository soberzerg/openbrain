"""Format Claude responses for Telegram (HTML parse mode with fallback)."""

from __future__ import annotations

import re

TELEGRAM_MAX_LENGTH = 4096


def split_message(text: str, max_length: int = TELEGRAM_MAX_LENGTH) -> list[str]:
    """
    Split a long message into chunks respecting Telegram's limit.

    Tries to split at paragraph boundaries, then line boundaries,
    then word boundaries, then hard-cuts at max_length.
    """
    if not text:
        return [""]

    if len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break

        # Try to find a good break point within the limit
        cut_point = remaining.rfind("\n\n", 0, max_length)
        if cut_point < 1:
            cut_point = remaining.rfind("\n", 0, max_length)
        if cut_point < 1:
            cut_point = remaining.rfind(" ", 0, max_length)
        if cut_point < 1:
            cut_point = max_length

        chunk = remaining[:cut_point]
        chunks.append(chunk)
        remaining = remaining[cut_point:].lstrip("\n ")

        # Safety check: ensure we're making progress
        if not chunk or len(remaining) >= len(chunk) + len(chunks) * max_length:
            # Fallback: hard cut to prevent infinite loop
            if remaining:
                chunks.append(remaining[:max_length])
                remaining = remaining[max_length:]


    return chunks


def _escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_for_telegram(text: str) -> tuple[str, str | None]:
    """
    Convert Claude's markdown response to Telegram-safe HTML.

    Returns (formatted_text, parse_mode).
    Falls back to (original_text, None) if conversion fails.
    """
    try:
        result = _markdown_to_html(text)
        return result, "HTML"
    except Exception:
        return text, None


def _markdown_to_html(text: str) -> str:
    """Convert common markdown patterns to Telegram HTML."""
    # Process fenced code blocks first (preserve content inside)
    parts: list[str] = []
    last_end = 0

    for match in re.finditer(r"```(\w*)\n(.*?)```", text, flags=re.DOTALL):
        # Process text before this code block
        before = text[last_end : match.start()]
        parts.append(_convert_inline_markdown(before))

        # Code block: escape HTML inside, wrap in <pre>
        lang = match.group(1)
        code = _escape_html(match.group(2).rstrip("\n"))
        if lang:
            parts.append(f'<pre language="{lang}">{code}</pre>')
        else:
            parts.append(f"<pre>{code}</pre>")
        last_end = match.end()

    # Process remaining text after last code block
    if last_end < len(text):
        parts.append(_convert_inline_markdown(text[last_end:]))

    return "".join(parts)


def _convert_inline_markdown(text: str) -> str:
    """Convert inline markdown (bold, italic, code) to HTML, escaping the rest."""
    # Extract inline code spans first to protect them
    segments: list[str] = []
    last_end = 0

    for match in re.finditer(r"`([^`]+)`", text):
        before = text[last_end : match.start()]
        segments.append(_convert_bold_italic(_escape_html(before)))
        segments.append(f"<code>{_escape_html(match.group(1))}</code>")
        last_end = match.end()

    if last_end < len(text):
        segments.append(_convert_bold_italic(_escape_html(text[last_end:])))

    return "".join(segments)


def _convert_bold_italic(text: str) -> str:
    """Convert **bold** and _italic_ in already-escaped HTML text."""
    # Bold: **text** (use non-greedy match)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    # Italic: _text_ (word boundary to avoid matching snake_case)
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"<i>\1</i>", text)
    return text
