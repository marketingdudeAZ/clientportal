"""Guard: every state-changing route must have SOME auth check.

This is a static regression test. It iterates Flask's `app.url_map`, finds
every POST/PATCH/DELETE route, reads its handler source, and asserts that
the source references at least one known auth marker.

Intent: prevent a new unprotected route from silently landing. When the
signed-request auth decorator ships, add `@require_portal_auth` to the
marker set below and the test keeps protecting the codebase.

Does NOT verify auth is *correctly* applied — only that *some* check is
present. For behavioral coverage see the journey tests under tests/.
"""

import inspect
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webhook-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Load the app. Minimum env to make the imports succeed.
os.environ.setdefault("HUBSPOT_API_KEY", "test")
os.environ.setdefault("WEBHOOK_SECRET", "test")


# Markers that count as "some form of auth is performed".
#   HMAC_BODY       — webhook body HMAC via hmac_validator.validate_signature
#   INTERNAL_KEY    — server-to-server shared secret via require_internal_key
#   PORTAL_EMAIL    — interim portal trust via X-Portal-Email header read
#   PROVIDER_SIG    — delegated provider signature check via normalize_webhook
#   SIGNED_REQUEST  — future signed-request decorator (reserved)
AUTH_MARKERS = (
    "validate_signature",          # HMAC_BODY
    "require_internal_key",        # INTERNAL_KEY (decorator name)
    "X-Portal-Email",              # PORTAL_EMAIL (inline check)
    "_resolve_seo_context",        # PORTAL_EMAIL (SEO context helper)
    "_resolve_paid_context",       # PORTAL_EMAIL (paid context helper)
    "normalize_webhook",           # PROVIDER_SIG
    "require_portal_auth",         # SIGNED_REQUEST (reserved)
    "verify_request_signature",    # SIGNED_REQUEST (raw helper)
)

# Routes that are legitimately unauthenticated. Keep this list SHORT and
# explicit — anything added here should be defensible in a security review.
UNAUTHENTICATED_ALLOWLIST = {
    # Health check — intentionally public, no PII, just confirms the process
    # is alive for Railway.
    "/health",
}

WRITE_METHODS = {"POST", "PATCH", "DELETE", "PUT"}


def _load_app():
    import server
    return server.app


class TestAuthCoverage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _load_app()

    def test_every_write_route_has_an_auth_marker(self):
        missing = []
        for rule in self.app.url_map.iter_rules():
            methods = (rule.methods or set()) & WRITE_METHODS
            if not methods:
                continue
            if rule.rule in UNAUTHENTICATED_ALLOWLIST:
                continue
            view = self.app.view_functions.get(rule.endpoint)
            if view is None:
                continue
            try:
                source = inspect.getsource(view)
            except (OSError, TypeError):
                # Built-in endpoints (e.g. static) — skip.
                continue
            if not any(marker in source for marker in AUTH_MARKERS):
                missing.append(f"{sorted(methods)} {rule.rule} -> {rule.endpoint}")

        self.assertFalse(
            missing,
            msg=(
                "Routes that accept POST/PATCH/DELETE/PUT but have no auth "
                "marker in their handler source:\n  "
                + "\n  ".join(missing)
                + "\n\nApply one of these to the handler: "
                + ", ".join(AUTH_MARKERS)
                + f"\nIf intentionally public, add the path to UNAUTHENTICATED_"
                + f"ALLOWLIST in {__file__}."
            ),
        )

    def test_route_count_sanity(self):
        """Catch accidental route deletion / module-load failure.

        If someone deletes half of server.py and the app still boots, this
        fails loudly instead of silently passing the auth check with 2 routes.
        """
        total = sum(1 for _ in self.app.url_map.iter_rules())
        self.assertGreater(total, 30,
                           "Suspiciously few routes — did a blueprint fail to register?")


if __name__ == "__main__":
    unittest.main()
