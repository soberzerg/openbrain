"""Content classifier: determine type of incoming Telegram message."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum

from telegram import Message

logger = logging.getLogger(__name__)


class ContentType(Enum):
    YOUTUBE = "youtube"
    GITHUB = "github"
    ARTICLE_URL = "article"
    FORWARDED = "forwarded"
    VOICE = "voice"
    PHOTO = "photo"
    DOCUMENT = "document"
    TEXT_COMMAND = "text_command"
    TEXT_CHAT = "text_chat"


@dataclass
class ClassificationResult:
    content_type: ContentType
    metadata: dict[str, str] = field(default_factory=dict)


class ContentClassifier:
    """Level 1 classifier: regex + message type detection."""

    def __init__(self, routing_config: dict) -> None:
        self._config = routing_config.get("content_routing", {})
        self._url_rules = self._compile_url_rules()
        self._keyword_rules = self._compile_keyword_rules()

    def _compile_url_rules(self) -> list[tuple[str, list[re.Pattern], list[re.Pattern]]]:
        """Compile URL regex patterns from config.

        Returns list of (content_type_name, include_patterns, exclude_patterns).
        Order matters: first match wins.
        """
        rules: list[tuple[str, list[re.Pattern], list[re.Pattern]]] = []
        # Process URL-based rules in priority order
        for name in ("youtube", "github", "article"):
            cfg = self._config.get(name)
            if not cfg or "patterns" not in cfg:
                continue
            includes = [re.compile(p) for p in cfg["patterns"]]
            excludes = [re.compile(p) for p in cfg.get("exclude_patterns", [])]
            rules.append((name, includes, excludes))
        return rules

    def _compile_keyword_rules(self) -> list[tuple[re.Pattern, str]]:
        """Compile keyword detection patterns from config."""
        rules: list[tuple[re.Pattern, str]] = []
        text_cmd = self._config.get("text_command", {})
        for kw in text_cmd.get("keywords", []):
            rules.append((re.compile(kw["pattern"]), kw["action"]))
        return rules

    def classify(self, message: Message) -> ClassificationResult:
        """Classify a Telegram message into a content type.

        Priority: forwarded > voice > photo > document > URL patterns > keywords > text_chat.
        """
        # 1. Forwarded message
        if message.forward_origin is not None:
            return ClassificationResult(
                content_type=ContentType.FORWARDED,
                metadata=self._extract_forward_metadata(message),
            )

        # 2. Voice / video note
        if message.voice or message.video_note:
            return ClassificationResult(content_type=ContentType.VOICE)

        # 3. Photo
        if message.photo:
            meta: dict[str, str] = {}
            if message.caption:
                meta["caption"] = message.caption
            return ClassificationResult(content_type=ContentType.PHOTO, metadata=meta)

        # 4. Document
        if message.document:
            meta = {}
            if message.document.file_name:
                meta["filename"] = message.document.file_name
            if message.caption:
                meta["caption"] = message.caption
            return ClassificationResult(content_type=ContentType.DOCUMENT, metadata=meta)

        # 5. Text-based classification
        text = message.text or message.caption or ""
        if not text:
            return ClassificationResult(content_type=ContentType.TEXT_CHAT)

        # 5a. URL patterns
        url_result = self._classify_url(text)
        if url_result:
            return url_result

        # 5b. Keywords
        keyword_result = self._classify_keywords(text)
        if keyword_result:
            return keyword_result

        # 5c. Fallback: plain text chat
        return ClassificationResult(content_type=ContentType.TEXT_CHAT)

    def _classify_url(self, text: str) -> ClassificationResult | None:
        """Check text for URL patterns."""
        for name, includes, excludes in self._url_rules:
            # Check if any include pattern matches
            matched = any(p.search(text) for p in includes)
            if not matched:
                continue
            # Check excludes — if any exclude matches, skip this rule
            excluded = any(p.search(text) for p in excludes)
            if excluded:
                continue
            # Extract the first URL from text
            url = self._extract_url(text)
            content_type = ContentType(name)
            meta: dict[str, str] = {}
            if url:
                meta["url"] = url
            return ClassificationResult(content_type=content_type, metadata=meta)
        return None

    def _classify_keywords(self, text: str) -> ClassificationResult | None:
        """Check text for keyword patterns."""
        for pattern, action in self._keyword_rules:
            if pattern.search(text):
                return ClassificationResult(
                    content_type=ContentType.TEXT_COMMAND,
                    metadata={"keyword_action": action, "text": text},
                )
        return None

    @staticmethod
    def _extract_url(text: str) -> str | None:
        """Extract the first URL from text."""
        match = re.search(r'https?://\S+', text)
        return match.group(0) if match else None

    @staticmethod
    def _extract_forward_metadata(message: Message) -> dict[str, str]:
        """Extract metadata from forwarded message."""
        from telegram import MessageOriginChannel, MessageOriginChat, MessageOriginUser

        meta: dict[str, str] = {"sender": "Unknown", "source": "private chat"}

        origin = message.forward_origin
        if origin is None:
            return meta

        if origin.date:
            meta["date"] = origin.date.strftime("%Y-%m-%d %H:%M")

        if isinstance(origin, MessageOriginUser):
            meta["sender"] = origin.sender_user.full_name
        elif isinstance(origin, MessageOriginChat):
            meta["sender"] = origin.sender_chat.title or "Chat"
            meta["source"] = origin.sender_chat.title or "group chat"
        elif isinstance(origin, MessageOriginChannel):
            meta["sender"] = origin.chat.title or "Channel"
            meta["source"] = origin.chat.title or "channel"

        # Collect text content
        text = message.text or message.caption or ""
        if text:
            meta["text"] = text

        return meta
