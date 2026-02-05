"""Tests for message formatting and splitting."""

from tg_assistant.services.message_formatter import (
    format_for_telegram,
    split_message,
)


class TestSplitMessage:
    def test_short_message_single_chunk(self):
        assert split_message("hello") == ["hello"]

    def test_empty_message(self):
        assert split_message("") == [""]

    def test_exact_limit(self):
        text = "a" * 4096
        assert split_message(text) == [text]

    def test_split_at_paragraph(self):
        text = "a" * 3000 + "\n\n" + "b" * 3000
        chunks = split_message(text)
        assert len(chunks) == 2
        assert chunks[0] == "a" * 3000
        assert chunks[1] == "b" * 3000

    def test_split_at_newline(self):
        text = "a" * 3000 + "\n" + "b" * 3000
        chunks = split_message(text)
        assert len(chunks) == 2
        assert chunks[0] == "a" * 3000

    def test_split_at_space(self):
        text = "a" * 3000 + " " + "b" * 3000
        chunks = split_message(text)
        assert len(chunks) == 2

    def test_hard_cut_no_break_point(self):
        text = "a" * 5000
        chunks = split_message(text)
        assert len(chunks) == 2
        assert len(chunks[0]) == 4096
        assert chunks[0] + chunks[1] == text

    def test_multiple_splits(self):
        text = "a" * 10000
        chunks = split_message(text)
        assert len(chunks) == 3
        assert "".join(chunks) == text

    def test_custom_max_length(self):
        text = "abc def ghi"
        chunks = split_message(text, max_length=8)
        assert len(chunks) == 2
        assert chunks[0] == "abc def"
        assert chunks[1] == "ghi"


class TestFormatForTelegram:
    def test_plain_text(self):
        text, mode = format_for_telegram("Hello world")
        assert mode == "HTML"
        assert text == "Hello world"

    def test_bold_conversion(self):
        text, mode = format_for_telegram("**bold text**")
        assert mode == "HTML"
        assert "<b>bold text</b>" in text

    def test_inline_code_conversion(self):
        text, mode = format_for_telegram("use `print()` function")
        assert mode == "HTML"
        assert "<code>print()</code>" in text

    def test_code_block_conversion(self):
        text, mode = format_for_telegram("```python\nprint('hi')\n```")
        assert mode == "HTML"
        assert "<pre" in text
        assert "print(&#x27;hi&#x27;)" in text or "print('hi')" in text

    def test_html_escaping(self):
        text, mode = format_for_telegram("a < b & c > d")
        assert mode == "HTML"
        assert "&lt;" in text
        assert "&amp;" in text
        assert "&gt;" in text

    def test_italic_conversion(self):
        text, mode = format_for_telegram("this is _italic_ text")
        assert mode == "HTML"
        assert "<i>italic</i>" in text

    def test_snake_case_not_italic(self):
        text, mode = format_for_telegram("use some_variable_name here")
        assert mode == "HTML"
        # snake_case should NOT be converted to italic
        assert "<i>" not in text
