"""Tests for the transcription service."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tg_assistant.services.transcription import TranscriptionError, TranscriptionService


@pytest.fixture
def service() -> TranscriptionService:
    return TranscriptionService(
        api_key="test-key",
        model="whisper-1",
        base_url="https://api.openai.com/v1",
    )


@pytest.fixture
def audio_file(tmp_path: Path) -> Path:
    fp = tmp_path / "test.ogg"
    fp.write_bytes(b"fake audio data")
    return fp


def _make_mock_client(response: MagicMock) -> AsyncMock:
    """Create a mock httpx.AsyncClient that returns the given response on post()."""
    client = AsyncMock()
    client.post.return_value = response
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


async def test_transcribe_success(service: TranscriptionService, audio_file: Path):
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"text": "Hello world"}

    mock_client = _make_mock_client(mock_response)

    with patch("tg_assistant.services.transcription.httpx.AsyncClient", return_value=mock_client):
        result = await service.transcribe(audio_file)

    assert result == "Hello world"
    mock_client.post.assert_called_once()
    call_kwargs = mock_client.post.call_args
    assert "audio/transcriptions" in call_kwargs[0][0]
    assert call_kwargs[1]["headers"]["Authorization"] == "Bearer test-key"


async def test_transcribe_empty_result(service: TranscriptionService, audio_file: Path):
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"text": ""}

    mock_client = _make_mock_client(mock_response)

    with patch("tg_assistant.services.transcription.httpx.AsyncClient", return_value=mock_client):
        result = await service.transcribe(audio_file)

    assert result == ""


async def test_transcribe_api_error(service: TranscriptionService, audio_file: Path):
    import httpx

    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = "Unauthorized"
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "401",
        request=MagicMock(),
        response=mock_response,
    )

    mock_client = _make_mock_client(mock_response)

    with patch("tg_assistant.services.transcription.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(TranscriptionError, match="401"):
            await service.transcribe(audio_file)


async def test_transcribe_network_error(service: TranscriptionService, audio_file: Path):
    import httpx

    mock_client = AsyncMock()
    mock_client.post.side_effect = httpx.RequestError("Connection failed")
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("tg_assistant.services.transcription.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(TranscriptionError, match="Connection failed"):
            await service.transcribe(audio_file)


def test_custom_base_url():
    service = TranscriptionService(
        api_key="key",
        model="whisper-large-v3",
        base_url="https://api.groq.com/openai/v1/",
    )
    assert service.base_url == "https://api.groq.com/openai/v1"
    assert service.model == "whisper-large-v3"
