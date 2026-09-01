"""Every Claude call must carry the workspace header when the key needs one.

An identity-linked Anthropic API key is not bound to a single workspace, so
the API refuses to guess which one a request acts in and returns 400 on every
call. In August 2026 that took down every Claude call on the platform — recaps,
briefs, digests, the Ask surface — and it took three days to find, because
nineteen modules each built their own SDK client and there was no single place
to look, let alone fix.

Client construction now lives in `skills.llm_gateway.anthropic_client`. These
tests hold that line: the header is sent when configured, omitted when not, and
no module goes back to building its own client.
"""

from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from skills import llm_gateway  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SERVER = REPO / "webhook-server"


class TheWorkspaceHeader(unittest.TestCase):
    def test_it_is_sent_when_the_key_is_identity_linked(self):
        with mock.patch.object(llm_gateway, "workspace_headers",
                               return_value={"anthropic-workspace-id": "ws-123"}), \
                mock.patch("anthropic.Anthropic") as ctor:
            llm_gateway.anthropic_client()
        self.assertEqual(ctor.call_args.kwargs["default_headers"],
                         {"anthropic-workspace-id": "ws-123"})

    def test_an_ordinary_workspace_key_sends_nothing_extra(self):
        """Sending the header with a workspace-scoped key would be wrong."""
        with mock.patch.object(llm_gateway, "workspace_headers", return_value={}), \
                mock.patch("anthropic.Anthropic") as ctor:
            llm_gateway.anthropic_client()
        self.assertIsNone(ctor.call_args.kwargs["default_headers"])

    def test_config_drives_it_and_blank_means_absent(self):
        import config
        for value, expected in (("  ws-9  ", {"anthropic-workspace-id": "ws-9"}),
                                ("", {}), ("   ", {})):
            with mock.patch.object(config, "ANTHROPIC_WORKSPACE_ID", value):
                self.assertEqual(llm_gateway.workspace_headers(), expected)

    def test_a_caller_can_add_headers_without_dropping_the_workspace_one(self):
        with mock.patch.object(llm_gateway, "workspace_headers",
                               return_value={"anthropic-workspace-id": "ws-123"}), \
                mock.patch("anthropic.Anthropic") as ctor:
            llm_gateway.anthropic_client(default_headers={"x-trace": "abc"})
        self.assertEqual(ctor.call_args.kwargs["default_headers"],
                         {"anthropic-workspace-id": "ws-123", "x-trace": "abc"})


class NoModuleBuildsItsOwnClient(unittest.TestCase):
    """The regression that made the outage take three days to find."""

    def test_the_gateway_is_the_only_place_that_constructs_one(self):
        offenders = []
        for path in SERVER.rglob("*.py"):
            if path.name == "llm_gateway.py":
                continue
            for i, line in enumerate(path.read_text().splitlines(), 1):
                if "anthropic.Anthropic(" in line and not line.lstrip().startswith("#"):
                    offenders.append(f"{path.relative_to(REPO)}:{i}")
        self.assertEqual(offenders, [], "construct clients via llm_gateway.anthropic_client()")

    def test_hand_rolled_http_callers_send_the_header_too(self):
        """Two modules POST to the API directly and do not get the fix for free."""
        for rel in ("kb_writer.py", "services/fluency_ingestion/url_scraper.py"):
            text = (SERVER / rel).read_text()
            self.assertIn("api.anthropic.com", text, rel)
            self.assertIn("_workspace_headers()", text,
                          f"{rel} POSTs to Anthropic without the workspace header")

    def test_every_anthropic_caller_is_covered_by_one_of_the_two_paths(self):
        """A new module calling Anthropic some third way would slip past both."""
        missed = []
        for path in SERVER.rglob("*.py"):
            text = path.read_text()
            if "api.anthropic.com" not in text and "import anthropic" not in text:
                continue
            if path.name == "llm_gateway.py":
                continue
            if "llm_gateway" in text or "_workspace_headers()" in text:
                continue
            # A module that only names an exception type is not a caller.
            if re.search(r"anthropic\.(Anthropic|messages)", text):
                missed.append(str(path.relative_to(REPO)))
        self.assertEqual(missed, [])


if __name__ == "__main__":
    unittest.main()
