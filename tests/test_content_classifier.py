"""Tests for ContentClassifier."""

from unittest.mock import MagicMock

import pytest
import yaml

from tg_assistant.services.content_classifier import (
    ClassificationResult,
    ContentClassifier,
    ContentType,
)

ROUTING_YAML = """\
content_routing:
  youtube:
    patterns:
      - 'youtube\\.com/watch'
      - 'youtu\\.be/'
      - 'youtube\\.com/shorts/'
    default_action: save_to_inbox
  github:
    patterns:
      - 'github\\.com/'
    default_action: save_to_inbox
  article:
    patterns:
      - 'https?://'
    exclude_patterns:
      - 'youtube\\.com'
      - 'youtu\\.be'
      - 'github\\.com'
      - 't\\.me'
    default_action: save_to_inbox
  forwarded:
    trigger: is_forwarded
    default_action: save_to_inbox
  voice:
    trigger: has_voice
    default_action: transcribe_and_save
  photo:
    trigger: has_photo
    default_action: save_to_inbox
  document:
    trigger: has_document
    default_action: save_to_inbox
  text_command:
    keywords:
      - pattern: '(?i)(задач|task)'
        action: create_task
      - pattern: '(?i)(запиши|запомни|сохрани|note|save)'
        action: save_to_inbox
"""


@pytest.fixture
def config() -> dict:
    return yaml.safe_load(ROUTING_YAML)


@pytest.fixture
def classifier(config) -> ContentClassifier:
    return ContentClassifier(config)


def _make_message(
    text: str | None = None,
    caption: str | None = None,
    forward_origin=None,
    voice=None,
    video_note=None,
    photo=None,
    document=None,
) -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.caption = caption
    msg.forward_origin = forward_origin
    msg.voice = voice
    msg.video_note = video_note
    msg.photo = photo
    msg.document = document
    return msg


class TestYouTubeClassification:
    def test_youtube_watch_url(self, classifier):
        msg = _make_message(text="Check this https://youtube.com/watch?v=abc123")
        result = classifier.classify(msg)
        assert result.content_type == ContentType.YOUTUBE
        assert "url" in result.metadata

    def test_youtube_short_url(self, classifier):
        msg = _make_message(text="https://youtu.be/abc123")
        result = classifier.classify(msg)
        assert result.content_type == ContentType.YOUTUBE

    def test_youtube_shorts_url(self, classifier):
        msg = _make_message(text="https://youtube.com/shorts/abc123")
        result = classifier.classify(msg)
        assert result.content_type == ContentType.YOUTUBE


class TestGitHubClassification:
    def test_github_url(self, classifier):
        msg = _make_message(text="https://github.com/user/repo")
        result = classifier.classify(msg)
        assert result.content_type == ContentType.GITHUB
        assert result.metadata["url"] == "https://github.com/user/repo"


class TestArticleClassification:
    def test_article_url(self, classifier):
        msg = _make_message(text="Read this: https://example.com/article")
        result = classifier.classify(msg)
        assert result.content_type == ContentType.ARTICLE_URL

    def test_youtube_excluded_from_article(self, classifier):
        msg = _make_message(text="https://youtube.com/watch?v=123")
        result = classifier.classify(msg)
        assert result.content_type == ContentType.YOUTUBE  # Not article

    def test_github_excluded_from_article(self, classifier):
        msg = _make_message(text="https://github.com/repo")
        result = classifier.classify(msg)
        assert result.content_type == ContentType.GITHUB  # Not article

    def test_telegram_link_excluded(self, classifier):
        msg = _make_message(text="https://t.me/channel/123")
        result = classifier.classify(msg)
        assert result.content_type == ContentType.TEXT_CHAT  # Not article


class TestForwardedClassification:
    def test_forwarded_message(self, classifier):
        origin = MagicMock()
        origin.date = None
        msg = _make_message(text="Hello", forward_origin=origin)
        result = classifier.classify(msg)
        assert result.content_type == ContentType.FORWARDED

    def test_forwarded_takes_priority_over_url(self, classifier):
        """Forwarded message with URL should be classified as FORWARDED, not URL."""
        origin = MagicMock()
        origin.date = None
        msg = _make_message(text="https://youtube.com/watch?v=123", forward_origin=origin)
        result = classifier.classify(msg)
        assert result.content_type == ContentType.FORWARDED


class TestMediaClassification:
    def test_voice_message(self, classifier):
        msg = _make_message(voice=MagicMock())
        result = classifier.classify(msg)
        assert result.content_type == ContentType.VOICE

    def test_video_note(self, classifier):
        msg = _make_message(video_note=MagicMock())
        result = classifier.classify(msg)
        assert result.content_type == ContentType.VOICE

    def test_photo(self, classifier):
        msg = _make_message(photo=[MagicMock()], caption="My photo")
        result = classifier.classify(msg)
        assert result.content_type == ContentType.PHOTO
        assert result.metadata["caption"] == "My photo"

    def test_document(self, classifier):
        doc = MagicMock()
        doc.file_name = "report.pdf"
        msg = _make_message(document=doc, caption="Here's the report")
        result = classifier.classify(msg)
        assert result.content_type == ContentType.DOCUMENT
        assert result.metadata["filename"] == "report.pdf"
        assert result.metadata["caption"] == "Here's the report"


class TestKeywordClassification:
    def test_task_keyword_ru(self, classifier):
        msg = _make_message(text="Создай задачу: купить молоко")
        result = classifier.classify(msg)
        assert result.content_type == ContentType.TEXT_COMMAND
        assert result.metadata["keyword_action"] == "create_task"

    def test_task_keyword_en(self, classifier):
        msg = _make_message(text="Add a task for tomorrow")
        result = classifier.classify(msg)
        assert result.content_type == ContentType.TEXT_COMMAND
        assert result.metadata["keyword_action"] == "create_task"

    def test_save_keyword(self, classifier):
        msg = _make_message(text="Запиши это как заметку")
        result = classifier.classify(msg)
        assert result.content_type == ContentType.TEXT_COMMAND
        assert result.metadata["keyword_action"] == "save_to_inbox"


class TestFallback:
    def test_plain_text_chat(self, classifier):
        msg = _make_message(text="Hello, how are you?")
        result = classifier.classify(msg)
        assert result.content_type == ContentType.TEXT_CHAT

    def test_empty_message(self, classifier):
        msg = _make_message()
        result = classifier.classify(msg)
        assert result.content_type == ContentType.TEXT_CHAT


class TestPriority:
    def test_forward_before_voice(self, classifier):
        """Forward + voice: forward wins."""
        origin = MagicMock()
        origin.date = None
        msg = _make_message(forward_origin=origin, voice=MagicMock())
        result = classifier.classify(msg)
        assert result.content_type == ContentType.FORWARDED

    def test_voice_before_photo(self, classifier):
        """Voice + photo: voice wins."""
        msg = _make_message(voice=MagicMock(), photo=[MagicMock()])
        result = classifier.classify(msg)
        assert result.content_type == ContentType.VOICE

    def test_youtube_before_article(self, classifier):
        """YouTube URL matches youtube rule, not article."""
        msg = _make_message(text="https://youtube.com/watch?v=123")
        result = classifier.classify(msg)
        assert result.content_type == ContentType.YOUTUBE
