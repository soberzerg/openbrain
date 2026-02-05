"""Speech-to-text transcription via OpenAI Whisper API."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


class TranscriptionError(Exception):
    """Raised when transcription fails."""


class TranscriptionService:
    """Async wrapper for OpenAI-compatible Whisper API."""

    def __init__(
        self,
        api_key: str,
        model: str = "whisper-1",
        base_url: str = "https://api.openai.com/v1",
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")

    async def transcribe(self, audio_path: str | Path) -> str:
        """
        Transcribe an audio file to text.

        Args:
            audio_path: Path to audio file (.ogg, .mp3, .wav, etc.)

        Returns:
            Transcribed text.

        Raises:
            TranscriptionError: If the API call fails.
        """
        audio_path = Path(audio_path)
        url = f"{self.base_url}/audio/transcriptions"

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                with audio_path.open("rb") as f:
                    response = await client.post(
                        url,
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        files={"file": (audio_path.name, f, "audio/ogg")},
                        data={"model": self.model},
                    )
                response.raise_for_status()
                data = response.json()
                text = data.get("text", "").strip()
                logger.info(
                    "Transcription complete: %d chars from %s",
                    len(text), audio_path.name,
                )
                return text

            except httpx.HTTPStatusError as e:
                logger.error("Whisper API error %d: %s", e.response.status_code, e.response.text)
                raise TranscriptionError(
                    f"Whisper API returned {e.response.status_code}"
                ) from e
            except httpx.RequestError as e:
                logger.error("Whisper API request failed: %s", e)
                raise TranscriptionError(f"Whisper API request failed: {e}") from e
