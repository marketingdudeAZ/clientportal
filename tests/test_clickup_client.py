"""Transport + field-cache behaviour for clickup_client.

Never mock `requests.get` here — after the transport landed, nothing calls it.
Install a fake on `clickup_client._SESSION` instead.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))

import clickup_client as cu  # noqa: E402


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None, text=""):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._body


class FakeSession:
    """Replays a queued list of responses and records the calls made."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self._responses:
            raise AssertionError(f"unexpected extra request: {method} {url}")
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


class _Base(unittest.TestCase):
    def setUp(self):
        cu.clear_field_cache()
        self.slept = []
        p = mock.patch.object(cu.time, "sleep", self.slept.append)
        p.start()
        self.addCleanup(p.stop)
        k = mock.patch.object(cu, "CLICKUP_API_KEY", "pk_test")
        k.start()
        self.addCleanup(k.stop)

    def install(self, *responses):
        fake = FakeSession(responses)
        p = mock.patch.object(cu, "_session", return_value=fake)
        p.start()
        self.addCleanup(p.stop)
        return fake


class RetryAndBackoff(_Base):
    def test_429_honours_retry_after_then_succeeds(self):
        fake = self.install(
            FakeResponse(429, headers={"Retry-After": "0"}),
            FakeResponse(200, {"id": "t1"}),
        )
        self.assertEqual(cu.get_task("t1"), {"id": "t1"})
        self.assertEqual(self.slept, [0.0])
        self.assertEqual(len(fake.calls), 2)

    def test_retry_after_is_capped(self):
        self.install(FakeResponse(429, headers={"Retry-After": "600"}),
                     FakeResponse(200, {"id": "t1"}))
        cu.get_task("t1")
        self.assertEqual(self.slept, [cu._MAX_BACKOFF])

    def test_unparseable_retry_after_falls_back_to_exponential(self):
        self.install(FakeResponse(429, headers={"Retry-After": "soon"}),
                     FakeResponse(200, {"id": "t1"}))
        cu.get_task("t1")
        self.assertEqual(self.slept, [2.0])

    def test_gives_up_after_max_retries(self):
        fake = self.install(*[FakeResponse(429, headers={"Retry-After": "0"})
                              for _ in range(cu._MAX_RETRIES + 1)])
        self.assertIsNone(cu.get_task("t1"))
        self.assertEqual(len(fake.calls), cu._MAX_RETRIES + 1)

    def test_5xx_is_retried_on_get(self):
        fake = self.install(FakeResponse(502), FakeResponse(200, {"id": "t1"}))
        self.assertEqual(cu.get_task("t1"), {"id": "t1"})
        self.assertEqual(len(fake.calls), 2)

    def test_5xx_is_NOT_retried_on_post(self):
        """A retried create against a 502 that actually landed files the
        requester's ticket twice."""
        fake = self.install(FakeResponse(502))
        self.assertIsNone(cu.create_task("901", "Subject"))
        self.assertEqual(len(fake.calls), 1)

    def test_429_IS_retried_on_post(self):
        """429 means ClickUp rejected it outright — nothing landed to duplicate."""
        fake = self.install(FakeResponse(429, headers={"Retry-After": "0"}),
                            FakeResponse(200, {"id": "new"}))
        self.assertEqual(cu.create_task("901", "Subject"), {"id": "new"})
        self.assertEqual(len(fake.calls), 2)

    def test_404_is_not_retried(self):
        fake = self.install(FakeResponse(404))
        self.assertIsNone(cu.get_task("nope"))
        self.assertEqual(len(fake.calls), 1)

    def test_call_budget_short_circuits_before_sleeping(self):
        self.install(FakeResponse(429, headers={"Retry-After": "5"}))
        with mock.patch.object(cu, "_CALL_BUDGET", 0.5):
            self.assertIsNone(cu.get_task("t1"))
        self.assertEqual(self.slept, [])

    def test_network_exception_returns_none_not_raises(self):
        import requests
        self.install(requests.RequestException("boom"))
        self.assertIsNone(cu.get_task("t1"))


class FieldCache(_Base):
    FIELDS = {"fields": [{"id": "f1", "name": "Priority", "type": "drop_down"}]}

    def test_second_call_is_served_from_cache(self):
        fake = self.install(FakeResponse(200, self.FIELDS))
        first = cu.get_list_fields("901")
        second = cu.get_list_fields("901")
        self.assertEqual(first, second)
        self.assertEqual(len(fake.calls), 1)

    def test_cache_is_keyed_per_list(self):
        fake = self.install(FakeResponse(200, self.FIELDS),
                            FakeResponse(200, self.FIELDS))
        cu.get_list_fields("901")
        cu.get_list_fields("902")
        self.assertEqual(len(fake.calls), 2)

    def test_expiry_refetches(self):
        fake = self.install(FakeResponse(200, self.FIELDS),
                            FakeResponse(200, self.FIELDS))
        cu.get_list_fields("901")
        with mock.patch.object(cu, "_FIELD_CACHE_TTL", -1):
            cu.get_list_fields("901")
        self.assertEqual(len(fake.calls), 2)

    def test_a_fieldless_list_is_empty_list_NOT_none(self):
        """The core distinction: [] means 'genuinely no fields', None means
        'we could not find out'. Conflating them renders a form that submits
        into a task missing every required field."""
        self.install(FakeResponse(200, {"fields": []}))
        self.assertEqual(cu.get_list_fields("901"), [])

    def test_failure_with_no_cache_is_none(self):
        self.install(*[FakeResponse(429, headers={"Retry-After": "0"})
                       for _ in range(cu._MAX_RETRIES + 1)])
        self.assertIsNone(cu.get_list_fields("901"))

    def test_failure_with_a_warm_cache_serves_stale(self):
        self.install(FakeResponse(200, self.FIELDS),
                     *[FakeResponse(500) for _ in range(cu._MAX_RETRIES + 1)])
        warm = cu.get_list_fields("901")
        with mock.patch.object(cu, "_FIELD_CACHE_TTL", -1):
            stale = cu.get_list_fields("901")
        self.assertEqual(stale, warm)

    def test_repeated_failures_hit_the_cooldown_not_the_api(self):
        """Without this, a ClickUp outage costs one full call budget per list
        per request — the outage amplifying itself."""
        fake = self.install(*[FakeResponse(500) for _ in range(cu._MAX_RETRIES + 1)])
        self.assertIsNone(cu.get_list_fields("901"))
        before = len(fake.calls)
        self.assertIsNone(cu.get_list_fields("901"))
        self.assertEqual(len(fake.calls), before)

    def test_missing_api_key_is_none_not_empty(self):
        with mock.patch.object(cu, "CLICKUP_API_KEY", ""):
            self.assertIsNone(cu.get_list_fields("901"))

    def test_clear_field_cache_resets(self):
        fake = self.install(FakeResponse(200, self.FIELDS),
                            FakeResponse(200, self.FIELDS))
        cu.get_list_fields("901")
        cu.clear_field_cache()
        cu.get_list_fields("901")
        self.assertEqual(len(fake.calls), 2)


if __name__ == "__main__":
    unittest.main()
