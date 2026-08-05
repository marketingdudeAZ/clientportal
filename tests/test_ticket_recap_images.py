"""Screenshots in ClickUp tickets must survive into the recap PDF.

Covers: image extraction from comment block arrays (where pasted screenshots
actually live — comment_text carries no trace of them), the download helper's
token-leak guard (API key only ever sent to clickup.com hosts), and an
end-to-end PDF build embedding a real image with the network mocked out.
"""

from __future__ import annotations

import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

import clickup_client  # noqa: E402
import ticket_recap_pdf  # noqa: E402


def _png_bytes(w=60, h=40, color=(200, 30, 30)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


class CommentImageExtraction(unittest.TestCase):
    def test_screenshot_block_extracted(self):
        raw = {
            "id": "c1", "user": {"username": "amy"}, "comment_text": "before/after below",
            "comment": [
                {"text": "before/after below"},
                {"type": "attachment",
                 "attachment": {"url": "https://t.clickup-attachments.com/x/shot.png",
                                "title": "shot.png", "extension": "png"}},
            ],
        }
        shaped = clickup_client._shape_comment(raw)
        self.assertEqual(len(shaped["images"]), 1)
        self.assertEqual(shaped["images"][0]["title"], "shot.png")
        self.assertEqual(shaped["images"][0]["extension"], "png")

    def test_attachment_under_attributes_shape(self):
        raw = {"comment": [{"attributes": {"attachment": {
            "url": "https://t.clickup-attachments.com/x/a.jpeg", "title": "a.jpeg",
            "extension": "jpeg"}}}]}
        shaped = clickup_client._shape_comment(raw)
        self.assertEqual(len(shaped["images"]), 1)

    def test_non_image_attachment_ignored(self):
        raw = {"comment": [{"type": "attachment", "attachment": {
            "url": "https://t.clickup-attachments.com/x/report.pdf",
            "title": "report.pdf", "extension": "pdf"}}]}
        self.assertEqual(clickup_client._shape_comment(raw)["images"], [])

    def test_extension_inferred_from_url_when_missing(self):
        att = {"url": "https://t.clickup-attachments.com/x/grab.PNG?sig=abc"}
        self.assertEqual(clickup_client._attachment_ext(att), "png")

    def test_no_blocks_no_images(self):
        self.assertEqual(clickup_client._shape_comment({"comment_text": "hi"})["images"], [])


class DownloadAuthGuard(unittest.TestCase):
    """The API token must go to clickup.com hosts ONLY."""

    def _resp(self):
        r = mock.Mock()
        r.status_code = 200
        r.iter_content = lambda chunk_size: iter([b"data"])
        return r

    def test_token_sent_to_clickup_host(self):
        with mock.patch.object(clickup_client.requests, "get",
                               return_value=self._resp()) as g, \
             mock.patch.object(clickup_client, "CLICKUP_API_KEY", "pk_test"):
            clickup_client.download_attachment("https://api.clickup.com/f/1.png")
            self.assertEqual(g.call_args.kwargs["headers"].get("Authorization"), "pk_test")

    def test_token_not_sent_to_other_hosts(self):
        for url in ("https://t.clickup-attachments.com/f/1.png",
                    "https://evil.example.com/clickup.com/f.png",
                    "https://notclickup.com/f.png"):
            with mock.patch.object(clickup_client.requests, "get",
                                   return_value=self._resp()) as g, \
                 mock.patch.object(clickup_client, "CLICKUP_API_KEY", "pk_test"):
                clickup_client.download_attachment(url)
                self.assertNotIn("Authorization", g.call_args.kwargs["headers"], url)

    def test_oversize_download_rejected(self):
        r = mock.Mock()
        r.status_code = 200
        r.iter_content = lambda chunk_size: iter([b"x" * 1024] * 3)
        with mock.patch.object(clickup_client.requests, "get", return_value=r):
            self.assertIsNone(
                clickup_client.download_attachment("https://a.b/c.png", max_bytes=2048))


class PdfEmbedding(unittest.TestCase):
    TASK = {
        "name": "Budget update — Kyle Lyle Apartments",
        "status": {"status": "complete", "type": "closed"},
        "creator": {"username": "amy"},
        "assignees": [{"username": "jo"}],
        "date_created": "1753500000000", "date_done": "1753600000000",
        "custom_fields": [], "tags": [],
    }

    def _build(self, comments, task=None, download=None):
        with mock.patch.object(clickup_client, "download_attachment",
                               side_effect=(download or (lambda url, **kw: _png_bytes()))):
            return ticket_recap_pdf.build_recap_pdf(
                task or self.TASK, comments, "We increased your paid search budget.",
                "https://app.clickup.com/t/abc")

    def test_comment_screenshot_embedded(self):
        comments = [{"author": "amy", "text": "screenshot attached", "date": "1753550000000",
                     "replies": [], "images": [
                         {"url": "https://t.clickup-attachments.com/x/s.png",
                          "title": "s.png", "extension": "png"}]}]
        with_img = self._build(comments)
        without = self._build([{**comments[0], "images": []}])
        self.assertIsNotNone(with_img)
        # the embedded JPEG makes the PDF strictly larger than the text-only build
        self.assertGreater(len(with_img), len(without))

    def test_image_only_comment_not_dropped(self):
        comments = [{"author": "amy", "text": "", "date": "1753550000000", "replies": [],
                     "images": [{"url": "https://t.clickup-attachments.com/x/s.png",
                                 "title": "s.png", "extension": "png"}]}]
        self.assertIsNotNone(self._build(comments))

    def test_failed_download_degrades_to_link_not_crash(self):
        comments = [{"author": "amy", "text": "shot", "date": "1753550000000", "replies": [],
                     "images": [{"url": "https://t.clickup-attachments.com/x/s.png",
                                 "title": "s.png", "extension": "png"}]}]
        pdf = self._build(comments, download=lambda url, **kw: None)
        self.assertIsNotNone(pdf)

    def test_task_attachment_section_and_dedupe(self):
        url = "https://t.clickup-attachments.com/x/s.png"
        task = {**self.TASK, "attachments": [
            {"url": url, "title": "s.png", "extension": "png"},
            {"url": "https://t.clickup-attachments.com/x/fresh.png",
             "title": "fresh.png", "extension": "png"},
            {"url": "https://t.clickup-attachments.com/x/data.csv",
             "title": "data.csv", "extension": "csv"},
        ]}
        calls = []

        def track(u, **kw):
            calls.append(u)
            return _png_bytes()

        comments = [{"author": "amy", "text": "shot", "date": "1753550000000", "replies": [],
                     "images": [{"url": url, "title": "s.png", "extension": "png"}]}]
        pdf = self._build(comments, task=task, download=track)
        self.assertIsNotNone(pdf)
        # the comment screenshot downloads once; the mirrored task copy is deduped
        self.assertEqual(calls.count(url), 1)
        self.assertIn("https://t.clickup-attachments.com/x/fresh.png", calls)
        # csv is never downloaded — listed as a link only
        self.assertNotIn("https://t.clickup-attachments.com/x/data.csv", calls)

    def test_embed_cap_stops_downloads(self):
        imgs = [{"url": f"https://t.clickup-attachments.com/x/{i}.png",
                 "title": f"{i}.png", "extension": "png"}
                for i in range(ticket_recap_pdf.MAX_EMBEDDED_IMAGES + 5)]
        comments = [{"author": "amy", "text": "many", "date": "1753550000000",
                     "replies": [], "images": imgs}]
        calls = []

        def track(u, **kw):
            calls.append(u)
            return _png_bytes()

        pdf = self._build(comments, download=track)
        self.assertIsNotNone(pdf)
        self.assertEqual(len(calls), ticket_recap_pdf.MAX_EMBEDDED_IMAGES)


if __name__ == "__main__":
    unittest.main()
