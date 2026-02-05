"""Direct file writer for Obsidian vault inbox."""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def _slugify(text: str, max_length: int = 50) -> str:
    """Create a filesystem-safe slug from text."""
    # Transliterate basic Cyrillic
    _cyr = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
    lowered = text.lower()
    transliterated = "".join(_cyr.get(c, c) for c in lowered)

    # Normalize unicode, keep only ascii alphanumeric and spaces/hyphens
    normalized = unicodedata.normalize("NFKD", transliterated)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")

    # Replace non-alphanum with hyphens, collapse multiples, strip edges
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)

    if not slug:
        slug = "note"

    return slug[:max_length].rstrip("-")


class InboxWriter:
    """Write markdown files to Obsidian vault inbox."""

    def __init__(self, vault_path: str, inbox_subdir: str = "00_Inbox") -> None:
        self._vault = Path(vault_path)
        self._inbox = self._vault / inbox_subdir

    async def save(
        self,
        content: str,
        metadata: dict[str, str],
        content_type: str,
    ) -> Path:
        """Save content as a markdown file in the inbox.

        Returns the path of the created file.
        """
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M")

        # Build a title from metadata or content
        title = self._derive_title(content, metadata, content_type)
        slug = _slugify(title)
        filename = f"{date_str}_{slug}.md"
        filepath = self._inbox / filename

        # Avoid overwriting — append timestamp suffix if exists
        if filepath.exists():
            ts = now.strftime("%H%M%S")
            filename = f"{date_str}_{slug}_{ts}.md"
            filepath = self._inbox / filename

        # Build markdown with YAML frontmatter
        frontmatter_lines = [
            "---",
            f"type: {content_type}",
            f"date: {date_str} {time_str}",
        ]
        if "source" in metadata:
            frontmatter_lines.append(f"source: {metadata['source']}")
        if "sender" in metadata:
            frontmatter_lines.append(f"sender: {metadata['sender']}")
        if "url" in metadata:
            frontmatter_lines.append(f"url: {metadata['url']}")
        if "filename" in metadata:
            frontmatter_lines.append(f"filename: {metadata['filename']}")
        frontmatter_lines.append(f"tags: [inbox, {content_type}]")
        frontmatter_lines.append("---")
        frontmatter_lines.append("")

        # Title and content
        frontmatter_lines.append(f"# {title}")
        frontmatter_lines.append("")
        frontmatter_lines.append(content)
        frontmatter_lines.append("")

        md_content = "\n".join(frontmatter_lines)

        # Write file (ensure directory exists)
        await asyncio.to_thread(self._write_file, filepath, md_content)
        logger.info("Saved to inbox: %s", filepath)
        return filepath

    @staticmethod
    def _write_file(filepath: Path, content: str) -> None:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content, encoding="utf-8")

    @staticmethod
    def _derive_title(content: str, metadata: dict[str, str], content_type: str) -> str:
        """Derive a short title for the inbox note."""
        if "url" in metadata:
            return metadata["url"].split("//")[-1][:60]
        if "filename" in metadata:
            return metadata["filename"]
        if "sender" in metadata and metadata["sender"] != "Unknown":
            prefix = f"From {metadata['sender']}"
            snippet = (content[:40] + "...") if len(content) > 40 else content
            return f"{prefix}: {snippet}"
        # Use first line of content
        first_line = content.split("\n", 1)[0].strip()
        if first_line:
            return (first_line[:60] + "...") if len(first_line) > 60 else first_line
        return f"{content_type} note"
