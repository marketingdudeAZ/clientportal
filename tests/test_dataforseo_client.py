"""Tests for the DataForSEO HTTP client.

We mock the session to assert URL shape, payload shape, and result unwrapping —
not to verify actual API behavior.
"""

import sys
import os
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import dataforseo_client as dfs


def _mock_response(body):
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = body
    m.raise_for_status = MagicMock()
    return m


class TestSerpOrganic(unittest.TestCase):
    @patch.object(dfs._SESSION, "post")
    def test_posts_expected_payload(self, mock_post):
        mock_post.return_value = _mock_response({
            "tasks": [{"result": [{"items": [{"rank_absolute": 4, "url": "https://example.com/a"}]}]}]
        })
        result = dfs.serp_organic_advanced("best apartments phoenix", location_code=2840, language_code="en")

        args, kwargs = mock_post.call_args
        self.assertIn("/v3/serp/google/organic/live/advanced", args[0])
        sent_body = kwargs["json"]
        self.assertIsInstance(sent_body, list)
        self.assertEqual(sent_body[0]["keyword"], "best apartments phoenix")
        self.assertEqual(sent_body[0]["location_code"], 2840)
        self.assertEqual(sent_body[0]["language_code"], "en")
        self.assertEqual(result["items"][0]["rank_absolute"], 4)


class TestBacklinksSummary(unittest.TestCase):
    @patch.object(dfs._SESSION, "post")
    def test_returns_first_result(self, mock_post):
        mock_post.return_value = _mock_response({
            "tasks": [{"result": [{"target": "example.com", "referring_domains": 42}]}]
        })
        r = dfs.backlinks_summary("example.com")
        self.assertEqual(r["referring_domains"], 42)


class TestLlmResponses(unittest.TestCase):
    @patch.object(dfs._SESSION, "post")
    def test_chatgpt_url(self, mock_post):
        mock_post.return_value = _mock_response({"tasks": [{"result": [{"items": [{"text": "ok"}]}]}]})
        dfs.llm_response_chatgpt("hello")
        args, _ = mock_post.call_args
        self.assertIn("/v3/ai_optimization/chat_gpt/llm_responses/live", args[0])

    @patch.object(dfs._SESSION, "post")
    def test_perplexity_url(self, mock_post):
        mock_post.return_value = _mock_response({"tasks": [{"result": [{"items": [{"text": "ok"}]}]}]})
        dfs.llm_response_perplexity("hello")
        args, _ = mock_post.call_args
        self.assertIn("/v3/ai_optimization/perplexity/llm_responses/live", args[0])


class TestErrorHandling(unittest.TestCase):
    @patch.object(dfs._SESSION, "post")
    def test_high_status_code_raises(self, mock_post):
        mock_post.return_value = _mock_response({
            "status_code": 40400,
            "status_message": "quota exceeded",
            "tasks": [],
        })
        with self.assertRaises(dfs.DataForSEOError):
            dfs.serp_organic_advanced("x")


class TestResultUnwrapping(unittest.TestCase):
    def test_first_result_empty_tasks(self):
        self.assertEqual(dfs._first_result({}), {})
        self.assertEqual(dfs._first_result({"tasks": []}), {})
        self.assertEqual(dfs._first_result({"tasks": [{"result": []}]}), {})


if __name__ == "__main__":
    unittest.main()
