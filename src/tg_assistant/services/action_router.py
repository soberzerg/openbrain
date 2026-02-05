"""Action router: map content classification to actions and follow-up buttons."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from tg_assistant.services.content_classifier import ClassificationResult, ContentType

logger = logging.getLogger(__name__)

# Maps ContentType enum values to their config key names
_TYPE_TO_CONFIG_KEY: dict[ContentType, str] = {
    ContentType.YOUTUBE: "youtube",
    ContentType.GITHUB: "github",
    ContentType.ARTICLE_URL: "article",
    ContentType.FORWARDED: "forwarded",
    ContentType.VOICE: "voice",
    ContentType.PHOTO: "photo",
    ContentType.DOCUMENT: "document",
}


@dataclass
class FollowUpAction:
    label: str
    action_type: str  # call_claude, call_skill, create_task, save_as_note, select_project
    skill: str | None = None
    prompt_template: str | None = None


@dataclass
class RouteResult:
    default_action: str  # save_to_inbox, transcribe_and_save, chat_with_claude
    follow_up_actions: list[FollowUpAction] = field(default_factory=list)
    skill: str | None = None


class ActionRouter:
    """Resolve classification result into actions."""

    def __init__(self, routing_config: dict) -> None:
        self._config = routing_config.get("content_routing", {})

    def resolve(self, classification: ClassificationResult) -> RouteResult:
        """Map a classification to a default action and follow-up buttons."""
        # TEXT_CHAT → pass through to Claude
        if classification.content_type == ContentType.TEXT_CHAT:
            return RouteResult(default_action="chat_with_claude")

        # TEXT_COMMAND → route by keyword action
        if classification.content_type == ContentType.TEXT_COMMAND:
            keyword_action = classification.metadata.get("keyword_action", "chat_with_claude")
            return RouteResult(default_action=keyword_action)

        # Lookup config for this content type
        config_key = _TYPE_TO_CONFIG_KEY.get(classification.content_type)
        if not config_key or config_key not in self._config:
            return RouteResult(default_action="chat_with_claude")

        cfg = self._config[config_key]
        default_action = cfg.get("default_action", "save_to_inbox")

        # Build follow-up actions
        follow_ups: list[FollowUpAction] = []
        for fu in cfg.get("follow_up", []):
            follow_ups.append(
                FollowUpAction(
                    label=fu["label"],
                    action_type=fu["action"],
                    skill=fu.get("skill"),
                    prompt_template=fu.get("prompt"),
                )
            )

        return RouteResult(
            default_action=default_action,
            follow_up_actions=follow_ups,
        )
