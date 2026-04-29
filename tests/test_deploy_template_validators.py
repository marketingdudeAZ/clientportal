"""Smoke tests for scripts/deploy_template.py HubL validators.

These guard the live HubSpot deploy. The hotfix on 2026-04-28 (rogue
`{% else %}` inside an HTML comment scrambling the branch structure)
motivated find_hubl_directives_in_html_comments. This test exercises
both the new check and the pre-existing IIFE-in-IF-branch check, plus
runs both against the real template to ensure it stays clean.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


def _load_validators():
    # Import lazily and skip the dotenv side-effects in deploy_template
    import importlib.util
    here = os.path.dirname(__file__)
    spec = importlib.util.spec_from_file_location(
        "deploy_template",
        os.path.join(here, "..", "scripts", "deploy_template.py"),
    )
    # The module reads HUBSPOT_API_KEY at import time; provide a dummy so
    # the SystemExit branch doesn't fire during tests.
    os.environ.setdefault("HUBSPOT_API_KEY", "test")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestHublInCommentDetector(unittest.TestCase):
    def setUp(self):
        self.mod = _load_validators()

    def test_clean_source_has_no_hits(self):
        source = (
            "<html>\n"
            "  <!-- A perfectly fine comment about SEO -->\n"
            "  {% if x %}A{% else %}B{% endif %}\n"
            "</html>"
        )
        self.assertEqual(self.mod.find_hubl_directives_in_html_comments(source), [])

    def test_else_inside_comment_is_detected(self):
        source = (
            "{% if uuid %}\n"
            "  hi\n"
            "  <!-- mirror lives in the {% else %} portfolio branch -->\n"
            "{% else %}\n"
            "  bye\n"
            "{% endif %}\n"
        )
        hits = self.mod.find_hubl_directives_in_html_comments(source)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][0], 3)  # line number
        self.assertIn("{% else %}", hits[0][1])

    def test_if_inside_comment_is_detected(self):
        source = "<!-- todo: handle the {% if foo %} case later -->\n"
        hits = self.mod.find_hubl_directives_in_html_comments(source)
        self.assertEqual(len(hits), 1)

    def test_endif_inside_comment_is_detected(self):
        source = "<!-- {% endif %} -->\n"
        hits = self.mod.find_hubl_directives_in_html_comments(source)
        self.assertEqual(len(hits), 1)

    def test_validate_hubl_structure_fails_on_rogue_else(self):
        # Minimal source with seoCheckEntitlement in the IF branch but a
        # rogue {% else %} written inside an HTML comment.
        source = (
            "{% if uuid_param %}\n"
            "  function seoCheckEntitlement(){}\n"
            "  <!-- the {% else %} branch lives below -->\n"
            "{% else %}\n"
            "  fallback\n"
            "{% endif %}"
        )
        ok, err = self.mod.validate_hubl_structure(source)
        self.assertFalse(ok)
        self.assertIn("HTML comment", err)

    def test_validate_hubl_structure_passes_on_clean_template(self):
        source = (
            "{% if uuid_param %}\n"
            "  <!-- happy property-branch comment -->\n"
            "  function seoCheckEntitlement(){}\n"
            "{% else %}\n"
            "  fallback\n"
            "{% endif %}"
        )
        ok, err = self.mod.validate_hubl_structure(source)
        self.assertTrue(ok, msg=f"validator returned err={err!r}")


class TestRealTemplateIsClean(unittest.TestCase):
    """The live template must always pass both validators."""

    def setUp(self):
        self.mod = _load_validators()
        path = os.path.join(
            os.path.dirname(__file__), "..",
            "hubspot-cms", "templates", "client-portal.html",
        )
        with open(path, encoding="utf-8") as f:
            self.source = f.read()

    def test_no_hubl_in_html_comments(self):
        hits = self.mod.find_hubl_directives_in_html_comments(self.source)
        self.assertEqual(
            hits, [],
            msg="HubL directive(s) found in HTML comments — see hotfix PR #8 "
                "for the failure mode this catches.",
        )

    def test_validate_hubl_structure_passes(self):
        ok, err = self.mod.validate_hubl_structure(self.source)
        self.assertTrue(ok, msg=f"validator failed: {err}")


if __name__ == "__main__":
    unittest.main()
