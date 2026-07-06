"""Tests for batch_runs — portfolio sweeps for Red Light v2 + forecasts."""

from __future__ import annotations

import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
os.environ.setdefault("HUBSPOT_API_KEY", "test-key")

import batch_runs as br  # noqa: E402


def _co(cid, ple="RPM Managed", uuid="", aptiq="", run_date=""):
    return {"_company_id": cid, "plestatus": ple, "uuid": uuid,
            "aptiq_property_id": aptiq, "redlight_v2_run_date": run_date,
            "seo_tier": "", "name": f"P{cid}"}


class RedLightBatchTests(unittest.TestCase):

    def test_skips_fresh_and_no_aptiq(self):
        import datetime as dt
        today = dt.date.today().isoformat()
        old = (dt.date.today() - dt.timedelta(days=40)).isoformat()
        companies = [
            _co("1", aptiq="a1", run_date=today),   # fresh -> skip
            _co("2", aptiq="a2", run_date=old),     # stale -> run
            _co("3", aptiq="",   run_date=""),      # no aptiq -> skip
            _co("4", aptiq="a4", run_date=""),      # never run -> run
        ]
        ran = []
        fake_rl = mock.Mock()
        fake_rl.run = mock.Mock(side_effect=lambda company_id: ran.append(company_id))
        with mock.patch.object(br, "_enumerate_companies", return_value=companies), \
             mock.patch.dict(sys.modules, {"redlight_v2_run": fake_rl}):
            res = br.red_light_v2_batch(pause_seconds=0)
            time.sleep(0.3)  # let the sweep thread drain
        self.assertEqual(res["status"], "dispatched")
        self.assertEqual(res["eligible"], 2)
        self.assertEqual(res["skipped_fresh"], 1)
        self.assertEqual(res["skipped_no_aptiq"], 1)
        self.assertEqual(sorted(ran), ["2", "4"])

    def test_limit(self):
        companies = [_co(str(i), aptiq=f"a{i}") for i in range(5)]
        fake_rl = mock.Mock(); fake_rl.run = mock.Mock()
        with mock.patch.object(br, "_enumerate_companies", return_value=companies), \
             mock.patch.dict(sys.modules, {"redlight_v2_run": fake_rl}):
            res = br.red_light_v2_batch(limit=2, pause_seconds=0)
            time.sleep(0.3)
        self.assertEqual(res["eligible"], 2)
        self.assertEqual(fake_rl.run.call_count, 2)

    def test_failures_counted_not_fatal(self):
        companies = [_co("1", aptiq="a1"), _co("2", aptiq="a2")]
        fake_rl = mock.Mock()
        fake_rl.run = mock.Mock(side_effect=[Exception("boom"), None])
        with mock.patch.object(br, "_enumerate_companies", return_value=companies), \
             mock.patch.dict(sys.modules, {"redlight_v2_run": fake_rl}):
            res = br.red_light_v2_batch(pause_seconds=0)
            time.sleep(0.3)
        self.assertEqual(res["eligible"], 2)
        self.assertEqual(fake_rl.run.call_count, 2)  # second still ran


class ForecastBatchTests(unittest.TestCase):

    def test_runs_only_uuid_companies(self):
        companies = [_co("1", uuid="u1"), _co("2", uuid=""), _co("3", uuid="u3")]
        fake_fc = mock.Mock(); fake_fc.run_forecast = mock.Mock()
        with mock.patch.object(br, "_enumerate_companies", return_value=companies), \
             mock.patch.dict(sys.modules, {"forecasting": fake_fc}):
            res = br.forecast_batch(pause_seconds=0)
            time.sleep(0.3)
        self.assertEqual(res["eligible"], 2)
        self.assertEqual(res["skipped_no_uuid"], 1)
        called = [c.args[0] for c in fake_fc.run_forecast.call_args_list]
        self.assertEqual(sorted(called), ["u1", "u3"])

    def test_seo_tier_passed(self):
        companies = [dict(_co("1", uuid="u1"), seo_tier="Standard")]
        fake_fc = mock.Mock(); fake_fc.run_forecast = mock.Mock()
        with mock.patch.object(br, "_enumerate_companies", return_value=companies), \
             mock.patch.dict(sys.modules, {"forecasting": fake_fc}):
            br.forecast_batch(pause_seconds=0)
            time.sleep(0.3)
        _, kwargs = fake_fc.run_forecast.call_args
        self.assertEqual(kwargs.get("seo_tier"), "Standard")


if __name__ == "__main__":
    unittest.main()
