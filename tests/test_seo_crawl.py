"""Tests for webhook-server/seo_crawl.py — the site crawl that feeds reco_seo.

DataForSEO is stubbed entirely. Nothing here opens a socket: the stub is passed
in as `client=`, and the one test that exercises the module-level default
asserts only that the default is the real connector module, without calling it.

The tests that matter most are the ones about NOT measuring something:
  * a crawl that stops at the page cap stores no inbound link counts, so the
    orphan rule cannot invent orphans out of a partial link graph;
  * a page whose raw HTML was never fetched carries no `jsonld_types` at all;
  * a crawl that never finishes is not stored;
  * a warehouse that cannot be read becomes a gap, never an empty finding.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import seo_crawl  # noqa: E402
from skills import reco_seo  # noqa: E402

DAY = "2026-09-18"


# ── the stub ─────────────────────────────────────────────────────────────────

def item(url, *, title="A title", description="A description", h1=("A heading",),
         inbound=3, outbound=12, words=420, status=200, resource="html",
         canonical=None, score=91.5, meta=True):
    """One DataForSEO on-page `pages` item, shaped like the real payload."""
    row = {"url": url, "status_code": status, "resource_type": resource,
           "onpage_score": score}
    if not meta:
        return row
    row["meta"] = {
        "title": title,
        "description": description,
        "htags": {"h1": list(h1)} if h1 is not None else {},
        "canonical": canonical,
        "internal_links_count": outbound,
        "inbound_links_count": inbound,
        "content": {"plain_text_word_count": words},
    }
    return row


HOME = "https://example.com/"
PLANS = "https://example.com/floorplans"
ORPHAN = "https://example.com/neighborhood"

LD = ('<html><head><script type="application/ld+json">'
      '{"@context":"https://schema.org","@graph":['
      '{"@type":"ApartmentComplex","name":"Example"},'
      '{"@type":["Offer","AggregateOffer"],"price":1450}]}'
      '</script></head><body>hi</body></html>')


class Stub:
    """A DataForSEO stand-in. Records every call; never touches the network."""

    def __init__(self, items=None, *, progress="finished", crawl_status=None,
                 html=None, links=None, fail_on=None, cost=0.02):
        self.items = items if items is not None else [item(HOME), item(PLANS)]
        self.progress = progress
        self.crawl_status = crawl_status if crawl_status is not None else {
            "pages_in_queue": 0, "pages_crawled": 2}
        self.html = html if html is not None else {}
        self.links = links
        self.fail_on = fail_on or set()
        self.cost = cost
        self.calls = []
        self.summaries = 0

    def _maybe_fail(self, name):
        self.calls.append(name)
        if name in self.fail_on:
            raise RuntimeError("stubbed %s failure" % name)

    def onpage_task_create(self, target, max_crawl_pages=100,
                           enable_javascript=True, store_raw_html=False):
        self._maybe_fail("task_create")
        self.target = target
        self.max_crawl_pages = max_crawl_pages
        self.store_raw_html = store_raw_html
        return {"task_id": "task-%s" % target, "cost": self.cost}

    def onpage_summary(self, task_id):
        self._maybe_fail("summary")
        self.summaries += 1
        progress = self.progress
        if isinstance(progress, list):
            progress = progress[min(self.summaries - 1, len(progress) - 1)]
        return {"crawl_progress": progress, "crawl_status": self.crawl_status}

    def onpage_pages(self, task_id, limit=100, offset=0):
        self._maybe_fail("pages")
        self.pages_limit = limit
        return {"items": self.items[:limit], "crawl_progress": self.progress,
                "crawl_status": self.crawl_status,
                "total": len(self.items), "cost": 0.0}

    def onpage_links(self, task_id, limit=1000, offset=0):
        self._maybe_fail("links")
        return {"items": self.links or [], "total": len(self.links or []),
                "cost": 0.001}

    def onpage_raw_html(self, task_id, url):
        self._maybe_fail("raw_html")
        return {"html": self.html.get(url), "cost": 0.0}


def crawl(stub, site="example.com", **kwargs):
    """crawl_site with the clock and the sleep taken out."""
    kwargs.setdefault("store", False)
    kwargs.setdefault("force", True)
    kwargs.setdefault("crawl_date", DAY)
    kwargs.setdefault("jsonld_pages", 0)
    return seo_crawl.crawl_site(site, client=stub, sleep=lambda s: None,
                                clock=_clock(), **kwargs)


def batch(stub, targets, **kwargs):
    """crawl_sites against the stub — the real crawl_site, never patched."""
    kwargs.setdefault("store", False)
    kwargs.setdefault("jsonld_pages", 0)
    kwargs.setdefault("crawl_date", DAY)
    return seo_crawl.crawl_sites(targets, client=stub, sleep=lambda s: None,
                                 clock=_clock(), **kwargs)


def _clock():
    """A clock that advances 10 seconds per read, so a timeout is reachable."""
    state = {"t": 0.0}

    def read():
        state["t"] += 10.0
        return state["t"]
    return read


# ── field extraction ─────────────────────────────────────────────────────────

class ExtractionTests(unittest.TestCase):
    def test_every_field_the_rules_read_comes_off_one_item(self):
        page = seo_crawl.extract_page(item(PLANS, title="Floor Plans",
                                           description="Plans and prices",
                                           h1=("Floor Plans",), inbound=7,
                                           outbound=21, words=612))
        self.assertEqual(page["url"], PLANS)
        self.assertEqual(page["title"], "Floor Plans")
        self.assertEqual(page["meta_description"], "Plans and prices")
        self.assertEqual(page["h1"], "Floor Plans")
        self.assertEqual(page["h1_all"], ["Floor Plans"])
        self.assertEqual(page["internal_links_in"], 7)
        self.assertEqual(page["internal_links_out"], 21)
        self.assertEqual(page["word_count"], 612)
        self.assertEqual(page["onpage_score"], 91.5)

    def test_a_page_with_nothing_on_it_reads_as_measured_and_empty(self):
        """This is the finding, so "" not None — the crawl did look."""
        page = seo_crawl.extract_page(item(HOME, title="", description=None, h1=()))
        self.assertEqual(page["title"], "")
        self.assertEqual(page["meta_description"], "")
        self.assertEqual(page["h1"], "")
        self.assertEqual(page["h1_all"], [])

    def test_two_top_headings_are_both_kept(self):
        page = seo_crawl.extract_page(item(HOME, h1=("Welcome", "Live here")))
        self.assertEqual(page["h1_all"], ["Welcome", "Live here"])

    def test_a_pdf_a_redirect_and_a_meta_less_item_are_not_pages(self):
        self.assertIsNone(seo_crawl.extract_page(item(HOME, resource="broken_resource")))
        self.assertIsNone(seo_crawl.extract_page(item(HOME, status=301)))
        self.assertIsNone(seo_crawl.extract_page(item(HOME, status=404)))
        self.assertIsNone(seo_crawl.extract_page(item(HOME, meta=False)))
        self.assertIsNone(seo_crawl.extract_page({"url": ""}))
        self.assertIsNone(seo_crawl.extract_page("not a dict"))

    def test_the_pages_a_renter_lands_on_are_ordered_first(self):
        pages = seo_crawl.extract_pages([item("https://example.com/blog/post"),
                                         item(PLANS), item(HOME)])
        self.assertEqual([p["url"] for p in pages],
                         [HOME, PLANS, "https://example.com/blog/post"])


class JsonLdTests(unittest.TestCase):
    def test_graph_and_list_types_are_all_collected(self):
        self.assertEqual(seo_crawl.jsonld_types(LD),
                         ["ApartmentComplex", "Offer", "AggregateOffer"])

    def test_nested_types_are_found_and_deduplicated(self):
        html = ('<script type="application/ld+json">'
                '{"@type":"Apartment","makesOffer":{"@type":"Offer",'
                '"seller":{"@type":"Apartment"}}}</script>')
        self.assertEqual(seo_crawl.jsonld_types(html), ["Apartment", "Offer"])

    def test_several_blocks_and_odd_quoting(self):
        html = ("<script type='application/ld+json'>{\"@type\":\"WebSite\"}</script>"
                '<SCRIPT TYPE="application/ld+json">{"@type":"Organization"}</SCRIPT>')
        self.assertEqual(seo_crawl.jsonld_types(html), ["WebSite", "Organization"])

    def test_a_cdata_wrapper_still_parses(self):
        html = ('<script type="application/ld+json">//<![CDATA[\n'
                '{"@type":"Apartment"}\n]]></script>')
        self.assertEqual(seo_crawl.jsonld_types(html), [])   # the // guard is not JSON
        html = ('<script type="application/ld+json"><![CDATA['
                '{"@type":"Apartment"}]]></script>')
        self.assertEqual(seo_crawl.jsonld_types(html), ["Apartment"])

    def test_malformed_json_is_skipped_not_guessed_at(self):
        html = ('<script type="application/ld+json">{"@type": </script>'
                '<script type="application/ld+json">{"@type":"Offer"}</script>')
        self.assertEqual(seo_crawl.jsonld_types(html), ["Offer"])

    def test_no_markup_and_no_html_are_both_empty(self):
        self.assertEqual(seo_crawl.jsonld_types("<html><body>hi</body></html>"), [])
        self.assertEqual(seo_crawl.jsonld_types(""), [])
        self.assertEqual(seo_crawl.jsonld_types(None), [])

    def test_a_script_of_another_type_is_not_read(self):
        html = '<script type="application/json">{"@type":"Offer"}</script>'
        self.assertEqual(seo_crawl.jsonld_types(html), [])


class InternalLinkTests(unittest.TestCase):
    GRAPH = [
        {"page_from": HOME, "page_to": PLANS, "is_external": False},
        {"page_from": PLANS, "page_to": HOME, "is_external": False},
        {"link_from": PLANS, "link_to": HOME, "is_external": False},
        {"page_from": HOME, "page_to": HOME, "is_external": False},          # self
        {"page_from": HOME, "page_to": "https://apartments.com/x",
         "is_external": True},                                               # external
    ]

    def test_counts_exclude_self_links_and_external_links(self):
        counts = seo_crawl.inbound_counts(self.GRAPH, "example.com")
        self.assertEqual(counts, {PLANS: 1, HOME: 2})

    def test_an_offsite_destination_is_dropped_even_without_the_flag(self):
        counts = seo_crawl.inbound_counts(
            [{"page_from": HOME, "page_to": "https://other.com/x"}], "example.com")
        self.assertEqual(counts, {})

    def test_a_subdomain_still_counts_as_the_site(self):
        counts = seo_crawl.inbound_counts(
            [{"page_from": HOME, "page_to": "https://tour.example.com/x"}],
            "example.com")
        self.assertEqual(counts, {"https://tour.example.com/x": 1})

    def test_the_graph_fills_in_a_page_the_payload_did_not_count(self):
        pages = seo_crawl.extract_pages(
            [item(HOME, inbound=None), item(PLANS, inbound=None),
             item(ORPHAN, inbound=None)],
            link_graph=seo_crawl.inbound_counts(self.GRAPH, "example.com"))
        counts = {p["url"]: p["internal_links_in"] for p in pages}
        self.assertEqual(counts, {HOME: 2, PLANS: 1, ORPHAN: 0})
        self.assertEqual({p["inbound_source"] for p in pages}, {"link_graph"})

    def test_a_counted_page_is_not_overwritten_by_the_graph(self):
        pages = seo_crawl.extract_pages([item(HOME, inbound=9)], link_graph={HOME: 1})
        self.assertEqual(pages[0]["internal_links_in"], 9)
        self.assertEqual(pages[0]["inbound_source"], "meta")

    def test_junk_in_the_graph_is_ignored(self):
        self.assertEqual(seo_crawl.inbound_counts(["nope", None, {}], "example.com"), {})
        self.assertEqual(seo_crawl.inbound_counts(None), {})


# ── one crawl end to end ─────────────────────────────────────────────────────

class CrawlSiteTests(unittest.TestCase):
    def test_a_finished_crawl_becomes_rows_the_rules_can_read(self):
        stub = Stub(html={HOME: LD, PLANS: LD})
        out = crawl(stub, jsonld_pages=5)
        self.assertEqual(out["status"], "stored")
        self.assertEqual(out["site"], "example.com")
        self.assertEqual(out["pages"], 2)
        self.assertTrue(out["crawl_complete"])
        row = out["rows"][0]
        self.assertEqual(row["crawl_date"], DAY)
        self.assertEqual(row["site"], "example.com")
        self.assertEqual(row["url"], HOME)
        self.assertEqual(row["title"], "A title")
        self.assertEqual(row["meta_description"], "A description")
        self.assertEqual(row["h1"], "A heading")
        self.assertEqual(row["h1_all"], ["A heading"])
        self.assertTrue(row["jsonld_read"])
        self.assertEqual(row["jsonld_types"],
                         ["ApartmentComplex", "Offer", "AggregateOffer"])
        self.assertEqual(row["internal_links_in"], 3)
        self.assertTrue(row["crawl_complete"])
        self.assertEqual(row["task_id"], "task-example.com")

    def test_the_url_is_reduced_to_a_host_before_it_is_crawled_or_keyed(self):
        stub = Stub()
        out = crawl(stub, site="HTTPS://WWW.Example.com/floorplans?x=1")
        self.assertEqual(stub.target, "example.com")
        self.assertEqual(out["site"], "example.com")

    def test_a_property_with_no_site_is_a_gap_not_a_crash(self):
        for value in (None, "", "   ", "not-a-host"):
            out = crawl(Stub(), site=value)
            self.assertEqual(out["status"], "unreadable_site")
            self.assertEqual(out["pages"], 0)
            self.assertTrue(out["gaps"])

    def test_the_corporate_domain_is_never_crawled_as_a_property_site(self):
        stub = Stub()
        out = crawl(stub, site="https://www.rpmliving.com/")
        self.assertEqual(out["status"], "unreadable_site")
        self.assertEqual(stub.calls, [])

    def test_cost_is_summed_from_what_the_api_reported(self):
        stub = Stub(cost=0.0375, html={HOME: LD})
        out = crawl(stub, jsonld_pages=2)
        self.assertEqual(out["cost_usd"], 0.0375)

    def test_a_crawl_that_never_finishes_stores_nothing(self):
        stub = Stub(progress="in_progress")
        out = crawl(stub, poll_seconds=1, poll_timeout=30)
        self.assertEqual(out["status"], "crawl_unfinished")
        self.assertEqual(out["rows"], [])
        self.assertNotIn("pages", stub.calls)
        self.assertTrue(out["gaps"])

    def test_polling_stops_as_soon_as_the_crawl_finishes(self):
        stub = Stub(progress=["in_progress", "in_progress", "finished"])
        out = crawl(stub, poll_seconds=1, poll_timeout=600)
        self.assertEqual(out["status"], "stored")
        self.assertEqual(stub.summaries, 3)

    def test_a_finished_crawl_with_no_readable_page_says_so(self):
        out = crawl(Stub(items=[item(HOME, status=404), item(PLANS, meta=False)]))
        self.assertEqual(out["status"], "no_pages")
        self.assertTrue(out["gaps"])


class PageCapTests(unittest.TestCase):
    def test_the_cap_is_passed_to_the_api_and_to_the_inventory_read(self):
        stub = Stub(items=[item(HOME), item(PLANS), item(ORPHAN)])
        crawl(stub, max_pages=2)
        self.assertEqual(stub.max_crawl_pages, 2)
        self.assertEqual(stub.pages_limit, 2)

    def test_the_default_cap_is_the_configured_one(self):
        stub = Stub()
        crawl(stub, max_pages=None)
        self.assertEqual(stub.max_crawl_pages, seo_crawl.SEO_CRAWL_MAX_PAGES)

    def test_a_crawl_stopped_by_the_cap_stores_no_inbound_link_counts(self):
        """A partial link graph would turn linked pages into invented orphans."""
        stub = Stub(items=[item(HOME), item(PLANS), item(ORPHAN)],
                    crawl_status={"pages_in_queue": 14, "pages_crawled": 2})
        out = crawl(stub, max_pages=2)
        self.assertEqual(out["status"], "stored")
        self.assertFalse(out["crawl_complete"])
        for row in out["rows"]:
            self.assertIsNone(row["internal_links_in"])
            self.assertFalse(row["crawl_complete"])
        self.assertTrue(any(g["field"] == "internal_links_in" for g in out["gaps"]))

    def test_hitting_the_cap_exactly_also_counts_as_truncated(self):
        stub = Stub(items=[item(HOME), item(PLANS)],
                    crawl_status={"pages_in_queue": 0, "pages_crawled": 2})
        out = crawl(stub, max_pages=2)
        self.assertFalse(out["crawl_complete"])

    def test_more_pages_available_than_fetched_is_truncated(self):
        stub = Stub(items=[item(HOME), item(PLANS), item(ORPHAN)],
                    crawl_status={"pages_in_queue": 0, "pages_crawled": 3})
        out = crawl(stub, max_pages=2)
        self.assertFalse(out["crawl_complete"])

    def test_the_jsonld_budget_is_spent_on_the_landing_pages_first(self):
        stub = Stub(items=[item("https://example.com/blog/a"), item(PLANS), item(HOME)],
                    html={HOME: LD, PLANS: LD, "https://example.com/blog/a": LD})
        out = crawl(stub, jsonld_pages=2)
        by_url = {r["url"]: r for r in out["rows"]}
        self.assertTrue(by_url[HOME]["jsonld_read"])
        self.assertTrue(by_url[PLANS]["jsonld_read"])
        self.assertFalse(by_url["https://example.com/blog/a"]["jsonld_read"])

    def test_a_page_whose_html_was_not_read_carries_no_markup_claim(self):
        """Absent, not "no markup found" — the reader must leave the key off."""
        out = crawl(Stub(html={}), jsonld_pages=5)
        for row in out["rows"]:
            self.assertFalse(row["jsonld_read"])
            self.assertEqual(row["jsonld_types"], [])
        pages = [seo_crawl._row_to_page(r) for r in out["rows"]]
        for page in pages:
            self.assertNotIn("jsonld_types", page)

    def test_jsonld_pages_zero_skips_raw_html_entirely(self):
        stub = Stub(html={HOME: LD})
        crawl(stub, jsonld_pages=0)
        self.assertNotIn("raw_html", stub.calls)
        self.assertFalse(stub.store_raw_html)

    def test_one_unreadable_page_does_not_lose_the_others_markup(self):
        class Flaky(Stub):
            def onpage_raw_html(self, task_id, url):
                if url == HOME:
                    raise RuntimeError("timeout")
                return {"html": LD, "cost": 0.0}

        out = crawl(Flaky(html={}), jsonld_pages=5)
        by_url = {r["url"]: r for r in out["rows"]}
        self.assertFalse(by_url[HOME]["jsonld_read"])
        self.assertTrue(by_url[PLANS]["jsonld_read"])


class LinkGraphFallbackTests(unittest.TestCase):
    def test_the_graph_is_only_read_when_a_page_has_no_count(self):
        stub = Stub()
        crawl(stub)
        self.assertNotIn("links", stub.calls)

    def test_the_graph_fills_the_gap_when_the_payload_omits_the_count(self):
        stub = Stub(items=[item(HOME, inbound=None), item(PLANS, inbound=None)],
                    links=[{"page_from": HOME, "page_to": PLANS}])
        out = crawl(stub)
        self.assertIn("links", stub.calls)
        counts = {r["url"]: r["internal_links_in"] for r in out["rows"]}
        self.assertEqual(counts, {HOME: 0, PLANS: 1})
        self.assertEqual(out["cost_usd"], 0.021)

    def test_a_graph_that_cannot_be_read_leaves_the_count_unmeasured(self):
        stub = Stub(items=[item(HOME, inbound=None)], fail_on={"links"})
        out = crawl(stub)
        self.assertEqual(out["status"], "stored")
        self.assertIsNone(out["rows"][0]["internal_links_in"])


class FailureIsolationTests(unittest.TestCase):
    def test_a_failure_at_any_stage_is_a_result_not_an_exception(self):
        for stage in ("task_create", "summary", "pages"):
            out = crawl(Stub(fail_on={stage}))
            self.assertEqual(out["status"], "failed", stage)
            self.assertTrue(out["errors"], stage)
            self.assertTrue(out["gaps"], stage)
            self.assertEqual(out["rows"], [])

    def test_a_task_with_no_id_fails_that_site_only(self):
        class NoId(Stub):
            def onpage_task_create(self, *a, **kw):
                return {"task_id": None, "cost": 0.0}

        out = crawl(NoId())
        self.assertEqual(out["status"], "failed")

    def test_one_site_failing_never_stops_the_rest(self):
        class OneBadSite(Stub):
            def onpage_task_create(self, target, **kw):
                if target == "broken.com":
                    raise RuntimeError("DataForSEO refused")
                return Stub.onpage_task_create(self, target, **kw)

        targets = [{"site": "a.com", "company_id": "1"},
                   {"site": "broken.com", "company_id": "2"},
                   {"site": "c.com", "company_id": "3"}]
        with patch.object(seo_crawl, "stored_sites", return_value=set()):
            summary = batch(OneBadSite(), targets)
        self.assertEqual(summary["sites"], 3)
        self.assertEqual(summary["crawled"], 2)
        self.assertEqual([f["site"] for f in summary["failures"]], ["broken.com"])
        self.assertEqual([r["status"] for r in summary["results"]],
                         ["stored", "failed", "stored"])

    def test_a_batch_reports_cost_per_site_and_total(self):
        with patch.object(seo_crawl, "stored_sites", return_value=set()):
            summary = batch(Stub(cost=0.05), [{"site": "a.com"}, {"site": "b.com"}])
        self.assertEqual(summary["cost_usd"], 0.1)
        self.assertEqual(summary["cost_per_site_usd"], 0.05)
        self.assertEqual(summary["pages"], 4)

    def test_the_default_client_is_the_real_connector_and_is_never_called(self):
        import dataforseo_client
        self.assertIs(seo_crawl._client(None), dataforseo_client)


# ── idempotency ──────────────────────────────────────────────────────────────

class IdempotencyTests(unittest.TestCase):
    def test_a_site_already_stored_for_the_date_is_not_crawled_again(self):
        stub = Stub()
        with patch.object(seo_crawl, "stored_sites", return_value={"example.com"}):
            out = seo_crawl.crawl_site("example.com", client=stub, crawl_date=DAY,
                                       store=False, jsonld_pages=0,
                                       sleep=lambda s: None, clock=_clock())
        self.assertEqual(out["status"], "already_crawled")
        self.assertEqual(stub.calls, [])

    def test_force_crawls_it_again_without_even_asking(self):
        stub = Stub()
        with patch.object(seo_crawl, "stored_sites",
                          side_effect=AssertionError("must not be asked")):
            out = seo_crawl.crawl_site("example.com", client=stub, crawl_date=DAY,
                                       force=True, store=False, jsonld_pages=0,
                                       sleep=lambda s: None, clock=_clock())
        self.assertEqual(out["status"], "stored")

    def test_an_unanswerable_warehouse_does_not_silently_stop_the_crawl(self):
        """None is "we cannot tell", and must not be read as "already done"."""
        stub = Stub()
        with patch.object(seo_crawl, "stored_sites", return_value=None):
            out = seo_crawl.crawl_site("example.com", client=stub, crawl_date=DAY,
                                       store=False, jsonld_pages=0,
                                       sleep=lambda s: None, clock=_clock())
        self.assertEqual(out["status"], "stored")

    def test_the_stored_set_is_read_once_per_batch_not_once_per_site(self):
        with patch.object(seo_crawl, "stored_sites", return_value=set()) as reader:
            batch(Stub(), [{"site": "a.com"}, {"site": "b.com"}, {"site": "c.com"}])
        self.assertEqual(reader.call_count, 1)

    def test_the_same_site_twice_in_one_batch_is_crawled_once(self):
        with patch.object(seo_crawl, "stored_sites", return_value=set()):
            summary = batch(Stub(), [{"site": "https://www.dup.com/", "company_id": "1"},
                                     {"site": "dup.com", "company_id": "2"}])
        self.assertEqual(summary["crawled"], 1)
        self.assertEqual(summary["skipped_already_crawled"], 1)

    def test_stored_sites_returns_none_rather_than_raising(self):
        with patch("bigquery_client.is_bigquery_configured", return_value=True), \
                patch("bigquery_client.query", side_effect=RuntimeError("no table")):
            self.assertIsNone(seo_crawl.stored_sites(DAY))

    def test_stored_sites_is_none_when_bigquery_is_not_configured(self):
        with patch("bigquery_client.is_bigquery_configured", return_value=False):
            self.assertIsNone(seo_crawl.stored_sites(DAY))


# ── the plan (--dry-run) ─────────────────────────────────────────────────────

class PlanTests(unittest.TestCase):
    TARGETS = [{"site": "https://www.a.com/", "company_id": "1", "name": "A"},
               {"site": "a.com", "company_id": "2", "name": "A again"},
               {"site": "b.com", "company_id": "3", "name": "B"},
               {"site": None, "company_id": "4", "name": "No site"},
               {"site": "rpmliving.com", "company_id": "5", "name": "Corporate"},
               {"site": "done.com", "company_id": "6", "name": "Done"}]

    def test_the_plan_calls_nothing_and_names_every_state(self):
        with patch.object(seo_crawl, "stored_sites", return_value={"done.com"}):
            plan = seo_crawl.plan(self.TARGETS, max_pages=7, crawl_date=DAY)
        states = [r["state"] for r in plan["rows"]]
        self.assertEqual(states, ["would_crawl", "duplicate_of_earlier_target",
                                  "would_crawl", "unreadable_site",
                                  "unreadable_site", "already_crawled"])
        self.assertEqual(plan["would_crawl"], 2)
        self.assertEqual(plan["already_crawled"], 1)
        self.assertEqual(plan["unreadable"], 2)
        self.assertEqual(plan["duplicates"], 1)
        self.assertEqual(plan["max_pages_per_site"], 7)
        self.assertEqual(plan["max_pages_total"], 14)
        self.assertTrue(plan["stored_sites_known"])

    def test_the_plan_admits_when_it_cannot_tell_what_is_stored(self):
        with patch.object(seo_crawl, "stored_sites", return_value=None):
            plan = seo_crawl.plan(self.TARGETS, crawl_date=DAY)
        self.assertFalse(plan["stored_sites_known"])
        self.assertEqual(plan["already_crawled"], 0)

    def test_the_plan_predicts_no_price(self):
        with patch.object(seo_crawl, "stored_sites", return_value=set()):
            plan = seo_crawl.plan(self.TARGETS, crawl_date=DAY)
        self.assertNotIn("cost_usd", plan)
        self.assertNotIn("estimated_cost", plan)


# ── storage and the reader ───────────────────────────────────────────────────

def stored_row(url, **extra):
    """A row as BigQuery hands it back."""
    row = {"crawl_date": DAY, "site": "example.com", "company_id": "1001",
           "property_uuid": "u-1", "property_name": "Example",
           "url": url, "status_code": 200, "title": "A title",
           "meta_description": "A description", "h1": "A heading",
           "h1_all": ["A heading"], "jsonld_read": False, "jsonld_types": [],
           "internal_links_in": 4, "internal_links_out": 12,
           "inbound_source": "meta", "word_count": 420, "canonical": None,
           "onpage_score": 91.5, "crawl_complete": True, "pages_available": 2,
           "task_id": "task-1", "crawled_at": DAY + "T00:00:00+00:00"}
    row.update(extra)
    return row


class StoreTests(unittest.TestCase):
    def test_rows_go_to_the_configured_audit_table(self):
        out = crawl(Stub(), store=True)
        with patch("bigquery_client.insert_rows") as writer:
            stored = seo_crawl.store_rows(out["rows"])
        self.assertEqual(stored, 2)
        table, rows = writer.call_args[0]
        self.assertEqual(table, seo_crawl.BIGQUERY_SEO_AUDIT_TABLE)
        self.assertEqual(len(rows), 2)

    def test_nothing_to_store_writes_nothing(self):
        with patch("bigquery_client.insert_rows") as writer:
            self.assertEqual(seo_crawl.store_rows([]), 0)
        writer.assert_not_called()

    def test_a_write_failure_fails_that_site_and_not_the_run(self):
        with patch("bigquery_client.insert_rows",
                   side_effect=RuntimeError("insert errors")):
            out = crawl(Stub(), store=True)
        self.assertEqual(out["status"], "failed")
        self.assertTrue(out["errors"])


class ReaderTests(unittest.TestCase):
    def read(self, rows, **kwargs):
        kwargs.setdefault("company_id", "1001")
        with patch("bigquery_client.is_bigquery_configured", return_value=True), \
                patch("bigquery_client._dataset", return_value="rpm_portal"), \
                patch("bigquery_client.query", return_value=rows) as q:
            out = seo_crawl.stored_pages(**kwargs)
        self.q = q
        return out

    def test_a_stored_row_becomes_the_page_shape_the_rules_read(self):
        out = self.read([stored_row(HOME, jsonld_read=True,
                                    jsonld_types=["Apartment", "Offer"])])
        self.assertEqual(out["source"], "site_crawl")
        self.assertEqual(out["as_of"], DAY)
        page = out["pages"][0]
        self.assertEqual(page["url"], HOME)
        self.assertEqual(page["title"], "A title")
        self.assertEqual(page["meta_description"], "A description")
        self.assertEqual(page["h1"], "A heading")
        self.assertEqual(page["jsonld_types"], ["Apartment", "Offer"])
        self.assertEqual(page["internal_links_in"], 4)
        self.assertEqual(page["headings"], [{"level": "1", "text": "A heading"}])
        self.assertEqual(page["word_count"], 420)

    def test_an_unmeasured_column_is_left_off_the_page_entirely(self):
        out = self.read([stored_row(HOME, title=None, meta_description=None,
                                    h1=None, h1_all=None, internal_links_in=None,
                                    word_count=None, jsonld_read=False)])
        page = out["pages"][0]
        for key in ("title", "meta_description", "h1", "headings",
                    "internal_links_in", "jsonld_types", "word_count"):
            self.assertNotIn(key, page)

    def test_a_measured_empty_title_is_kept_because_that_is_the_finding(self):
        out = self.read([stored_row(HOME, title="", meta_description="", h1="")])
        page = out["pages"][0]
        self.assertEqual(page["title"], "")
        self.assertEqual(page["meta_description"], "")
        self.assertEqual(page["h1"], "")

    def test_a_truncated_crawl_reports_its_own_limit_as_a_gap(self):
        out = self.read([stored_row(HOME, crawl_complete=False,
                                    internal_links_in=None)])
        self.assertFalse(out["complete"])
        self.assertEqual([g["field"] for g in out["gaps"]], ["internal_links_in"])

    def test_nothing_stored_is_no_pages_and_no_gap(self):
        out = self.read([])
        self.assertEqual(out["pages"], [])
        self.assertEqual(out["gaps"], [])

    def test_a_row_with_no_url_is_dropped(self):
        out = self.read([stored_row(""), stored_row(HOME)])
        self.assertEqual([p["url"] for p in out["pages"]], [HOME])

    def test_bigquery_unconfigured_is_empty_and_never_raises(self):
        with patch("bigquery_client.is_bigquery_configured", return_value=False):
            out = seo_crawl.stored_pages(company_id="1001")
        self.assertEqual(out["pages"], [])
        self.assertEqual(out["gaps"], [])

    def test_bigquery_unreadable_becomes_a_gap_and_never_raises(self):
        with patch("bigquery_client.is_bigquery_configured", return_value=True), \
                patch("bigquery_client._dataset", return_value="rpm_portal"), \
                patch("bigquery_client.query", side_effect=RuntimeError("no table")):
            out = seo_crawl.stored_pages(company_id="1001")
        self.assertEqual(out["pages"], [])
        self.assertEqual(len(out["gaps"]), 1)
        self.assertEqual(out["gaps"][0]["source"], seo_crawl.BIGQUERY_SEO_AUDIT_TABLE)
        self.assertTrue(out["gaps"][0]["message"].endswith("."))

    def test_no_key_at_all_reads_nothing(self):
        with patch("bigquery_client.is_bigquery_configured", return_value=True), \
                patch("bigquery_client.query") as q:
            out = seo_crawl.stored_pages()
        q.assert_not_called()
        self.assertEqual(out["pages"], [])

    def test_a_shared_host_never_hands_a_property_its_neighbours_pages(self):
        rows = [stored_row(HOME, site="mine.com", company_id="1001"),
                stored_row(PLANS, site="theirs.com", company_id="2002")]
        out = self.read(rows, company_id="1001", site="mine.com")
        self.assertEqual([p["url"] for p in out["pages"]], [HOME])

    def test_the_site_asked_for_wins_when_no_company_matches(self):
        rows = [stored_row(HOME, site="mine.com", company_id="9"),
                stored_row(PLANS, site="theirs.com", company_id="8")]
        out = self.read(rows, company_id="1001", site="theirs.com")
        self.assertEqual([p["url"] for p in out["pages"]], [PLANS])

    def test_the_lookback_window_is_a_bound_parameter(self):
        self.read([stored_row(HOME)], within_days=9)
        params = {p.name: p.value for p in self.q.call_args[0][1]}
        self.assertEqual(params["days"], 9)
        self.assertEqual(params["company_id"], "1001")
        self.assertIn("DATE_SUB(CURRENT_DATE(), INTERVAL @days DAY)",
                      self.q.call_args[0][0])

    def test_pages_for_property_is_the_same_read(self):
        with patch.object(seo_crawl, "stored_pages",
                          return_value={"pages": [], "gaps": []}) as reader:
            seo_crawl.pages_for_property("1001", site="x.com", property_uuid="u")
        reader.assert_called_once_with(company_id="1001", site="x.com",
                                       property_uuid="u", within_days=None)


# ── the point of all of it ───────────────────────────────────────────────────

class RecoSeoFiresFromStoredRowsTests(unittest.TestCase):
    """The four rules that were skipped on 97 sites now have their input."""

    ROWS = [
        # The homepage: fine, and it is what proves the rule is selective.
        stored_row(HOME, title="Example Apartments", meta_description="Live here",
                   h1="Example Apartments", internal_links_in=6),
        # A landing page with no title and no description: the metadata finding.
        stored_row(PLANS, title="", meta_description="", h1="Floor Plans",
                   internal_links_in=2),
        # A page nothing links to: the orphan finding.
        stored_row(ORPHAN, title="Neighborhood", meta_description="Around us",
                   h1="Neighborhood", internal_links_in=0),
    ]

    def _gather(self, rows):
        identity = type("Id", (), {
            "to_dict": lambda self: {"name": "Example", "uuid": "u-1",
                                     "domain": "example.com"}})()
        with patch("skills.property_resolver.resolve", return_value=identity), \
                patch("community_brief.load_company_state", return_value={}), \
                patch("bigquery_client.is_bigquery_configured", return_value=True), \
                patch("bigquery_client._dataset", return_value="rpm_portal"), \
                patch("bigquery_client.query", return_value=rows):
            return reco_seo.gather("1001")

    def test_gather_hands_the_stored_crawl_to_the_rules(self):
        data, gaps = self._gather(self.ROWS)
        self.assertEqual(len(data["pages"]), 3)
        self.assertEqual(data["pages_source"], "site_crawl")
        self.assertEqual(data["pages_as_of"], DAY)
        self.assertNotIn("site_crawl", {g["source"] for g in gaps})

    def test_the_metadata_and_orphan_rules_both_fire_off_stored_rows(self):
        from skills import reco_engine

        data, _ = self._gather(self.ROWS)
        out = reco_seo.run("1001", data=data)
        fired = {r["rule_key"] for r in out["recommendations"]}
        self.assertIn("seo_money_page_metadata_gap", fired)
        self.assertIn("seo_orphan_page", fired)
        self.assertIn("seo_money_page_metadata_gap", out["rules_run"])
        self.assertIn("seo_orphan_page", out["rules_run"])
        for reco in out["recommendations"]:
            self.assertIsNone(reco_engine.validate(reco))
            self.assertIsNone(reco_engine.compliance_refusal(reco))
        metadata = [r for r in out["recommendations"]
                    if r["rule_key"] == "seo_money_page_metadata_gap"][0]
        self.assertEqual([p["url"] for p in metadata["action"]["params"]["pages"]],
                         [PLANS])
        orphan = [r for r in out["recommendations"]
                  if r["rule_key"] == "seo_orphan_page"][0]
        self.assertEqual(orphan["action"]["params"]["pages"], [ORPHAN])
        self.assertTrue(any(r["source"] == "site_crawl" for r in orphan["receipts"]))

    def test_the_availability_rule_fires_on_stored_markup(self):
        rows = [stored_row(HOME, jsonld_read=True, jsonld_types=["WebSite"]),
                stored_row(PLANS, jsonld_read=True, jsonld_types=["WebPage"])]
        data, _ = self._gather(rows)
        data["availability"] = {"available_units": 22, "source": "aptiq_api",
                                "as_of": DAY}
        out = reco_seo.run("1001", data=data)
        self.assertIn("seo_availability_not_machine_readable",
                      {r["rule_key"] for r in out["recommendations"]})

    def test_stored_markup_that_is_readable_keeps_the_rule_quiet(self):
        rows = [stored_row(PLANS, jsonld_read=True,
                           jsonld_types=["ApartmentComplex", "Offer"])]
        data, _ = self._gather(rows)
        data["availability"] = {"available_units": 22, "source": "aptiq_api"}
        out = reco_seo.run("1001", data=data)
        self.assertNotIn("seo_availability_not_machine_readable",
                         {r["rule_key"] for r in out["recommendations"]})

    def test_the_floor_plan_rule_fires_against_the_crawled_pages(self):
        data, _ = self._gather(self.ROWS)
        data["floor_plans"] = [{"name": "A1", "beds": 1, "available": 4},
                               {"name": "B2", "beds": 2, "available": 0}]
        out = reco_seo.run("1001", data=data)
        self.assertIn("seo_missing_floor_plan_page",
                      {r["rule_key"] for r in out["recommendations"]})

    def test_a_truncated_crawl_cannot_produce_an_orphan_finding(self):
        rows = [stored_row(HOME, crawl_complete=False, internal_links_in=None),
                stored_row(ORPHAN, crawl_complete=False, internal_links_in=None)]
        data, gaps = self._gather(rows)
        out = reco_seo.run("1001", data=data)
        self.assertNotIn("seo_orphan_page", {r["rule_key"]
                                             for r in out["recommendations"]})
        self.assertIn("internal_links_in", {g["field"] for g in gaps})

    def test_nothing_stored_leaves_the_page_rules_skipped_not_clean(self):
        data, gaps = self._gather([])
        self.assertNotIn("pages", data)
        self.assertIn("site_crawl", {g["source"] for g in gaps})
        out = reco_seo.run("1001", data=data)
        skipped = {s["rule_key"] for s in out["rules_skipped"]}
        for rule in ("seo_money_page_metadata_gap", "seo_orphan_page",
                     "seo_missing_floor_plan_page",
                     "seo_availability_not_machine_readable"):
            self.assertIn(rule, skipped)

    def test_a_vendor_read_of_the_site_is_not_overwritten_by_the_crawl(self):
        data = {"company_id": "1001",
                "pages": [{"url": HOME, "title": "From the vendor"}],
                "pages_source": "searchable_site_health"}
        gaps = []
        with patch.object(seo_crawl, "stored_pages",
                          side_effect=AssertionError("must not be read")):
            reco_seo._gather_pages("1001", None, data, gaps)
        self.assertEqual(data["pages_source"], "searchable_site_health")

    def test_a_warehouse_that_raises_leaves_gather_standing(self):
        data = {"company_id": "1001"}
        gaps = []
        with patch.object(seo_crawl, "pages_for_property",
                          side_effect=RuntimeError("down")):
            reco_seo._gather_pages("1001", None, data, gaps)
        self.assertNotIn("pages", data)


if __name__ == "__main__":
    unittest.main()
