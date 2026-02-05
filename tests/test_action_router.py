"""Tests for ActionRouter."""

import pytest
import yaml

from tg_assistant.services.action_router import ActionRouter
from tg_assistant.services.content_classifier import ClassificationResult, ContentType

ROUTING_YAML = """\
content_routing:
  youtube:
    default_action: save_to_inbox
    follow_up:
      - label: "YouTube саммари"
        action: call_skill
        skill: youtube-summary
      - label: "Создать задачу"
        action: create_task
  github:
    default_action: save_to_inbox
    follow_up:
      - label: "Проанализировать"
        action: call_claude
        prompt: "Analyze this: {url}"
  article:
    default_action: save_to_inbox
    follow_up:
      - label: "Полный саммари"
        action: call_claude
        prompt: "Summarize: {url}"
  forwarded:
    default_action: save_to_inbox
    follow_up:
      - label: "Создать задачу"
        action: create_task
  voice:
    default_action: transcribe_and_save
    follow_up:
      - label: "В заметку"
        action: save_as_note
  photo:
    default_action: save_to_inbox
    follow_up: []
  document:
    default_action: save_to_inbox
    follow_up: []
"""


@pytest.fixture
def config() -> dict:
    return yaml.safe_load(ROUTING_YAML)


@pytest.fixture
def router(config) -> ActionRouter:
    return ActionRouter(config)


class TestActionRouter:
    def test_text_chat_passthrough(self, router):
        result = router.resolve(ClassificationResult(ContentType.TEXT_CHAT))
        assert result.default_action == "chat_with_claude"
        assert result.follow_up_actions == []

    def test_youtube_route(self, router):
        result = router.resolve(
            ClassificationResult(ContentType.YOUTUBE, {"url": "https://youtube.com/watch?v=1"})
        )
        assert result.default_action == "save_to_inbox"
        assert len(result.follow_up_actions) == 2
        assert result.follow_up_actions[0].action_type == "call_skill"
        assert result.follow_up_actions[0].skill == "youtube-summary"

    def test_github_route(self, router):
        result = router.resolve(
            ClassificationResult(ContentType.GITHUB, {"url": "https://github.com/user/repo"})
        )
        assert result.default_action == "save_to_inbox"
        assert len(result.follow_up_actions) == 1
        assert result.follow_up_actions[0].prompt_template == "Analyze this: {url}"

    def test_forwarded_route(self, router):
        result = router.resolve(ClassificationResult(ContentType.FORWARDED))
        assert result.default_action == "save_to_inbox"
        assert result.follow_up_actions[0].action_type == "create_task"

    def test_voice_route(self, router):
        result = router.resolve(ClassificationResult(ContentType.VOICE))
        assert result.default_action == "transcribe_and_save"
        assert result.follow_up_actions[0].action_type == "save_as_note"

    def test_text_command_route(self, router):
        result = router.resolve(
            ClassificationResult(ContentType.TEXT_COMMAND, {"keyword_action": "create_task"})
        )
        assert result.default_action == "create_task"

    def test_article_route(self, router):
        result = router.resolve(
            ClassificationResult(ContentType.ARTICLE_URL, {"url": "https://example.com"})
        )
        assert result.default_action == "save_to_inbox"
        assert len(result.follow_up_actions) == 1

    def test_photo_empty_follow_up(self, router):
        result = router.resolve(ClassificationResult(ContentType.PHOTO))
        assert result.default_action == "save_to_inbox"
        assert result.follow_up_actions == []
