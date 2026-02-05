"""Tests for InboxWriter."""

import pytest

from tg_assistant.services.inbox_writer import InboxWriter, _slugify


class TestSlugify:
    def test_english_text(self):
        assert _slugify("Hello World") == "hello-world"

    def test_cyrillic_text(self):
        assert _slugify("Привет мир") == "privet-mir"

    def test_url_text(self):
        slug = _slugify("example.com/article/123")
        assert slug == "example-com-article-123"

    def test_special_characters(self):
        slug = _slugify("Hello! @World #2024")
        assert slug == "hello-world-2024"

    def test_empty_string(self):
        assert _slugify("") == "note"

    def test_max_length(self):
        long_text = "a" * 100
        slug = _slugify(long_text, max_length=20)
        assert len(slug) <= 20

    def test_no_trailing_hyphen(self):
        slug = _slugify("hello world foo", max_length=11)
        assert not slug.endswith("-")


class TestInboxWriter:
    @pytest.fixture
    def writer(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        return InboxWriter(str(vault))

    async def test_save_creates_file(self, writer, tmp_path):
        path = await writer.save(
            content="Test content",
            metadata={"url": "https://example.com"},
            content_type="article",
        )
        assert path.exists()
        text = path.read_text()
        assert "Test content" in text
        assert "type: article" in text
        assert "url: https://example.com" in text

    async def test_save_creates_inbox_directory(self, writer, tmp_path):
        path = await writer.save(
            content="Hello",
            metadata={},
            content_type="text",
        )
        assert "00_Inbox" in str(path)
        assert path.parent.name == "00_Inbox"

    async def test_save_with_forward_metadata(self, writer):
        path = await writer.save(
            content="Forwarded text",
            metadata={"sender": "John", "source": "group chat"},
            content_type="forwarded",
        )
        text = path.read_text()
        assert "sender: John" in text
        assert "source: group chat" in text

    async def test_frontmatter_has_tags(self, writer):
        path = await writer.save(
            content="test",
            metadata={},
            content_type="youtube",
        )
        text = path.read_text()
        assert "tags: [inbox, youtube]" in text

    async def test_duplicate_filename_gets_suffix(self, writer):
        path1 = await writer.save(content="first", metadata={}, content_type="text")
        path2 = await writer.save(content="second", metadata={}, content_type="text")
        # Both files should exist and be different
        assert path1.exists()
        assert path2.exists()
        assert path1 != path2

    async def test_derive_title_from_url(self, writer):
        path = await writer.save(
            content="Check this",
            metadata={"url": "https://example.com/cool-article"},
            content_type="article",
        )
        text = path.read_text()
        assert "# example.com/cool-article" in text

    async def test_derive_title_from_filename(self, writer):
        path = await writer.save(
            content="Document content",
            metadata={"filename": "report.pdf"},
            content_type="document",
        )
        text = path.read_text()
        assert "# report.pdf" in text
