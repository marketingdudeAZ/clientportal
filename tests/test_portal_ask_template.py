"""Guards for the Ask UI inside the HubSpot template.

Two things this catches that nothing else does.

**Trap 4.** `client-portal.html` is one file with a `{% if uuid_param %}` branch
holding the real portal and an `{% else %}` branch holding the signed-out page.
Markup or JS appended at the end of the file lands in `{% else %}`, uploads
without complaint, renders nothing, and the CDN then caches that for hours.
The tests below assert every piece of the Ask surface sits above `{% else %}`.

**The render path.** The template's JS is never exercised by pytest, so the way
it reads an API payload can drift from the payload without a single test going
red. `test_render_harness_against_a_real_answer` drives the actual extracted
code in node against a captured production response.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "hubspot-cms" / "templates" / "client-portal.html"
HARNESS = REPO / "tests" / "js" / "ask_render_harness.js"
ANSWER_FIXTURE = REPO / "tests" / "fixtures" / "ask_live_answer.json"
MANIFEST_FIXTURE = REPO / "tests" / "fixtures" / "ask_manifest.json"


@pytest.fixture(scope="module")
def template() -> str:
    return TEMPLATE.read_text()


@pytest.fixture(scope="module")
def else_line(template) -> int:
    """Line number of the {% else %} that ends the live branch."""
    lines = template.split("\n")
    hits = [i for i, l in enumerate(lines, 1) if l.strip() == "{% else %}"]
    assert len(hits) == 1, f"expected exactly one top-level else, found {hits}"
    return hits[0]


def _line_of(template: str, needle: str) -> int:
    for i, line in enumerate(template.split("\n"), 1):
        if needle in line:
            return i
    raise AssertionError(f"not found in the template: {needle!r}")


@pytest.mark.parametrize("what,needle", [
    ("nav item",     "nav('ask',this)"),
    ("section",      'id="section-ask"'),
    ("nav hook",     "if (id === 'ask')"),
    ("loadAsk",      "window.loadAsk = function()"),
    ("askRun",       "window.askRun = function("),
    ("stylesheet",   ".ask-chip{"),
])
def test_every_piece_is_inside_the_live_branch(template, else_line, what, needle):
    """DEPLOY.md Trap 4. Below {% else %} this code is dead on the live page."""
    at = _line_of(template, needle)
    assert at < else_line, (
        f"the Ask {what} is at line {at}, below the {{% else %}} at line "
        f"{else_line} — it will upload cleanly and never run")


def test_the_section_id_matches_what_nav_switches_to(template):
    """nav('ask') does getElementById('section-' + id); a mismatch is a
    null-dereference that silently blanks the page."""
    assert 'id="section-ask"' in template
    assert "nav('ask',this)" in template


def test_nothing_is_answered_until_someone_asks(template):
    """The product decision: no model call on section open. loadAsk fetches the
    question list only — if it ever starts fetching an answer, that is a cost
    and a UX regression at once."""
    body = template[template.index("window.loadAsk = function()"):]
    body = body[:body.index("function _renderChips")]
    assert "/api/ask/questions" in body
    assert not re.search(r"/api/ask/'\s*\+", body), "loadAsk must not request an answer"


def test_there_is_no_free_text_input(template):
    """v1 is preset questions only, and its absence is a decision the manifest
    publishes as free_text:false."""
    section = template[template.index('id="section-ask"'):]
    section = section[:section.index("</section>")]
    assert "<textarea" not in section
    assert 'type="text"' not in section


def test_the_model_writes_the_findings_so_they_must_be_escaped(template):
    """Findings are LLM output rendered as HTML. Interpolating them raw is a
    stored-XSS vector where the payload arrives via the model."""
    body = template[template.index("function _renderAnswer("):]
    body = body[:body.index("_panels('answer')")]
    for field in ("f.title", "f.detail", "ev[k]"):
        assert f"_e({field})" in body, f"{field} is rendered without escaping"


def test_fixtures_are_a_real_response_not_a_hand_written_stub(template):
    answer = json.loads(ANSWER_FIXTURE.read_text())
    # A hand-written stub would not carry these, and they are what the UI
    # exists to display.
    assert answer.get("findings"), "fixture has no findings"
    assert any(f.get("evidence") for f in answer["findings"]), "no evidence lines"
    assert answer.get("missing_inputs"), "fixture has no dark sources to caveat"
    assert answer.get("narrator") in ("claude", "rules")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_render_harness_against_a_real_answer(tmp_path, template):
    """Run the template's own JS against a captured production payload.

    Extracts the Ask IIFE straight out of the template so this cannot pass
    against a stale copy.
    """
    start = template.index("/* ── Ask ─")
    start = template.rindex("<script>", 0, start) + len("<script>")
    end = template.index("</script>", start)
    js = tmp_path / "ask.js"
    js.write_text(template[start:end])

    proc = subprocess.run(
        ["node", str(HARNESS), str(js), str(ANSWER_FIXTURE), str(MANIFEST_FIXTURE)],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, (
        "the portal's Ask JS did not render the real payload correctly:\n"
        + proc.stdout + proc.stderr)
    assert "ALL CHECKS PASSED" in proc.stdout
