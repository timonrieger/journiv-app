"""
Unit tests for Day One RichText parser.

Tests title extraction and richText -> Markdown conversion.
"""
import pytest
from app.data_transfer.dayone.richtext_parser import DayOneRichTextParser


class TestDayOneRichTextParser:
    """Test Day One richText parsing and conversion."""

    def test_parse_richtext_valid_json(self):
        """Test parsing valid richText JSON."""
        richtext_json = '{"contents":[{"text":"Hello"}],"meta":{"version":1}}'
        result = DayOneRichTextParser.parse_richtext(richtext_json)

        assert result is not None
        assert "contents" in result
        assert len(result["contents"]) == 1
        assert result["contents"][0]["text"] == "Hello"

    def test_parse_richtext_invalid_json(self):
        """Test parsing invalid JSON returns None."""
        result = DayOneRichTextParser.parse_richtext("{invalid json")
        assert result is None

    def test_parse_richtext_empty_string(self):
        """Test parsing empty string returns None."""
        result = DayOneRichTextParser.parse_richtext("")
        assert result is None

    def test_extract_title_from_header_1(self):
        """Test title extraction from header:1 block."""
        richtext = {
            "contents": [
                {
                    "attributes": {"line": {"header": 1}},
                    "text": "This is a just \n"
                },
                {
                    "attributes": {"line": {"header": 0}},
                    "text": "Body text\n"
                }
            ]
        }

        title = DayOneRichTextParser.extract_title(richtext)
        assert title == "This is a just"  # Trailing \n removed

    def test_extract_title_without_header_returns_none(self):
        """Test title extraction returns None when no header is present."""
        richtext = {
            "contents": [
                {
                    "attributes": {"line": {"header": 0}},
                    "text": "First line of text\n"
                },
                {
                    "text": "Second line\n"
                }
            ]
        }

        title = DayOneRichTextParser.extract_title(richtext)
        assert title is None

    def test_extract_title_strips_markdown(self):
        """Test title extraction strips markdown characters."""
        richtext = {
            "contents": [
                {
                    "attributes": {"line": {"header": 1}},
                    "text": "# **Bold Title** with *formatting*\n"
                }
            ]
        }

        title = DayOneRichTextParser.extract_title(richtext)
        assert title == "Bold Title with formatting"  # Markdown stripped

    def test_extract_title_truncates_to_60_chars(self):
        """Test title truncation to 60 characters."""
        long_text = "A" * 100
        richtext = {
            "contents": [
                {
                    "attributes": {"line": {"header": 1}},
                    "text": f"{long_text}\n"
                }
            ]
        }

        title = DayOneRichTextParser.extract_title(richtext)
        assert len(title) == 60
        assert title == "A" * 60

    def test_extract_title_empty_content_returns_none(self):
        """Test empty content returns None."""
        richtext = {"contents": []}
        title = DayOneRichTextParser.extract_title(richtext)
        assert title is None

    def test_extract_title_only_empty_text_returns_none(self):
        """Test only empty text blocks returns None."""
        richtext = {
            "contents": [
                {"text": "\n"},
                {"text": "   \n"}
            ]
        }
        title = DayOneRichTextParser.extract_title(richtext)
        assert title is None

    def test_convert_to_markdown_header_1(self):
        """Test converting header:1 to Markdown."""
        richtext = {
            "contents": [
                {
                    "attributes": {"line": {"header": 1}},
                    "text": "My Title\n"
                }
            ]
        }

        markdown = DayOneRichTextParser.convert_to_markdown(richtext)
        assert markdown == "# My Title"

    def test_convert_to_markdown_header_0_plain_text(self):
        """Test converting header:0 to plain text."""
        richtext = {
            "contents": [
                {
                    "attributes": {"line": {"header": 0}},
                    "text": "Plain paragraph\n"
                }
            ]
        }

        markdown = DayOneRichTextParser.convert_to_markdown(richtext)
        assert markdown == "Plain paragraph"

    def test_convert_to_markdown_mixed_headers_and_text(self):
        """Test converting mix of headers and text."""
        richtext = {
            "contents": [
                {
                    "attributes": {"line": {"header": 1}},
                    "text": "Title\n"
                },
                {
                    "attributes": {"line": {"header": 0}},
                    "text": "First paragraph\n"
                },
                {
                    "attributes": {"line": {"header": 0}},
                    "text": "Second paragraph\n"
                }
            ]
        }

        markdown = DayOneRichTextParser.convert_to_markdown(richtext)
        expected = "# Title\n\nFirst paragraph\n\nSecond paragraph"
        assert markdown == expected

    def test_convert_to_markdown_embedded_photo_without_entry_id(self):
        """Test embedded photo creates placeholder when entry_id not provided."""
        # Create mock photo object
        class MockPhoto:
            def __init__(self, identifier, md5):
                self.identifier = identifier
                self.md5 = md5

        photos = [MockPhoto("PHOTO-UUID-1", "abc123")]

        richtext = {
            "contents": [
                {
                    "attributes": {"line": {"header": 1}},
                    "text": "Photo Entry\n"
                },
                {
                    "embeddedObjects": [
                        {"identifier": "PHOTO-UUID-1", "type": "photo"}
                    ]
                }
            ]
        }

        markdown = DayOneRichTextParser.convert_to_markdown(richtext, photos=photos, entry_id=None)
        expected = "# Photo Entry\n\nDAYONE_PHOTO:abc123"
        assert markdown == expected

    def test_convert_to_markdown_embedded_photo_with_entry_id(self):
        """Test embedded photo creates proper markdown with entry_id."""
        class MockPhoto:
            def __init__(self, identifier, md5):
                self.identifier = identifier
                self.md5 = md5

        photos = [MockPhoto("PHOTO-UUID-1", "abc123")]

        richtext = {
            "contents": [
                {
                    "attributes": {"line": {"header": 1}},
                    "text": "Photo Entry\n"
                },
                {
                    "embeddedObjects": [
                        {"identifier": "PHOTO-UUID-1", "type": "photo"}
                    ]
                }
            ]
        }

        entry_id = "entry-123"
        markdown = DayOneRichTextParser.convert_to_markdown(richtext, photos=photos, entry_id=entry_id)
        expected = "# Photo Entry\n\nDAYONE_PHOTO:abc123"
        assert markdown == expected

    def test_convert_to_markdown_embedded_video_placeholder(self):
        """Test embedded video creates placeholder when video list is provided."""
        class MockVideo:
            def __init__(self, identifier, md5):
                self.identifier = identifier
                self.md5 = md5

        videos = [MockVideo("VIDEO-UUID-1", "vid123")]

        richtext = {
            "contents": [
                {
                    "text": "Video Entry\n"
                },
                {
                    "embeddedObjects": [
                        {"identifier": "VIDEO-UUID-1", "type": "video"}
                    ]
                }
            ]
        }

        markdown = DayOneRichTextParser.convert_to_markdown(richtext, videos=videos, entry_id=None)
        assert markdown == "Video Entry\n\nDAYONE_VIDEO:vid123"

    def test_convert_to_markdown_embedded_photo_not_found(self):
        """Test embedded photo not in photos list is skipped with warning."""
        richtext = {
            "contents": [
                {
                    "text": "Text\n"
                },
                {
                    "embeddedObjects": [
                        {"identifier": "MISSING-PHOTO", "type": "photo"}
                    ]
                }
            ]
        }

        # No photos provided
        markdown = DayOneRichTextParser.convert_to_markdown(richtext, photos=None, entry_id=None)
        # Photo placeholder should not appear
        assert "DAYONE_PHOTO:" not in markdown
        assert markdown == "Text"

    def test_replace_photo_placeholders(self):
        """Test replacing photo placeholders with Journiv media shortcode format."""
        content = "# Title\n\nDAYONE_PHOTO:photo1\n\nSome text\n\nDAYONE_PHOTO:photo2"
        photo_map = {
            "photo1": "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA",
            "photo2": "BBBBBBBB-BBBB-BBBB-BBBB-BBBBBBBBBBBB"
        }

        result = DayOneRichTextParser.replace_photo_placeholders(content, photo_map)

        assert "DAYONE_PHOTO:" not in result
        assert "![[media:AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA]]" in result
        assert "![[media:BBBBBBBB-BBBB-BBBB-BBBB-BBBBBBBBBBBB]]" in result

    def test_replace_photo_placeholders_missing_photo(self):
        """Test placeholder removal when photo not in map."""
        content = "# Title\n\nDAYONE_PHOTO:photo1\n\nDAYONE_PHOTO:missing"
        photo_map = {
            "photo1": "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"
        }

        result = DayOneRichTextParser.replace_photo_placeholders(content, photo_map)

        # First photo should be replaced with Journiv media shortcode format
        assert "![[media:AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA]]" in result
        # Missing photo placeholder should be removed silently
        assert "DAYONE_PHOTO:missing" not in result

    def test_replace_placeholders_with_uuid_identifier(self):
        """Test placeholders with UUIDs (hyphens) are replaced."""
        content = "DAYONE_VIDEO:CCCCCCCC-CCCC-CCCC-CCCC-CCCCCCCCCCCC"
        photo_map = {
            "CCCCCCCC-CCCC-CCCC-CCCC-CCCCCCCCCCCC": "media-test-1"
        }

        result = DayOneRichTextParser.replace_photo_placeholders(content, photo_map)
        assert result == "![[media:media-test-1]]"

    def test_real_dayone_export_example(self):
        """Test with real Day One export richText structure."""
        richtext_json = '{"contents":[{"attributes":{"line":{"header":1,"identifier":"AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"}},"text":"Sample Title \\n"},{"attributes":{"line":{"header":0,"identifier":"BBBBBBBB-BBBB-BBBB-BBBB-BBBBBBBBBBBB"}},"text":"Sample paragraph \\n"},{"embeddedObjects":[{"identifier":"CCCCCCCC-CCCC-CCCC-CCCC-CCCCCCCCCCCC","type":"photo"},{"identifier":"DDDDDDDD-DDDD-DDDD-DDDD-DDDDDDDDDDDD","type":"photo"}]}],"meta":{"created":{"platform":"com.bloombuilt.dayone-ios","version":2638},"small-lines-removed":true,"version":1}}'

        richtext = DayOneRichTextParser.parse_richtext(richtext_json)
        assert richtext is not None

        # Test title extraction
        title = DayOneRichTextParser.extract_title(richtext)
        assert title == "Sample Title"

        # Test markdown conversion
        class MockPhoto:
            def __init__(self, identifier, md5):
                self.identifier = identifier
                self.md5 = md5

        photos = [
            MockPhoto("CCCCCCCC-CCCC-CCCC-CCCC-CCCCCCCCCCCC", "photo1_md5"),
            MockPhoto("DDDDDDDD-DDDD-DDDD-DDDD-DDDDDDDDDDDD", "photo2_md5")
        ]

        markdown = DayOneRichTextParser.convert_to_markdown(richtext, photos=photos, entry_id=None)

        # Should have header
        assert "# Sample Title" in markdown
        # Should have paragraph
        assert "Sample paragraph" in markdown
        # Should have photo placeholders
        assert "DAYONE_PHOTO:photo1_md5" in markdown
        assert "DAYONE_PHOTO:photo2_md5" in markdown

    def test_strip_markdown_removes_formatting(self):
        """Test _strip_markdown removes formatting characters."""
        # Bold
        assert DayOneRichTextParser._strip_markdown("**bold**") == "bold"
        assert DayOneRichTextParser._strip_markdown("__bold__") == "bold"

        # Italic
        assert DayOneRichTextParser._strip_markdown("*italic*") == "italic"
        assert DayOneRichTextParser._strip_markdown("_italic_") == "italic"

        # Code
        assert DayOneRichTextParser._strip_markdown("`code`") == "code"

        # Headers
        assert DayOneRichTextParser._strip_markdown("# Header") == "Header"
        assert DayOneRichTextParser._strip_markdown("### Header") == "Header"

        # Mixed
        assert DayOneRichTextParser._strip_markdown("**bold** and *italic*") == "bold and italic"
