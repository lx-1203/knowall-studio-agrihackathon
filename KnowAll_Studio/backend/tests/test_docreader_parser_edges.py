"""Boundary cases for the standard-library EPUB and MHTML readers."""

import base64
import re
import unittest
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from io import BytesIO
from zipfile import ZipFile

from app.core.parsing.docreader import epub_parser, mhtml_parser


def _epub_bytes(opf, chapter_path="OEBPS/one.xhtml", chapter_html=None, extra=None):
    if chapter_html is None:
        chapter_html = "<html><body><h1>First</h1><p>Body</p></body></html>"
    data = BytesIO()
    with ZipFile(data, "w") as archive:
        archive.writestr(
            "META-INF/container.xml",
            '<container><rootfile full-path="OEBPS/book.opf"/></container>',
        )
        archive.writestr("OEBPS/book.opf", opf)
        archive.writestr(chapter_path, chapter_html)
        for path, content in (extra or {}).items():
            archive.writestr(path, content)
    return data.getvalue()


class EpubParserEdgeTests(unittest.TestCase):
    def test_rejects_utf16_opf_with_dtd(self):
        opf = (
            '<?xml version="1.0" encoding="UTF-16"?>'
            '<!DOCTYPE package [<!ENTITY title "Expanded title">]>'
            '<package><metadata><title>&title;</title></metadata>'
            '<manifest><item id="one" href="one.xhtml" '
            'media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="one"/></spine></package>'
        ).encode("utf-16")

        with self.assertRaisesRegex(ValueError, "DTD|entity"):
            epub_parser.parse_epub(_epub_bytes(opf), extract_images=False)

    def test_accepts_utf16_opf_without_dtd(self):
        opf = (
            '<?xml version="1.0" encoding="UTF-16"?>'
            '<package><metadata><title>网络教材</title></metadata>'
            '<manifest><item id="one" href="one.xhtml" '
            'media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="one"/></spine></package>'
        ).encode("utf-16")

        parsed = epub_parser.parse_epub(_epub_bytes(opf), extract_images=False)
        self.assertEqual(parsed.metadata["title"], "网络教材")
        self.assertIn("Body", parsed.content)

    def test_percent_encoded_hash_in_chapter_filename(self):
        opf = (
            '<package><manifest><item id="one" href="chapter%23intro.xhtml" '
            'media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="one"/></spine></package>'
        )

        parsed = epub_parser.parse_epub(
            _epub_bytes(opf, chapter_path="OEBPS/chapter#intro.xhtml"),
            extract_images=False,
        )
        self.assertIn("Body", parsed.content)

    def test_same_basename_images_follow_chapter_relative_paths(self):
        opf = (
            '<package><manifest>'
            '<item id="one" href="Text/one.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="a" href="Images/A/foo.png" media-type="image/png"/>'
            '<item id="b" href="Images/B/foo.png" media-type="image/png"/>'
            '</manifest><spine><itemref idref="one"/></spine></package>'
        )
        chapter = (
            '<html><body><h1>Pictures</h1>'
            '<p><img src="../Images/A/foo.png" alt="A"/></p>'
            '<p><img src="../Images/B/foo.png" alt="B"/></p>'
            '</body></html>'
        )
        parsed = epub_parser.parse_epub(
            _epub_bytes(
                opf,
                chapter_path="OEBPS/Text/one.xhtml",
                chapter_html=chapter,
                extra={
                    "OEBPS/Images/A/foo.png": b"AIMAGE",
                    "OEBPS/Images/B/foo.png": b"BIMAGE",
                },
            )
        )
        paths = dict(re.findall(r"!\[([AB])\]\(([^)]+)\)", parsed.content))
        self.assertEqual(base64.b64decode(parsed.images[paths["A"]]), b"AIMAGE")
        self.assertEqual(base64.b64decode(parsed.images[paths["B"]]), b"BIMAGE")

    def test_deeply_nested_chapter_has_explicit_error(self):
        opf = (
            '<package><manifest><item id="one" href="one.xhtml" '
            'media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="one"/></spine></package>'
        )
        chapter = "<div>" * 1100 + "<p>Deep text</p>" + "</div>" * 1100

        with self.assertRaisesRegex(ValueError, "deeply nested"):
            epub_parser.parse_epub(_epub_bytes(opf, chapter_html=chapter), False)

    def test_deeply_nested_chapter_without_html_extension_has_explicit_error(self):
        opf = (
            '<package><manifest><item id="one" href="one.dat" '
            'media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="one"/></spine></package>'
        )
        chapter = "<div>" * 1100 + "<p>Deep text</p>" + "</div>" * 1100

        with self.assertRaisesRegex(ValueError, "deeply nested"):
            epub_parser.parse_epub(
                _epub_bytes(opf, chapter_path="OEBPS/one.dat", chapter_html=chapter),
                False,
            )


class MhtmlParserEdgeTests(unittest.TestCase):
    def test_related_start_selects_root_html(self):
        message = MIMEMultipart("related", start="<root@example.test>")
        main = MIMEText("<html><body><h1>Main article</h1></body></html>", "html", "utf-8")
        main.add_header("Content-ID", "<root@example.test>")
        frame = MIMEText(
            "<html><body><h1>Embedded frame</h1><p>" + "unrelated" * 100 + "</p></body></html>",
            "html",
            "utf-8",
        )
        frame.add_header("Content-ID", "<frame@example.test>")
        message.attach(main)
        message.attach(frame)

        parsed = mhtml_parser.parse_mhtml(message.as_bytes(), extract_images=False)
        self.assertIn("Main article", parsed.content)
        self.assertNotIn("Embedded frame", parsed.content)

    def test_deeply_nested_html_has_explicit_error(self):
        html = "<div>" * 1100 + "<p>Deep text</p>" + "</div>" * 1100
        message = MIMEText(html, "html", "utf-8")

        with self.assertRaisesRegex(ValueError, "deeply nested"):
            mhtml_parser.parse_mhtml(message.as_bytes(), extract_images=False)

    def test_near_recursion_limit_html_has_explicit_error(self):
        for depth in (240, 250, 254, 255):
            with self.subTest(depth=depth):
                html = "<div>" * depth + "Deep text" + "</div>" * depth
                message = MIMEText(html, "html", "utf-8")
                with self.assertRaisesRegex(ValueError, "deeply nested"):
                    mhtml_parser.parse_mhtml(message.as_bytes(), extract_images=False)


if __name__ == "__main__":
    unittest.main()
