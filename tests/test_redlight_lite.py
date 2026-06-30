"""Red Light Report — Lite: parsing, approved-threshold scoring, trends,
portfolio summary, and route-level Beta/Prod gating.

Uses the real property numbers from the rollout dataset so the scoring is
checked against numbers RPM recognizes.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import redlight_lite as rl


class TestParsing(unittest.TestCase):
    def test_pct_forms(self):
        self.assertEqual(rl._pct("85.82%"), 85.82)
        self.assertEqual(rl._pct("71%"), 71)
        self.assertEqual(rl._pct(0.71), 71)        # bare fraction → percent
        self.assertEqual(rl._pct(71), 71)
        self.assertIsNone(rl._pct("-"))
        self.assertIsNone(rl._pct(""))

    def test_money_forms(self):
        self.assertEqual(rl._money("$686"), 686.0)
        self.assertEqual(rl._money("$1,400"), 1400.0)
        self.assertIsNone(rl._money("$-"))
        self.assertIsNone(rl._money("-"))

    def test_num_forms(self):
        self.assertEqual(rl._num("6,840"), 6840.0)
        self.assertEqual(rl._num("14.4"), 14.4)
        self.assertIsNone(rl._num("-"))


class TestThresholds(unittest.TestCase):
    def test_lead_to_prospect(self):
        t = rl.FUNNEL_THRESHOLDS["lead_to_prospect"]
        self.assertEqual(t.status(60), rl.GREEN)
        self.assertEqual(t.status(59), rl.YELLOW)
        self.assertEqual(t.status(50), rl.YELLOW)
        self.assertEqual(t.status(49), rl.RED)
        self.assertEqual(t.status(None), rl.UNSCORED)

    def test_prospect_to_tour(self):
        t = rl.FUNNEL_THRESHOLDS["prospect_to_tour"]
        self.assertEqual(t.status(35), rl.GREEN)
        self.assertEqual(t.status(34), rl.YELLOW)
        self.assertEqual(t.status(25), rl.YELLOW)
        self.assertEqual(t.status(24), rl.RED)


class TestScoreProperty(unittest.TestCase):
    def test_overall_is_worst_funnel(self):
        # 79 West: L2P 48 (RED), P2T 20 (RED) → RED
        p = rl.score_property({
            "Property Name": "79 West",
            "Lead to Prospect": "48%", "Prospect to Tour": "20%",
        })
        self.assertEqual(p["lead_to_prospect"]["status"], rl.RED)
        self.assertEqual(p["prospect_to_tour"]["status"], rl.RED)
        self.assertEqual(p["status"], rl.RED)

    def test_green_lead_red_tour_is_red(self):
        # Park at Valenza: L2P 90 (GREEN), P2T 20 (RED) → worst = RED
        p = rl.score_property({"Lead to Prospect": "90%", "Prospect to Tour": "20%"})
        self.assertEqual(p["status"], rl.RED)

    def test_yellow(self):
        # 10X Tarpon: L2P 59 (YELLOW), P2T 44 (GREEN) → YELLOW
        p = rl.score_property({"Lead to Prospect": "59%", "Prospect to Tour": "44%"})
        self.assertEqual(p["status"], rl.YELLOW)

    def test_occupancy_trend_lease_up(self):
        # Oasis: occ rising from near-zero is GOOD; ATR falling is GOOD.
        p = rl.score_property({
            "Current Occupancy": "5.65%", "1mo Previous Occ": "1.30%",
            "ATR": "86.96%", "1mo Previous ATR": "94.35%",
        })
        self.assertEqual(p["occupancy_trend_1mo"]["arrow"], "up")
        self.assertEqual(p["occupancy_trend_1mo"]["sentiment"], "good")
        self.assertEqual(p["atr_trend_1mo"]["arrow"], "down")
        self.assertEqual(p["atr_trend_1mo"]["sentiment"], "good")

    def test_occupancy_decline_is_bad(self):
        p = rl.score_property({"Current Occupancy": "86.02%", "1mo Previous Occ": "88.14%"})
        self.assertEqual(p["occupancy_trend_1mo"]["sentiment"], "bad")

    def test_missing_funnel_is_unscored(self):
        p = rl.score_property({"Property Name": "No Funnel Data"})
        self.assertEqual(p["status"], rl.UNSCORED)

    def test_cost_per_lease_dash(self):
        p = rl.score_property({"Cost Per Lease": "$-"})
        self.assertIsNone(p["cost_per_lease"])


class TestNextSteps(unittest.TestCase):
    def test_red_funnel_yields_red_priority_step(self):
        p = rl.score_property({"Lead to Prospect": "48%", "Prospect to Tour": "20%"})
        steps = p["next_steps"]
        self.assertTrue(steps)
        self.assertEqual(steps[0]["priority"], rl.RED)         # most severe first
        joined = " ".join(s["text"] for s in steps)
        self.assertIn("Lead→Prospect", joined)
        self.assertIn("Prospect→Tour", joined)

    def test_two_month_occupancy_decline_flagged(self):
        p = rl.score_property({
            "Current Occupancy": "69.46%", "1mo Previous Occ": "72.68%",
            "2mo Previous Occ": "74.74%",
            "Lead to Prospect": "90%", "Prospect to Tour": "40%",
        })
        joined = " ".join(s["text"] for s in p["next_steps"])
        self.assertIn("two months", joined)

    def test_healthy_gets_maintain_step(self):
        p = rl.score_property({
            "Current Occupancy": "95%", "1mo Previous Occ": "94%",
            "ATR": "5%", "1mo Previous ATR": "6%",
            "Lead to Prospect": "70%", "Prospect to Tour": "45%",
        })
        self.assertEqual(p["next_steps"][0]["priority"], rl.GREEN)
        self.assertIn("Maintain", p["next_steps"][0]["text"])

    def test_next_steps_render_in_html(self):
        html = rl.render_html(rl.build_report([
            {"Property Name": "79 West", "Lead to Prospect": "48%", "Prospect to Tour": "20%"},
        ]))
        self.assertIn("Next steps for the marketing manager", html)


class TestBuildReport(unittest.TestCase):
    def _rows(self):
        return [
            {"Property Name": "79 West", "Lead to Prospect": "48%", "Prospect to Tour": "20%"},
            {"Property Name": "10X", "Lead to Prospect": "59%", "Prospect to Tour": "44%"},
            {"Property Name": "Sur Club", "Lead to Prospect": "61%", "Prospect to Tour": "51%"},
        ]

    def test_summary_counts(self):
        rep = rl.build_report(self._rows())
        self.assertEqual(rep["summary"][rl.RED], 1)
        self.assertEqual(rep["summary"][rl.YELLOW], 1)
        self.assertEqual(rep["summary"][rl.GREEN], 1)
        self.assertEqual(rep["property_count"], 3)

    def test_sorted_worst_first(self):
        rep = rl.build_report(self._rows())
        self.assertEqual(rep["properties"][0]["status"], rl.RED)
        self.assertEqual(rep["properties"][-1]["status"], rl.GREEN)

    def test_parse_csv_roundtrip(self):
        csv_text = ("Property Name,Lead to Prospect,Prospect to Tour\n"
                    "Foo,70%,40%\n")
        rep = rl.build_report(rl.parse_csv(csv_text))
        self.assertEqual(rep["properties"][0]["status"], rl.GREEN)

    def test_render_html_smoke(self):
        html = rl.render_html(rl.build_report(self._rows()))
        self.assertIn("RPM LIVING", html)
        self.assertIn("79 West", html)
        self.assertIn("Prospect", html)


class TestEndpointGating(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("HUBSPOT_API_KEY", "test")
        os.environ.setdefault("WEBHOOK_SECRET", "test")
        os.environ["INTERNAL_API_KEY"] = "lite-secret"
        import server
        cls.client = server.app.test_client()

    _BODY = {"properties": [
        {"Property Name": "79 West", "Lead to Prospect": "48%", "Prospect to Tour": "20%"},
    ]}

    def test_no_credentials_401(self):
        r = self.client.post("/api/red-light-lite/report", json=self._BODY)
        self.assertEqual(r.status_code, 401)

    def test_non_allowlisted_client_403(self):
        r = self.client.post("/api/red-light-lite/report", json=self._BODY,
                             headers={"X-Portal-Email": "client@acme.test"})
        self.assertEqual(r.status_code, 403)

    def test_internal_staff_200(self):
        r = self.client.post("/api/red-light-lite/report", json=self._BODY,
                             headers={"X-Portal-Email": "kyle@rpmliving.com"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["summary"][rl.RED], 1)

    def test_internal_key_bypasses_gate_and_renders_html(self):
        r = self.client.post("/api/red-light-lite/report?format=html", json=self._BODY,
                             headers={"X-Internal-Key": "lite-secret"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers.get("Content-Type", ""))

    def test_empty_body_400(self):
        r = self.client.post("/api/red-light-lite/report", json={"properties": []},
                             headers={"X-Internal-Key": "lite-secret"})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
